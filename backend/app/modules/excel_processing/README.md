# Excel processing module

本模块拥有当前已经实现的 Excel Final 单文件处理链路，以及其三张关系化投影表。公开 HTTP 前缀保持为 `/api/v1/excel-final`，公共 Celery 任务名保持为 `app.workers.tasks_excel_final.process_excel_final`，队列保持为 `excel_final`。

## 已实现链路

1. `routes/processing.py` 复用 files 模块的 durable transfer saga，把 `.xls`/`.xlsx`/`.xlsm` 登记到 `files` 并写入配置的 Local/MinIO 存储；`.xlsx`/`.xlsm` 输入必须是单工作表，预处理负责从含人工结果的多表文件中分离原表。规范结果固定为新 `.xlsx`，不复制源宏。
2. `execution.py` 使用 jobs 模块的 attempt 状态机下载源文件、记录步骤、调用独立 Stage、登记结果文件与 `analysis_results`。
3. `stage_adapter.py` 是父进程唯一的 Stage 入口；`stage_runner.py` 在隔离子进程内导入 `Stages/excel_final`。二者使用严格的 `protocol_version=1` JSON 结果，密码只通过子进程环境传递，child traceback 不进入公共错误或日志。子进程同时生成任务临时目录中的完整内部导入工作簿和删列后的最终工作簿；数据库只读前者，对象存储和下载只使用后者。
4. `importers.py` 流式读取规范六表工作簿的“整理表”“构件表”“处理报告”，投影到 `excel_final_parts`、`excel_final_components` 和批次质量摘要；缺构件身份的 part 行跳过，重复构件 ID、负长度/数量/重量/面积及 NaN/Infinity 等非有限数值拒绝；`persistence.py` 拥有批次替换、表净重/表毛重统计和清理。
5. `routes/catalog.py` 只查询关系化投影；`routes/tools.py` 提供手册比重查询；`routes/health.py` 分项报告 Stage、依赖、手册库、业务库和对象存储状态。
6. `stage2_execution.py` 接收工作流已冻结的第一阶段正式 Excel 与分类阶段 BH DXF 清单，在任务临时目录调用 BH 读取器，再调用 Stage 二阶段深化；中间读取表和正式结果分别登记，任何项目、Job 或 attempt 血缘不一致都会拒绝继续。

## 顶层源码分工

- `access.py` 统一校验输入文件、Job 和 batch 的资源所有权；route 不重复拼授权查询。
- `availability.py` 只执行 Excel Final feature flag 门禁，不把依赖健康误当成开关状态。
- `idempotency.py` 规范并作用域化请求幂等键，防止不同 operation 误复用同一个 key。
- `models.py` 定义 `ExcelFinalBatch`、`ExcelFinalPart`、`ExcelFinalComponent` 三张关系投影表。
- `schemas.py` 定义导入统计、五金手册类别、零件类别和重量状态等稳定英文枚举；HTTP DTO 由 route/presentation 保持稳定。
- `staging.py` 只解析 file ID 并下载登记对象，不打开工作簿或识别格式。后端以 `format=auto` 记录委托事实；标准工作簿、初始表、制表符文本和固定宽度文本均由 Stage 的 Source Intake 唯一识别。
- `uploads.py` 复用 files transfer saga 保存上传对象，避免另建一套对象补偿逻辑。
- `validation.py` 在创建 Job 前读取并校验上传或冻结对象，统一映射结构化输入错误。
- `importers.py` 流式读取结果工作簿，`persistence.py` 写入/替换关系投影。
- `presentation.py` 把模型投影为 batch、part、component、process status 等稳定响应。
- `tasks.py` 只注册历史 Celery 名并调用 `execution.py`，不复制 attempt 状态机。
- `stage_adapter.py` / `stage_runner.py` 隔离父进程与 Stage；`interface.py` 是跨域唯一入口。
- `stage2_execution.py` 是工作流 Excel 第二阶段的应用层入口，只消费工作流已冻结的精确文件清单，不扫描项目目录或拆板产物。
- `handbook_catalog_source.py` 把唯一可信 `五金手册.xls` 逐行映射为可追溯的关系表、生成确定性 SQL，并提供源表与已部署手册库的逐值审计。

## 规范结果与质量语义

- Stage 只生成 `原表、清洗表、构件表、整理表、part、处理报告` 六张表；后端不再修补或重写 Stage 输出。
- 后端数据库继续从构件级 `整理表`导入零件关系，下载工作簿中的 `part`是独立的未分班组下料汇总；`part`清空普通零件构件号并跨构件合并，不覆盖数据库中的构件身份。
- 内部 `整理表` 的中文类型在入库时显式转换为稳定英文枚举，例如 `板材 -> plate`、`BOX腹 -> box_web`、`圆钢 -> round_bar`、`圆管/钢管 -> steel_pipe`；未知类型拒绝入库。下载工作簿的类型列只显示六种 BH/BOX/BT 子板类型，不影响内部完整类别入库。
- 批次零件列表的 `part_type` 查询参数只接受 `schemas.ExcelFinalPartType` 的稳定英文值；中文标签或未知值返回 HTTP 422，不静默返回空结果。
- 批次净重和毛重仅汇总 `表净重` 与 `表毛重`，拆板翼行的空表重不重复计入，合法零值保持为零。
- 尺寸、数量、面积、比重、利用率和重量统一用 `DECIMAL(24,9)` 持久化并在库内精确汇总；只在 HTTP/Job JSON 边界转换为普通数字，避免 MySQL `FLOAT` 累计漂移。
- `warning` 和 `severe_warning` 不改变 Job 的成功状态；批次、process status、AnalysisResult、步骤和 done event 都返回质量状态、计数及有界摘要。计数以最终人工处置报告的合并行口径为准；`A2=无`按零问题导入，旧15列报告仍可兼容读取。
- Job、步骤和日志只保存文件 basename、逻辑 ID、质量摘要与异常类型；临时绝对路径、MySQL 主机/DSN、口令和 traceback 不进入持久化或公共日志。
- `/weights/lookup` 必须提供英文 `category` 和 `spec`。D 系列还必须提供 `material`：HPB/Q235B/Q355B 只允许 `round_bar`，HRB 只允许 `rebar`。板材返回常量 7.85；`PIP/PD` 按 `(D-t)×t×0.02466` 返回理论米重且不查询手册；`skip` 返回空值，查无返回 `not_found`。

## 边界与依赖方向

- 其他业务模块只能导入 `interface.py`。尤其 jobs 的取消、重试恢复只能请求 Excel 域清理，不能直接操作 Excel 表。
- 本模块通过 `files.interface` 和 `jobs.interface` 使用文件与作业能力，不复制它们的模型或事务规则。
- bootstrap 可以直接装配本模块的 route、model 和 task 入口。
- `Stages/excel_final` 保持独立产品目录、算法实现和测试，本模块不复制其核心算法。

## 真实链路回归

常规后端测试默认跳过外部五金手册依赖。需要验证真实预处理输入、Stage 子进程、MySQL 五金手册、上传与任务状态机、关系化入库、结果登记及签名下载的完整链路时，在 `backend` 目录执行：

```bash
DWG_RUN_LIVE_EXCEL_FINAL=1 .venv/bin/pytest -q -s tests/excel_processing/test_excel_final_live_flow.py
```

该测试使用隔离的 SQLite 业务库和临时本地对象存储，不写入部署业务数据；五金手册查询使用当前配置的只读 MySQL。

## 范围边界

这里的基础链路仍是一个源 Excel 文件的 Excel Final 处理，不是目标架构中的完整“全部图纸就绪后最终汇总”。生产工作流已实现 BH 左右进二阶段：它使用分类账中的拆板前 BH DXF 深化当前第一阶段正式 Excel，并同步整理表与 part；它不代表跨图纸数据库屏障、CAM 自动汇总或所有截面深化已经实现。由唯一可信 `五金手册.xls` 生成并审计的 `hardware_handbook` 数据库仍是部署依赖。
