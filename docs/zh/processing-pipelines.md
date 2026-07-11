# 处理管线

> 英文对应文档：[../processing-pipelines.md](../processing-pipelines.md)

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
| DWG -> DXF | `convert_dwg_to_dxf` / `dxf` | 一个已存储 DWG | 已存储 DXF + result row | 功能开关保护；需要 ODA 和受支持 DWG header |
| DXF -> DWG | `convert_dxf_to_dwg` / `dxf2dwg` | 一个已存储 DXF | 已存储 DWG + result row | 功能开关保护；需要 ODA 和有效 DXF |
| DXF -> Excel | `extract_dxf_to_excel` / `dxf2excel` | 命名 batch 的已存储 DXF | XLSX + result row | 功能开关保护；Stage gitlink 无法从 clean clone 复现 |
| Excel Final | `process_excel_final` / `excel_final` | 内容受支持的已存储 `.xls`/`.xlsx` | 最终 XLSX + result + 关系化 batch 数据 | 功能开关保护；需要手册库和受支持 schema |
| Agent | `agent` | Agent-run 请求 | 无 | 只有 API/model 边界；task module 是占位 |
| Windows CAD | `cad` | 预留 | 无 | 配置/task/目录占位；没有部署 worker |

## DWG 转 DXF

`DXF_PIPELINE_ENABLED=true` 允许 `task_type=convert_dwg_to_dxf`。service 校验已存储文件、放入临时目录、调用 `dwg_converter` ODA adapter、把生成 DXF 存入 `dxf-derived`、创建 `AnalysisResult`，并完成匹配 attempt。

步骤为 `download_source_dwg`、`run_oda_convert` 和 `persist_dxf_result`。ODA timeout/retry 设置限制子进程，但兼容性仍取决于真实源版本和 ODA 行为。已跟踪 AppImage 和单元测试不能证明支持所有 DWG，也不代表拥有许可权利。

## DXF 转 DWG

`DXF2DWG_PIPELINE_ENABLED=true` 允许 `task_type=convert_dxf_to_dwg`。流程与 DWG -> DXF 对称，步骤为 `download_source_dxf`、`run_oda_convert_dxf` 和 `persist_dwg_result`。它把派生对象存入 DWG-derived bucket 并创建 result row。

该管线可消费上传的 DXF 或此前可访问的转换结果。结果选择是确定性的：先选最新成功 Job，再选最新成功 result row，不会静默选择任意历史输出。

## DXF 转 Excel

`DXF2EXCEL_PIPELINE_ENABLED=true` 允许 `task_type=extract_dxf_to_excel`。service 收集请求 `batch_name` 下可访问的 DXF，暂存可读对象，调用 `dxf2excel` 包，存储一个工作簿，并在 Job step 中记录部分下载 warning。

步骤为 `download_dxf_batch`、`run_dxf2excel_pipeline` 和 `persist_excel_result`。batch name 不是授权范围：列表、metadata、删除和下载必须通过相同 SQL 访问边界过滤每个文件。

当前父仓库只把 `Stages/dxf2excel` 记录为 gitlink commit `86e99dce5ebce992273c7df78ca13d58036f7472`，没有 `.gitmodules`，本地也缺少该对象。已填充工作目录使当前 checkout 可工作，但 clean clone 和 image build 不能依赖它。在管线被视为可复现交付前必须修复。

## Excel Final

`EXCEL_FINAL_PIPELINE_ENABLED=true` 启用专用 upload/process 端点及 `task_type=process_excel_final`。支持输入为：

- Tekla tab/whitespace-delimited 导出，尽管是文本也可能使用 `.xls` 文件名；
- 含必要初始表 signature 的真实 `.xlsx`/`.xlsm` 工作簿；
- `xlrd` 可解析且包含必要业务列的 legacy 二进制 `.xls` 工作簿。

只有正确扩展名的普通表格是合理的负例。探测会尝试文本与工作簿路径，不把一次文本解码失败当成输入无效的最终证据。

步骤为 `download_excel_source`、`run_excel_final_pipeline`、`import_parts_to_db` 和 `persist_excel_final_result`。backend 以有界 timeout 启动独立 Stage 子进程，密码通过环境而非命令行传递。成功时存储最终工作簿，并导入一个 `excel_final_batches` row 以及 component/part rows。失败只在同一 attempt 仍被当前执行拥有时清理临时 batch row。

## 结果与下载解析

管线输出由一个 `files` row 和 `analysis_results.result_file_id` 表示。结果详情、下载 URL 和复核检查委托给父 Job 边界。无项目 Job 仅管理员或创建者可读。

浏览器单文件下载先获得 300 秒签名 path，再执行认证 fetch。遇到网络错误、403、408、429 或 5xx 时等待 500 ms，并用新签名进行第二次且仅一次尝试。ZIP 端点流式返回 POST 响应，不使用相同重签名循环。

## 取消、重试与恢复

- 取消只改变匹配的 active Job；worker 在取消后的写入被条件更新拒绝。
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
