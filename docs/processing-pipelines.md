# 处理管线

## 共享 Job 契约

每条异步管线都使用同一控制路径：

```text
已认证请求
  -> 校验功能开关、输入和资源权限
  -> 创建 Job(status=queued, attempt=N) 并 commit
  -> 向路由到的 MySQL 队列发布 (job_id, attempt)
  -> worker 原子领取 queued + expected attempt
  -> 写入 attempt-scoped JobSteps 和进度快照
  -> 读取源对象 -> 执行 Stage -> 写结果对象
  -> 持久化 file/result/domain metadata
  -> 条件完成同一 attempt
```

API 在 Job 持久化和投递后返回 HTTP 202。投递失败只条件标记仍 queued 的同一 attempt。worker 不得创建第二个正确性存储，也不得在不匹配 attempt 的情况下更新 Job。

## 能力矩阵

| 管线 | Task / queue | 输入 | 输出 | 当前边界 |
|---|---|---|---|---|
| Framework smoke | `local_stub` / `report` | Job 参数 | JSON `AnalysisResult` | 已实现框架路径，不是 LLM report Agent |
| DWG -> DXF | `convert_dwg_to_dxf` / `dxf` | 一个或最多 200 个已存储 DWG | 每文件一个已存储 DXF + result row | 功能开关保护；需要 ODA 和受支持 DWG header |
| DXF -> DWG | `convert_dxf_to_dwg` / `dxf2dwg` | 一个或最多 200 个已存储 DXF | 每文件一个已存储 DWG + result row | 功能开关保护；需要 ODA 和有效 DXF |
| DXF -> Excel | `extract_dxf_to_excel` / `dxf2excel` | 命名 batch 的已存储 DXF | XLSX + result row | 功能开关保护；Stage 源码已跟踪，外部 corpus 不随仓库分发 |
| Excel Final | `process_excel_final` / `excel_final` | 内容受支持的已存储 `.xls`/`.xlsx` | 最终 XLSX + result + 关系化 batch 数据 | 功能开关保护；需要手册库和受支持 schema |
| Agent | `agent` | Agent-run 请求 | 无 | 只有 API/model 边界；task module 是占位 |
| Windows CAD | `cad` | 预留 | 无 | 配置/task/目录占位；没有部署 worker |

这些管线是 Job 执行层。`linux_production` 工作流的 `excel_stage1` 和 `excel_final` 公开执行端点会创建或复用对应 Job、绑定 attempt，并在详情同步时推进阶段和挂接结果；其他转换页仍可独立使用。图纸/CAM/Windows 留白阶段不创建伪 Job。详见[Linux 生产工作流](workflow-framework.md)。

## DWG 转 DXF

`DXF_PIPELINE_ENABLED=true` 允许 `task_type=convert_dwg_to_dxf`。service 校验已存储文件、放入临时目录、调用 `dwg_converter` ODA adapter、把生成 DXF 存入 `dxf-derived`、创建 `AnalysisResult`，并完成匹配 attempt。

步骤为 `download_source_dwg`、`run_oda_convert` 和 `persist_dxf_result`。ODA timeout/retry 设置限制子进程，但兼容性仍取决于真实源版本和 ODA 行为。已跟踪 AppImage 和单元测试不能证明支持所有 DWG，也不代表拥有许可权利。

当前 ODA adapter 会把“非零退出码”“退出码为 0 但未生成目标文件”“输出路径被普通文件占用”“源文件复制失败”和“二进制不存在/无权限”映射为明确失败结果，避免未捕获 OS 错误绕过 Job 收敛。该加固已有 Stage regression test，但仍需真实 ODA/图纸样本验证。

### 双向批处理与实时进度

`POST /api/v1/jobs/batches` 一次接收 1-200 个同方向 `file_id`。服务端先验证全部文件、扩展名和权限，再一次提交每文件 Job，并只向对应队列投递一条批任务；验证失败不会留下部分 Job。单文件 `/jobs` 入口仍兼容保留。

worker 领取每个 Job 后按目标 AutoCAD 版本分组。小组一次调用 ODA；大组按 `CAD_BATCH_MIN_FILES_PER_SHARD` 自适应拆分，最多并行 `CAD_BATCH_MAX_SHARDS` 个目录调用。每个文件仍有独立 attempt、步骤、进度、结果和错误；某个输出缺失只失败对应 Job。DWG -> DXF 与 DXF -> DWG 使用相同的分组、分片和持久 Xvfb 契约。

前端文件夹、ZIP 和“提交/重试”走批量入口；单个上传也使用同一批量合同。超过 200 个文件时前端分块，每轮最多并发 3 个请求，分别保留已提交和待补交 ID，不因一个块失败隐藏其他已创建 Job。`GET /api/v1/jobs/events/stream?task_type=...&file_ids=...` 每个连接最多观察 200 个文件，500 ms 读取 MySQL 短事务快照，任一 Job 变化即推送，全部终态后关闭。页面按 200 个文件分片连接，显示当前文件夹或全部范围的成功进度、成功、失败、处理中和待补交数量；失败和取消任务的历史进度按 0 计入汇总。10 秒轮询只作为断线修复。`POST /api/v1/jobs/cancellation-requests` 先验证全部 Job 权限，再只取消当前转换范围的 active Job，不再调用全局取消影响其他流水线。

### 文件夹删除契约

`POST /api/v1/files/batches/bulk-delete` 对 1-100 个文件夹执行单事务软删除。它按 `batch_name` 同时纳入源文件和已登记生成结果，取消关联双向 CAD 活动 Job，并复用文件软删除、预览失效、流转账本和审计路径。任一名称不存在、任一文件无权或任一写入失败时整批回滚。详细请求、响应、错误码和响应丢失边界见 [API 参考](api.md#多文件夹原子软删除)。

### DXF 在线预览

DXF 源文件和成功转换得到的 DXF 可在前端打开鉴权 SVG 预览。API 在读取对象前检查声明大小，读取时再次执行有界校验和 SHA-256 核对；渲染器使用 ezdxf SVG recording backend，不启用外部图像，并限制源文件为 20 MiB、文档实体 100,000、SVG 输出 16 MiB。生成结果写入报告 bucket 并登记到 `files`/`file_transfers`，后续命中必须通过对象 `stat`，浏览器通过带 Bearer 的 Blob 请求读取专用内容端点。

## DXF 转 DWG

`DXF2DWG_PIPELINE_ENABLED=true` 允许 `task_type=convert_dxf_to_dwg`。流程与 DWG -> DXF 对称，步骤为 `download_source_dxf`、`run_oda_convert_dxf` 和 `persist_dwg_result`。它把派生对象存入 DWG-derived bucket 并创建 result row。

该管线可消费上传的 DXF 或此前可访问的转换结果。结果选择是确定性的：先选最新成功 Job，再选最新成功 result row，不会静默选择任意历史输出。

## DXF 转 Excel

`DXF2EXCEL_PIPELINE_ENABLED=true` 允许 `task_type=extract_dxf_to_excel`。service 收集请求 `batch_name` 下可访问的 DXF，暂存可读对象，调用 `dxf2excel` 包，存储一个工作簿，并在 Job step 中记录部分下载 warning。

步骤为 `download_dxf_batch`、`run_dxf2excel_pipeline` 和 `persist_excel_result`。batch name 不是授权范围：列表、metadata、删除和下载必须通过相同 SQL 访问边界过滤每个文件。

`Stages/dxf2excel` 的源码、锁文件和内置单测已由父仓库直接跟踪，backend editable dependency 与 image build context 可以从源码检出恢复。Stage README 记录的 419 文件逐格历史验证依赖外部 corpus；当前仓库只包含最小解码单测，因此发布验收必须单独提供许可合规且摘要固定的 corpus。

独立 DXF→Excel 页面的“生成零件清单”桥接仍保留：从 extraction result 取得 `result_file_id`，以 `dxf2excel-{extraction_job_id}-{result_file_id}` 调用 Excel Final。生产流程页面则由 workflow executions 以工作流/阶段幂等键创建同类 Job，并自动绑定 result artifact；两条入口都复用已登记对象，不重新上传字节。

## Excel Final

`EXCEL_FINAL_PIPELINE_ENABLED=true` 启用专用 upload/process 端点及 `task_type=process_excel_final`。支持输入为：

- Tekla tab/whitespace-delimited 导出，尽管是文本也可能使用 `.xls` 文件名；
- 含必要初始表 signature 的真实 `.xlsx`/`.xlsm` 工作簿；
- `xlrd` 可解析且包含必要业务列的 legacy 二进制 `.xls` 工作簿。

只有正确扩展名的普通表格是合理的负例。探测会尝试文本与工作簿路径，不把一次文本解码失败当成输入无效的最终证据。

步骤为 `download_excel_source`、`run_excel_final_pipeline`、`import_parts_to_db` 和 `persist_excel_final_result`。backend 以有界 timeout 启动独立 Stage 子进程，密码通过环境而非命令行传递。成功时存储最终工作簿，并导入一个 `excel_final_batches` row 以及 component/part rows。失败只在同一 attempt 仍被当前执行拥有时清理临时 batch row。

关系化导入使用只读 `iter_rows(values_only=True)` 遍历输出表。零件表通过规范化表头定位列并跳过空行/合计行；构件表必须存在 `构件编号` 列，`构件数` 和重量列可选，数值 0 不会再被误写成 NULL。输入字节保持不变；输出工作簿中的“原表”是去除半角/全角空格后的处理基线，并非原始对象的逐字节副本。

Excel Final 前端总览由 `/overview` 在 SQL 中同时按 `task_type=process_excel_final` 和当前用户可读 Job 聚合，不使用当前批次页冒充全局统计。批次、零件、构件和跨批次搜索均使用相同业务域/权限域及服务端分页；结果工作簿经现有鉴权预览/下载接口读取，批次页不会逐行轮询 Job 状态。页面把任务、批次分页、批次抽屉和已应用搜索写入 URL，刷新/分享/浏览器历史可恢复监视上下文；健康条按实际数据库与 Local/MinIO 后端显示，不能把开发 SQLite/local 固定写成 MySQL/MinIO。

## 结果与下载解析

管线输出由一个 `files` row 和 `analysis_results.result_file_id` 表示。结果详情、下载 URL 和复核检查委托给父 Job 边界。无项目 Job 仅管理员或创建者可读。

浏览器单文件下载先获得 300 秒签名 path，再执行认证 fetch。遇到网络错误、403、408、429 或 5xx 时等待 500 ms，并用新签名进行第二次且仅一次尝试。ZIP 端点流式返回 POST 响应，不使用相同重签名循环。

## 取消、重试与恢复

- 批量取消先完成全部权限检查，再只改变请求中匹配的 active Job；worker 在取消后的写入被条件更新拒绝。
- failed/cancelled 状态允许重试，递增 attempt、重置终态字段并发布 `(job_id, new_attempt)`。
- 旧单参数消息映射为 attempt 1，不能领取 attempt 2。
- worker 启动把足够 stale 的 running Job 标为 `CELERY_WORKER_LOST`；操作员必须检查依赖后再重试。
- Celery result row 在 24 小时后过期，但 Job/JobStep 业务历史会保留在 MySQL，直到实现显式保留策略。

## 启用检查表

1. 修复或验证 Stage 源码归属与锁定依赖。
2. 用有代表性的有效/无效样本运行 Stage 单元测试。
3. 验证 MySQL 迁移、存储写/读/删和必要手册库授权。
4. 为队列启动且仅启动预期的一个 worker node，并验证 readiness。
5. 只启用对应功能开关。
6. 通过 Nginx 提交，观察 Job step/SSE，下载结果并比较 SHA-256。
7. 覆盖取消、重试、依赖中断、重启和未授权访问。

不要因为 `AGENT_ENABLED` 或 `CAD_WORKER_ENABLED` 的 API/configuration symbol 存在就启用它们。
