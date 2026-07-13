# CAD 双向转换吞吐与实时进度设计

## 1. 背景与目标

平台当前把每个 DWG→DXF 或 DXF→DWG 文件建成独立 Job，并在 Celery 子进程内为每个 Job 执行一次 `xvfb-run -a ODAFileConverter.AppImage`。这保留了逐文件状态、重试和结果追踪，但把 Xvfb、AppImage 和 ODA 的启动成本重复到了每个文件上，并且 `xvfb-run -a` 的显示号探测不是原子操作，并发启动时会发生显示号竞态。

本次改造目标是：

1. 两个转换方向都能在真实文件上稳定提高吞吐，不能用随机失败换取更短墙钟时间。
2. 单文件与批量路径共享同一组状态机、权限、存储和结果不变量。
3. 文件夹、ZIP 和恢复任务走高吞吐批量路径；单文件上传和单任务重试继续可用。
4. 前端及时显示上传、排队、转换、成功、失败和取消状态，并且统计范围与用户当前选择一致。
5. 本地脚本、Compose、后端配置和前端协议使用同一组并发与进度语义。

不改变 DWG/DXF 几何语义，不关闭 ODA audit，不引入 Redis，也不改变 MySQL 作为 Job 权威事实源的架构。

## 2. 实测基线

样本来自：

`/home/Creeken/Paper/CAD_research/Data/十份排版/排版1/C区域四节钢柱（宝冶）/2.零件图/1：1零件图/`

16 个小型 DWG 的直接 Stage 实测结果：

| 方向 | 执行方式 | 墙钟时间 | 成功率 |
|---|---:|---:|---:|
| DWG→DXF | 单文件串行，每次 `xvfb-run` | 60.31 秒 | 16/16 |
| DWG→DXF | 原方式并发 2 | 30.37 秒 | 13/16 |
| DWG→DXF | 原方式并发 4 | 15.31 秒 | 11/16 |
| DWG→DXF | 原方式并发 8 | 8.19 秒 | 8/16 |
| DWG→DXF | 持久 Xvfb，并发 8 | 1.94 秒 | 16/16 |
| DWG→DXF | 持久 Xvfb，ODA 目录批处理 | 约 0.85 秒 | 16/16 |
| DXF→DWG | 持久 Xvfb，并发 8 | 1.87 秒 | 16/16 |
| DXF→DWG | 持久 Xvfb，ODA 目录批处理 | 约 0.85 秒 | 16/16 |

32 文件扩展测试中，DWG→DXF 从并发 8 开始收益明显变小；DXF→DWG 到并发 16 仍有收益。考虑两条队列可能同时运行以及本机有 24 个逻辑 CPU，默认每方向并发 8，允许通过环境变量调整。

## 3. 方案选择

### 3.1 未采用：只提高 Celery 并发

只把并发从 2 提到 8 会放大 `xvfb-run -a` 竞态。实测成功率从 16/16 降到 8/16，因此不满足正确性要求。

### 3.2 未采用：所有任务只保留一个目录级 Job

纯目录 Job 虽然最快，但会丢失逐文件权限、取消、重试、版本选择、结果下载和失败隔离。DXF→DWG 还必须按目标 DWG 版本分组，不能把不同版本无条件放入同一 ODA 调用。

### 3.3 采用：持久显示服务、可配置单文件并发与版本分组批处理

单文件任务共享队列级持久 Xvfb；文件夹、ZIP 和批量恢复一次创建多个逐文件 Job，再由一个批处理 Celery 消息领取这些 Job、按输出版本分组，并对每组执行一次 ODA 目录转换。每个文件仍有独立 Job、JobStep、AnalysisResult 和 StoredFile。

## 4. 后端架构

### 4.1 Xvfb 生命周期

新增统一 Worker 启动包装器：

1. 为 `dxf` 和 `dxf2dwg` 队列分配不同 DISPLAY。
2. 启动一个 Xvfb，等待 Unix socket 就绪。
3. 导出 DISPLAY 后启动 Celery worker。
4. Celery 退出时清理对应 Xvfb。

两个 Stage 的 `OdaConverter` 在 DISPLAY 已配置时直接执行 ODA；只有独立 CLI 使用且没有 DISPLAY 时才回退到 `xvfb-run -a`。显式传入 `xvfb_run=True/False` 的行为保持不变。

### 4.2 并发配置

新增：

- `DXF_WORKER_CONCURRENCY`，默认 8；
- `DXF2DWG_WORKER_CONCURRENCY`，默认 8；
- `DXF_WORKER_DISPLAY`，本地默认 `:91`；
- `DXF2DWG_WORKER_DISPLAY`，本地默认 `:92`。

本地启动脚本、Compose worker command、环境示例和配置文档都从这些键取值。Celery 继续使用 `worker_prefetch_multiplier=1`，防止单 worker 提前占有过多长任务。

### 4.3 批量 Job API

新增 `POST /api/v1/jobs/batches`，请求包含：

```json
{
  "task_type": "convert_dwg_to_dxf",
  "file_ids": [1, 2, 3],
  "precision_level": "normal"
}
```

约束：

- 只接受 DWG→DXF 或 DXF→DWG；
- 每批 1–200 个文件，去重后保持输入顺序；
- 校验文件存在、未删除、扩展名与方向匹配；
- 复用单 Job 的管线开关和访问控制；
- 在同一事务中创建所有逐文件 Job 与审计记录；
- commit 后只发送一个批处理 Celery 消息；
- 响应返回创建的 Job 列表。

单文件 `POST /jobs` 保持兼容。

### 4.4 批处理执行

两个方向分别提供批处理 task，但共享批处理编排约定：

1. 按 `(job_id, attempt)` 原子领取所有仍为 queued 的 Job。
2. 将每个 Job 推进到 10%，记录领取状态。
3. 定位本地对象或流式下载到批次临时目录，推进到 30%。
4. DWG→DXF 按 DWG 头部解析出的 ODA 版本分组；DXF→DWG 按 AnalysisResult 反查、`$ACADVER` 或配置默认值解析出的版本分组。
5. 每个版本组调用一次 `convert_directory`。
6. 对每个文件分别读取实际产物或 `.err` 结果，推进到 70%。
7. 分文件保存派生对象、AnalysisResult 和完成步骤，推进到 90% 后以 attempt guard 完成到 100%。
8. 一个文件失败只失败该 Job；其他 Job 继续持久化。
9. Job 在转换期间被取消或 attempt 已变化时，不写入旧产物登记。

批处理消息异常退出时，仍由现有陈旧 running Job 恢复机制处理；任务不得把 traceback、宿主路径、子进程 stderr 或凭据返回给客户端。

### 4.5 批量取消

新增 `POST /api/v1/jobs/cancellation-requests`，请求为 Job ID 列表。逐项做写权限校验，只转换当前 attempt 的可取消状态。前端不再使用跨业务的 `cancel-all-active`。

## 5. 进度协议与前端

### 5.1 聚合 SSE

新增 `GET /api/v1/jobs/events/stream?task_type=...&file_ids=...`：

- 一条连接查询最多 200 个 Job；
- 初始帧返回完整快照；
- 后续只在状态、attempt、progress 或错误变化时发帧；
- 每次轮询使用短事务；
- 所有 Job 终态后关闭；
- 继续使用 jobs 路径的 HttpOnly SSE cookie；
- 默认 0.5–1 秒轮询，空轮次发送 keepalive。

前端 Hook 按转换页实际 query key 更新缓存，不再写入无关的 `['jobs']` 键。

### 5.2 页面状态

两个方向继续共用 `ConversionPage`。页面分别维护：

- 上传总数、已上传数、上传失败数；
- 待提交、排队、转换中；
- 成功、失败、取消；
- 终态数与总体任务数计算的批次完成率；
- 单文件真实 Job progress。

总进度统计当前批次或当前筛选范围，而不是只统计当前分页。失败和取消计入“已处理”，但不能计入“成功”。页面明确显示统计范围。

文件夹上传使用受控上传并发，上传完成后一次调用批量 Job API。ZIP 上传已由后端返回 StoredFile 列表，直接一次批量提交。恢复任务也一次批量提交。上传进度与转换进度分别展示，避免上传尚未完成时显示虚假的转换百分比。

### 5.3 暂停与恢复

“全部暂停”改为“暂停当前范围”，只取消当前方向、当前批次或筛选范围内的 active Job。“继续任务”只为无 Job、失败、取消或明确陈旧的当前范围文件创建新任务。

## 6. 错误处理与兼容性

- 单文件 API、单任务重试、结果下载和 DXF 预览保持兼容。
- ODA returncode、stdout、stderr 只写受控日志；客户端仍使用稳定错误码。
- 批量请求部分输入非法时整体 422，不创建半批 Job。
- 批量执行中的单文件转换失败不回滚其他已完成文件。
- 存储写入失败继续使用现有补偿规则。
- 根 `.env` 与 `backend/.env` 的运行开关需一致；状态脚本检测本地与 Compose 同时消费相同队列并给出明确告警。

## 7. 验证标准

完成必须同时满足：

1. 两个 Stage 的单元测试通过，新增 DISPLAY 选择和批量结果映射覆盖。
2. 后端批量创建、权限、取消、版本分组、部分失败、attempt guard 和聚合 SSE 测试通过。
3. 前端 TypeScript 构建和转换页 Playwright 契约通过。
4. 指定真实目录的 DWG→DXF 全量输出数量与输入一致，零转换失败。
5. 生成的 DXF 全量反向转 DWG，输出数量一致，零转换失败。
6. 真实批量墙钟时间显著低于现有逐文件 `xvfb-run` 基线，并记录吞吐、并发和样本数。
7. 单文件、文件夹、ZIP、暂停、恢复、失败和下载进度在前后端一致。
8. 仓库文档、ruff、pytest、Alembic、Compose 配置和前端构建门禁通过。
