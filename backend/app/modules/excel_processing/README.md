# Excel processing module

本模块拥有当前已经实现的 Excel Final 单文件处理链路，以及其三张关系化投影表。公开 HTTP 前缀保持为 `/api/v1/excel-final`，公共 Celery 任务名保持为 `app.workers.tasks_excel_final.process_excel_final`，队列保持为 `excel_final`。

## 已实现链路

1. `routes/processing.py` 复用 files 模块的 durable transfer saga，把 `.xls`/`.xlsx` 登记到 `files` 并写入配置的 Local/MinIO 存储。
2. `execution.py` 使用 jobs 模块的 attempt 状态机下载源文件、记录步骤、调用独立 Stage、登记结果文件与 `analysis_results`。
3. `stage_adapter.py` 是父进程唯一的 Stage 入口；`stage_runner.py` 在隔离子进程内导入 `Stages/excel_final`。密码只通过子进程环境传递。
4. `importers.py` 把输出工作簿的“整理表”和“构件表”投影到 `excel_final_parts` 与 `excel_final_components`；`persistence.py` 拥有批次替换、统计和清理。
5. `routes/catalog.py` 只查询关系化投影；`routes/tools.py` 提供手册比重查询；`routes/health.py` 分项报告 Stage、依赖、手册库、业务库和对象存储状态。

## 顶层源码分工

- `access.py` 统一校验输入文件、Job 和 batch 的资源所有权；route 不重复拼授权查询。
- `availability.py` 只执行 Excel Final feature flag 门禁，不把依赖健康误当成开关状态。
- `idempotency.py` 规范并作用域化请求幂等键，防止不同 operation 误复用同一个 key。
- `models.py` 定义 `ExcelFinalBatch`、`ExcelFinalPart`、`ExcelFinalComponent` 三张关系投影表。
- `schemas.py` 定义导入过程的 typed statistics；HTTP DTO 由 route/presentation 保持稳定。
- `staging.py` 解析 file ID、下载登记对象并识别源格式；不直接写业务终态。
- `uploads.py` 复用 files transfer saga 保存上传对象，避免另建一套对象补偿逻辑。
- `importers.py` 流式读取结果工作簿，`persistence.py` 写入/替换关系投影。
- `presentation.py` 把模型投影为 batch、part、component、process status 等稳定响应。
- `tasks.py` 只注册历史 Celery 名并调用 `execution.py`，不复制 attempt 状态机。
- `stage_adapter.py` / `stage_runner.py` 隔离父进程与 Stage；`interface.py` 是跨域唯一入口。

## 边界与依赖方向

- 其他业务模块只能导入 `interface.py`。尤其 jobs 的取消、重试恢复只能请求 Excel 域清理，不能直接操作 Excel 表。
- 本模块通过 `files.interface` 和 `jobs.interface` 使用文件与作业能力，不复制它们的模型或事务规则。
- bootstrap 可以直接装配本模块的 route、model 和 task 入口。
- `Stages/excel_final` 保持独立产品目录、算法实现和测试，本模块不复制其核心算法。

## 当前真实缺口

这里实现的是一个源 Excel 文件的 Excel Final 处理，不是目标架构中的完整“全部图纸就绪后最终汇总”。跨图纸数据库屏障、左右进结果合并、自动汇总触发和生产输入 schema 的最终验收仍是待实现能力；外部 `hardware_handbook` 数据库也是部署依赖。代码移动和健康检查不能被解释为这些能力已经完成。
