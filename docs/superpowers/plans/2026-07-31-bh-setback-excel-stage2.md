# BH 左右进 Excel 第二阶段（方案 A）实施计划

> 执行约束：在主仓库当前工作区由主代理顺序实施，不派子代理；每个任务先写失败测试，再写最小实现，再跑相关回归。除本计划明确列出的主仓库文件外，不修改 gg 部署和现有生产数据。

**目标：** 将 BH 左右进读取器 1.2.7 接入 `linux_production` 的 Excel 第二阶段，按项目和 Job attempt 隔离读取拆板前 BH DXF，重建整理表与 part，并提供真实进度和两个单文件 Excel 下载。

**架构：** 后端从当前工作流的冻结输入、第一阶段正式 Artifact 和分类账本解析全部 ID，创建紧凑清单摘要 Job。专用 worker 逐张下载、校验和读取 BH DXF，产生左右进审计表及紧凑测量 JSON；独立 Excel Stage 子进程重新建立第一阶段规范模型、核对基线、注入左右进并用公共 writer 重建六 sheet。成功后原子登记左右进 Excel、第二阶段 Excel、数据库投影和当前 attempt Artifact。

**技术栈：** Python 3.12、FastAPI、SQLAlchemy/MySQL、Celery SQL broker、MinIO、openpyxl/OOXML、React/TypeScript、TanStack Query、Ant Design、Playwright。

**设计依据：** [BH 左右进 Excel 第二阶段设计规格](../specs/2026-07-31-bh-setback-excel-stage2-design.md)

---

## Task 1：冻结第一阶段输出合同和重建接缝

**文件：**

- 修改：`Stages/excel_final/canonical_pipeline.py`
- 修改：`Stages/excel_final/writer_parts.py`
- 修改：`Stages/excel_final/tests/test_writer_workbook.py`
- 新增：`Stages/excel_final/tests/test_canonical_projection.py`
- 回归：`Stages/excel_final/tests/test_ground_truth_regression.py`

- [ ] 1.1 写失败测试：`build_canonical_projection()` 对同一 SourcePart 集合返回清洗行、构件行、整理行、PartCandidate 和 QualityIssue，但不写文件。
- [ ] 1.2 写失败测试：原 `process_canonical_records()` 经新接缝输出的六 sheet、可见列、公式、公式缓存、报告、列宽和 `part` J/K/L 合同不变。
- [ ] 1.3 在 `canonical_pipeline.py` 引入不可变 `CanonicalProjection`：

```python
@dataclass(frozen=True, slots=True)
class CanonicalProjection:
    cleaned_parts: tuple[SourcePart, ...]
    component_rows: tuple[ComponentSourceRow, ...]
    organized_rows: tuple[Mapping[str, object], ...]
    part_candidates: tuple[PartCandidate, ...]
    issues: tuple[QualityIssue, ...]
```

- [ ] 1.4 把现有循环提取为 `build_canonical_projection()`；把 `build_part_rows()`、最终问题标记和 writer 调用提取为 `write_canonical_projection()`。
- [ ] 1.5 让 `process_canonical_records()` 只做“build → write”兼容封装，默认行为完全不变。
- [ ] 1.6 运行：

```bash
cd /home/Creeken/Paper/CAD_research/complete_framework/Stages/excel_final
../../backend/.venv/bin/pytest -q tests/test_canonical_projection.py tests/test_writer_workbook.py tests/test_ground_truth_regression.py
```

预期：全部通过；第一阶段真实 ground truth 行数和公式不变。

- [ ] 1.7 提交：`refactor(excel): expose canonical projection before workbook write`

## Task 2：让公共 writer 明确支持模型长度和下料长度两种公式策略

**文件：**

- 修改：`Stages/excel_final/writer_parts.py`
- 修改：`Stages/excel_final/tests/test_writer_workbook.py`
- 修改：`Stages/excel_final/tests/test_ooxml_formula.py`

- [ ] 2.1 写失败测试：默认 `MODEL_LENGTH` 仍生成第一阶段公式 `U=M*T`、`W/X` 使用 M。
- [ ] 2.2 写失败测试：`CUT_LENGTH` 生成 `U=P*T`、`W/X` 使用 P，公式缓存等于下料长度计算值。
- [ ] 2.3 写失败测试：PIP/PD 密度公式不因长度策略改变；无左右进的非 BH 行数值与第一阶段一致。
- [ ] 2.4 新增：

```python
class FormulaLengthBasis(StrEnum):
    MODEL_LENGTH = "model_length"
    CUT_LENGTH = "cut_length"
```

- [ ] 2.5 将公式基准显式传给 `_theory_basis_formula()`、`_apply_organized_formulas()` 和 `write_canonical_workbook()`；默认值必须是 `MODEL_LENGTH`。
- [ ] 2.6 保存后同时用 `data_only=False` 和 `data_only=True` 回读所有新增公式坐标，禁止只有公式没有缓存。
- [ ] 2.7 运行：

```bash
cd /home/Creeken/Paper/CAD_research/complete_framework/Stages/excel_final
../../backend/.venv/bin/pytest -q tests/test_writer_workbook.py tests/test_ooxml_formula.py
```

- [ ] 2.8 提交：`feat(excel): support cut-length formula policy with caches`

## Task 3：把 Reader 1.2.7 受控纳入主仓库

**文件：**

- 新增：`Stages/bh_left_right_reader/pyproject.toml`
- 新增：`Stages/bh_left_right_reader/src/bh_reader/**`
- 新增：`Stages/bh_left_right_reader/config/default.toml`
- 新增：`Stages/bh_left_right_reader/tests/**`
- 修改：`backend/pyproject.toml`
- 修改：`backend/uv.lock`
- 修改：`backend/Dockerfile`
- 修改：`compose.dev.yaml`
- 修改：`backend/tests/infrastructure/test_compose.py`

- [ ] 3.1 复制正式 1.2.7 的源包、默认配置和测试；不复制 `.git`、`.venv`、旧输出、图片和临时日志。
- [ ] 3.2 校验复制前后核心文件 SHA-256，保存为测试中的固定 manifest；确认版本仍为 `1.2.7`。
- [ ] 3.3 将 `bh-left-right-reader==1.2.7` 加入 backend path dependency 和 uv lock。
- [ ] 3.4 Docker builder 显式 `COPY Stages/bh_left_right_reader`；development 和 protected runtime 都能导入 `bh_reader`，protected 层只留下 pyc。
- [ ] 3.5 Compose development worker 挂载新 Stage；保护镜像合同测试断言 Reader pyc 存在、Reader `.py` 不存在。
- [ ] 3.6 运行 Reader 原始门：

```bash
cd /home/Creeken/Paper/CAD_research/complete_framework/Stages/bh_left_right_reader
uv run python -m unittest discover -s tests -v
uv run bh-reader --version
```

预期：80/80，通过；版本为 1.2.7。

- [ ] 3.7 运行 backend lock 和 Compose 合同：

```bash
cd /home/Creeken/Paper/CAD_research/complete_framework/backend
uv sync --frozen
uv run pytest -q tests/infrastructure/test_compose.py tests/infrastructure/test_server_release.py
```

- [ ] 3.8 提交：`build(stage2): vendor verified BH setback reader 1.2.7`

## Task 4：给 Reader 增加有界内存批服务和真实进度

**文件：**

- 新增：`Stages/bh_left_right_reader/src/bh_reader/batch.py`
- 修改：`Stages/bh_left_right_reader/src/bh_reader/cli.py`
- 修改：`Stages/bh_left_right_reader/src/bh_reader/simple_xlsx.py`
- 新增：`Stages/bh_left_right_reader/tests/test_batch.py`
- 修改：`Stages/bh_left_right_reader/tests/test_cli.py`

- [ ] 4.1 写失败测试：批服务按输入顺序回调 `processed/total/file_name/status`，每张恰好一次。
- [ ] 4.2 写失败测试：`--no-visuals` 不保留 `DrawingData` 列表；处理完一张即可释放几何对象。
- [ ] 4.3 写失败测试：5000 个轻量有效输入时，结果 Excel 行数、进度回调数和角色记录数正确；不生成完整 JSON 时不会积累诊断 JSON 文本。
- [ ] 4.4 写失败测试：单图异常变成结构化失败结果，后续图仍可完成；批 outcome 明确列出失败数和文件名。
- [ ] 4.5 实现 `BhInputEntry`、`BhProgress`、`BhBatchOutcome` 和 `analyze_manifest()`，CLI 改为调用同一服务。
- [ ] 4.6 `write_results_xlsx()` 接受 iterable 或紧凑行集合；正式 backend 路径不创建图片和完整 JSON。
- [ ] 4.7 复测 199 张真实图：

```bash
cd /home/Creeken/Paper/CAD_research/complete_framework/Stages/bh_left_right_reader
TIMEFORMAT='elapsed=%3R user=%3U system=%3S'
time uv run bh-reader --backend ascii --no-visuals \
  -o /tmp/bh-stage2-reader.xlsx --json /tmp/bh-stage2-reader.json \
  '/home/Creeken/Paper/CAD_research/所有的dxf/BH拆板前后数据/BH_拆板前_dxf' \
  '/home/Creeken/Paper/CAD_research/所有的dxf/项目1/项目1_BH_dxf' \
  '/home/Creeken/Paper/CAD_research/所有的dxf/集散中心框架3~5层1批加工图中铁建区域（安徽齐顺）/分类1/BH'
```

预期：199/199 OK、420 条板件记录；与 1.2.7 正式 JSON 的交付字段逐项一致。

- [ ] 4.8 提交：`feat(reader): add bounded batch API and progress callbacks`

## Task 5：实现 BH 深化领域规则

**文件：**

- 新增：`Stages/excel_final/bh_stage2.py`
- 新增：`Stages/excel_final/tests/test_bh_stage2.py`
- 修改：`Stages/excel_final/quality.py`

- [ ] 5.1 写表驱动测试覆盖 `腹`、`翼`、`翼-1`、`上翼`、`下翼`、`上翼-N`、`下翼-N` 的类型、导入零件号、数量倍率和稳定顺序。
- [ ] 5.2 写失败测试：一个图纸结果可扇出到多个构件出现，各自使用自己的构件数、长度和原数量。
- [ ] 5.3 写失败测试：同名但分类规格、Reader 规格或 Excel 重建规格不同必须失败。
- [ ] 5.4 写失败测试：重复腹板、缺腹板、上下翼组合不完整、负左右进、非有限值、下料长度 `<=0` 全部失败关闭。
- [ ] 5.5 写失败测试：源净毛重和面积只留在腹板；新翼板行为空。
- [ ] 5.6 写失败测试：原数量守恒；左右进后理论重量下降量等于截面积 × 扣减长度 × 比重 × 总数。
- [ ] 5.7 实现紧凑测量合同 `bh_setback_measurements/v1` 的解析和严格字段校验。
- [ ] 5.8 实现 `enhance_bh_projection()`：只替换当前 projection 的 BH organized rows 和 PartCandidate，非 BH 对象不变。
- [ ] 5.9 新增聚合警告类别“BH图纸未进入Excel”及明确人工建议；正常图不写报告。
- [ ] 5.10 运行：

```bash
cd /home/Creeken/Paper/CAD_research/complete_framework/Stages/excel_final
../../backend/.venv/bin/pytest -q tests/test_bh_stage2.py tests/test_part_builder.py tests/test_domain_quality.py
```

- [ ] 5.11 提交：`feat(excel): model BH setback row expansion and invariants`

## Task 6：实现第一阶段基线核验和整表重建

**文件：**

- 新增：`Stages/excel_final/stage2_workbook.py`
- 新增：`Stages/excel_final/tests/test_stage2_workbook.py`
- 修改：`Stages/excel_final/pipeline.py`
- 修改：`Stages/excel_final/main.py`
- 修改：`backend/app/modules/excel_processing/stage_adapter.py`
- 修改：`backend/tests/excel_processing/test_excel_final_adapter.py`

- [ ] 6.1 写失败测试：Stage 1 正式 Excel 必须恰好包含六个规范 sheet、正确可见表头和可回读公式缓存。
- [ ] 6.2 写失败测试：从冻结源表重建的非 BH 整理行签名、BH 基线对、part 资格和可见值必须与 Stage 1 正式 Excel 一致。
- [ ] 6.3 写失败测试：任一非 BH 值、BH 基线身份或 part 资格漂移时报 `EXCEL_STAGE2_BASELINE_DRIFT`，不写正式输出。
- [ ] 6.4 写失败测试：重建使用 Stage 1 workbook 的第一张 `原表`，删除并重新生成其余五张，最终没有隐藏列、隐藏行或历史辅助 sheet。
- [ ] 6.5 实现规范化 `CanonicalBaselineSignature`，忽略 xlsx 元数据时间，但不忽略业务值、公式或 part 身份。
- [ ] 6.6 在 `main.py` 增加独立操作：

```text
process-stage2 --input <frozen-source> --stage1 <stage1.xlsx>
               --measurements <compact.json> --output <stage2.xlsx>
               --internal-output <internal.xlsx>
```

- [ ] 6.7 Stage2 流程调用 `build_canonical_projection()`、`enhance_bh_projection()`、`write_canonical_projection(..., CUT_LENGTH)`。
- [ ] 6.8 backend adapter 验证进程协议、正式输出和 internal 输出；错误只向上返回稳定代码和中文业务信息。
- [ ] 6.9 运行：

```bash
cd /home/Creeken/Paper/CAD_research/complete_framework
backend/.venv/bin/pytest -q Stages/excel_final/tests/test_stage2_workbook.py \
  backend/tests/excel_processing/test_excel_final_adapter.py
```

- [ ] 6.10 提交：`feat(excel): rebuild stage2 workbook from verified stage1 baseline`

## Task 7：建立当前分类 Run 的 BH 批量查询和清单摘要

**文件：**

- 修改：`backend/app/modules/dxf_classification/schemas.py`
- 修改：`backend/app/modules/dxf_classification/persistence.py`
- 修改：`backend/app/modules/dxf_classification/interface.py`
- 新增：`backend/tests/dxf_classification/test_bh_stage2_inputs.py`
- 修改：`backend/tests/dxf_classification/test_dxf_classification_pipeline.py`

- [ ] 7.1 新增 `DxfBhSetbackInput`，至少包含 classification item ID、output file ID、output name、profile、StoredFile SHA-256/大小/桶/key 和 classifier version。
- [ ] 7.2 写失败测试：只选当前 Run 中 classified、BH、eligible 的项，排除 BOX、待确认、不可读和旧 Run。
- [ ] 7.3 写失败测试：Run、Job、workflow、project、attempt 任一不一致都返回明确错误。
- [ ] 7.4 写失败测试：5000 项只进行固定数量 SQL 查询；禁止循环 `db.get(StoredFile)`。
- [ ] 7.5 实现 `list_bh_setback_inputs(db, *, run_id)` 的 join/bulk query，并复用同一批量文件加载器消除现有 split candidate 的 N+1。
- [ ] 7.6 实现版本化 canonical manifest hash；测试输入顺序变化不改变摘要、任一业务字段变化必改变摘要。
- [ ] 7.7 运行：

```bash
cd /home/Creeken/Paper/CAD_research/complete_framework/backend
uv run pytest -q tests/dxf_classification/test_bh_stage2_inputs.py tests/dxf_classification
```

- [ ] 7.8 提交：`perf(classification): bulk-load BH stage2 inputs with manifest hash`

## Task 8：实现 Stage2 预检、项目隔离和幂等 Job 绑定

**文件：**

- 修改：`backend/app/modules/workflows/templates.py`
- 修改：`backend/app/modules/workflows/contracts.py`
- 修改：`backend/app/modules/workflows/stage_execution.py`
- 修改：`backend/app/modules/workflows/routes/execution.py`
- 修改：`backend/app/modules/workflows/schemas/orchestration.py`
- 修改：`backend/tests/workflows/test_workflow_production.py`
- 修改：`backend/tests/workflows/test_workflow_dxf_contracts.py`

- [ ] 8.1 将 `excel_stage2` 标为 automated/implemented，改正 required inputs 和两个正式 artifact。
- [ ] 8.2 抽取 `_resolve_verified_source_excel()`，第一、二阶段复用同一冻结源表核验，不复制一套易漂移逻辑。
- [ ] 8.3 实现 `preflight_excel_stage2()`，返回 Stage 1 文件、分类版本、BH 图数、Excel BH 唯一零件/出现数、预计匹配/缺失/多余和 checks。
- [ ] 8.4 写越权测试：两个项目具有相同文件名和零件号，项目 A 预检和执行不能读取项目 B 的 Run、Artifact 或 StoredFile。
- [ ] 8.5 写恶意 payload 测试：额外传 file/run/project ID 返回 422。
- [ ] 8.6 写损坏数据库绑定测试：Stage1 Job、classification Job、attempt 或 project 被替换时返回 409。
- [ ] 8.7 将 `_bound_dxf_split_job()` 泛化为当前自动阶段的 `_bound_stage_job()`；工作流行锁下，两名项目成员并发提交只能得到一个逻辑 Job。
- [ ] 8.8 Job params 只保存摘要和少量 ID；5000 项测试断言序列化参数保持小于 4 KiB。
- [ ] 8.9 无 BH 时预检仍 ready，执行生成空读取表和与 Stage1 等价的 Stage2 正式表，不锁住下一步。
- [ ] 8.10 运行：

```bash
cd /home/Creeken/Paper/CAD_research/complete_framework/backend
uv run pytest -q tests/workflows/test_workflow_production.py tests/workflows/test_workflow_dxf_contracts.py
```

- [ ] 8.11 提交：`feat(workflow): bind isolated Excel stage2 inputs and preflight`

## Task 9：实现专用 Stage2 Worker 执行状态机

**文件：**

- 新增：`backend/app/modules/excel_processing/stage2_execution.py`
- 修改：`backend/app/modules/excel_processing/tasks.py`
- 修改：`backend/app/modules/excel_processing/interface.py`
- 修改：`backend/app/modules/jobs/creation.py`
- 修改：`backend/app/modules/jobs/dispatch.py`
- 修改：`backend/app/platform/config/constants.py`
- 修改：`backend/app/platform/config/settings.py`
- 修改：`backend/app/platform/messaging/celery_app.py`
- 新增：`backend/tests/excel_processing/test_excel_stage2_execution.py`
- 修改：`backend/tests/architecture/test_excel_processing_boundaries.py`

- [ ] 9.1 新增 task/pipeline/step 常量和 `app.workers.tasks_excel_stage2.process_excel_stage2` Celery task。
- [ ] 9.2 写执行测试：claim exact attempt、重算 manifest、逐文件 SHA-256、Reader 进度、Stage 子进程、公式验证、MySQL 导入、两个文件保存、Job 完成。
- [ ] 9.3 写失败测试：对象缺失/摘要变化、Reader 非 OK、Stage2 规则失败、DB 导入失败、第二个对象保存失败都不得把阶段标成功。
- [ ] 9.4 写 stale attempt 测试：attempt 改变后立即停止，清理工作目录和未结 transfer，不附加旧结果。
- [ ] 9.5 实现有界预取：最多 2 张待分析，单张完成立即 unlink；存储客户端不共享 SQLAlchemy Session。
- [ ] 9.6 实现节流进度：每秒或每约 0.5%，在 `progress_data` 写 phase、processed_files、total_files、current_file_name 和 message。
- [ ] 9.7 Reader 表先保存为带当前 `job_attempt` 的 AnalysisResult；Reader 有失败时该 Result 状态为 failed、可供诊断下载，但不创建 WorkflowArtifact，也不创建 `stage2_excel`。
- [ ] 9.8 成功时先完成两个 MinIO transfer 和 StoredFile，再添加两个当前 attempt AnalysisResult；最后完成 Job。
- [ ] 9.9 第二阶段 internal workbook 进入 `import_workbook_for_job(... source_type="stage2_bh")`，公共 xlsx 才作为下载结果。
- [ ] 9.10 运行：

```bash
cd /home/Creeken/Paper/CAD_research/complete_framework/backend
uv run pytest -q tests/excel_processing/test_excel_stage2_execution.py \
  tests/architecture/test_excel_processing_boundaries.py \
  tests/infrastructure/test_celery_recovery.py
```

- [ ] 9.11 提交：`feat(stage2): execute BH reader and Excel rebuild atomically`

## Task 10：让 Workflow 只认当前 attempt 的两个产物

**文件：**

- 修改：`backend/app/modules/workflows/job_sync.py`
- 修改：`backend/app/modules/workflows/contracts.py`
- 修改：`backend/app/modules/workflows/artifacts.py`
- 修改：`backend/app/modules/workflows/routes/archive.py`
- 修改：`backend/tests/workflows/test_workflow_production.py`
- 修改：`backend/tests/workflows/test_workflow_input_api.py`

- [ ] 10.1 抽取 `_current_attempt_artifacts(stage)`，所有 Job-backed 阶段按 metadata `job_id/job_attempt` 过滤，不再只特判拆板。
- [ ] 10.2 写回归：旧 attempt 两个产物存在、新 attempt 只有一个时，阶段必须失败为 outputs incomplete，不能误用旧文件补齐。
- [ ] 10.3 将 `stage2_excel`、`bh_setback_excel` 加入 Excel Artifact 格式合同。
- [ ] 10.4 新增参数化单文件下载 helper，供 Stage1 结果、Stage2 结果、Reader 表复用完整 lineage、对象 stat、Transfer 和审计校验。
- [ ] 10.5 新增端点；Reader 端点允许读取当前 attempt 的成功审计表或失败诊断表，正式 Stage2 端点仍只允许完整成功阶段：

```text
GET /api/v1/workflows/{id}/stages/excel_stage2/download-result
GET /api/v1/workflows/{id}/stages/excel_stage2/download-reader-result
```

- [ ] 10.6 通用阶段 ZIP 对 Stage1/Stage2 返回明确提示，要求使用单文件下载；不把两个 Excel 打成 ZIP。
- [ ] 10.7 运行：

```bash
cd /home/Creeken/Paper/CAD_research/complete_framework/backend
uv run pytest -q tests/workflows/test_workflow_production.py tests/workflows/test_workflow_input_api.py
```

- [ ] 10.8 提交：`fix(workflow): enforce current-attempt Excel stage artifacts`

## Task 11：增加专用 worker、工作目录和生产配置

**文件：**

- 修改：`compose.yaml`
- 修改：`compose.dev.yaml`
- 修改：`.env.example`
- 修改：`.env.docker.example`
- 修改：`backend/app/platform/config/settings.py`
- 修改：`backend/app/modules/operations/data_catalog/system_routes.py`
- 修改：`backend/tests/infrastructure/test_compose.py`
- 修改：`backend/tests/infrastructure/test_config.py`
- 修改：`backend/tests/infrastructure/test_celery_minio_deployment.py`
- 修改：`scripts/release/server-deploy.sh`

- [ ] 11.1 增加配置：

```text
EXCEL_STAGE2_PIPELINE_ENABLED=false
EXCEL_STAGE2_TIMEOUT_SECONDS=7200
EXCEL_STAGE2_WORK_ROOT=/app/var/excel-stage2-work
EXCEL_STAGE2_WORKER_CONCURRENCY=1
```

- [ ] 11.2 新增 `worker-excel-stage2`，监听 `excel_stage2`，挂载 `app_var`，restart/healthcheck/ulimit/security 与其他 worker 一致。
- [ ] 11.3 Celery queue registry、task route、recovery 清理和部署服务清单都加入 `excel_stage2`。
- [ ] 11.4 数据控制台系统状态返回中文“Excel 第二阶段服务”，但不暴露路径和内部参数。
- [ ] 11.5 写 Compose 合同：专用 queue 不与 Stage1 混用；默认并发 1；工作目录位于持久 volume 而非 `/tmp`。
- [ ] 11.6 运行：

```bash
cd /home/Creeken/Paper/CAD_research/complete_framework/backend
uv run pytest -q tests/infrastructure/test_compose.py tests/infrastructure/test_config.py \
  tests/infrastructure/test_celery_minio_deployment.py
docker compose --profile workers config >/tmp/complete-framework-stage2-compose.txt
```

- [ ] 11.7 提交：`ops(stage2): add isolated worker and bounded production scratch`

## Task 12：实现前端第二阶段卡、异步进度和下载

**文件：**

- 新增：`frontend/src/features/workflows/BhSetbackExcelStagePanel.tsx`
- 修改：`frontend/src/features/workflows/WorkflowDetailPage.tsx`
- 修改：`frontend/src/features/workflows/WorkflowStageArchiveCard.tsx`
- 修改：`frontend/src/features/workflows/workflows.api.ts`
- 修改：`frontend/src/features/workflows/workflow.ts`
- 修改：`frontend/src/features/workflows/model/workflowPresentation.tsx`
- 修改：`frontend/src/features/jobs/jobs.api.ts`
- 修改：`frontend/src/features/jobs/useJobEvents.ts`
- 修改：`frontend/src/features/operations/components/data-console/ProductionTaskPanel.tsx`
- 修改：`frontend/src/shared/api/error.ts`
- 修改：`frontend/tests/e2e/workflows/workflow-detail.spec.ts`

- [ ] 12.1 从 `WAITING_LAUNCH_STAGES` 删除 `excel_stage2`；能力标签显示“服务器已实现”。
- [ ] 12.2 新增 Stage2 preflight、execute、getJob、两个单文件下载 API 和类型。
- [ ] 12.3 面板显示 Stage1 文件、分类版本、BH 图数、Excel 唯一零件/出现数、预计缺失/多余和 checks。
- [ ] 12.4 按钮文本固定为“处理 BH 的左右进”；preflight 未 ready、Job queued/running 时禁止重复提交。
- [ ] 12.5 复用 `JobProgressBar`、`useJobEvents` 和 2.5 秒 Query fallback；SSE 同时更新 `['job', jobId]` cache。
- [ ] 12.6 进度条显示“已读取 X / Y 张”，终态后刷新 workflow、preflight 和 artifact；失败显示中文错误及有限文件示例。
- [ ] 12.7 成功后显示“下载处理后的 Excel”和“下载左右进读取表”，两个按钮均显示 `TransferProgressBar`。
- [ ] 12.8 Reader 失败但当前 attempt 已保存诊断表时，显示“下载左右进诊断表”；它不能被误显示为正式阶段产物。
- [ ] 12.9 当前 attempt 没有完整 Artifact 时正式 Excel 下载按钮必须禁用；旧 attempt Artifact 不可见。
- [ ] 12.10 完成后保持选择 Stage2，不自动跳到下一阶段。
- [ ] 12.11 数据控制台新增 `process_excel_stage2: BH左右进与Excel深化` 中文标签。
- [ ] 12.12 运行：

```bash
cd /home/Creeken/Paper/CAD_research/complete_framework/frontend
npm run check:architecture
npx tsc -b --pretty false
npx playwright test tests/e2e/workflows/workflow-detail.spec.ts
```

- [ ] 12.13 提交：`feat(frontend): operate and download BH Excel stage2`

## Task 13：覆盖错误提示和工人可操作性

**文件：**

- 新增：`backend/app/modules/excel_processing/stage2_contracts.py`
- 修改：`frontend/src/shared/api/error.ts`
- 修改：`backend/tests/contracts/test_frontend_contract.py`
- 修改：`frontend/tests/e2e/workflows/workflow-detail.spec.ts`

- [ ] 13.1 将设计规格第 12 节全部错误代码加入后端稳定错误和前端中文映射。
- [ ] 13.2 每个错误携带结构化 details：总失败数、最多 20 个文件名、缺失零件最多 20 个、建议操作；禁止传 traceback 和本机路径。
- [ ] 13.3 错误 Blob 下载和普通 JSON 错误都经过现有 `describeApiError/parseApiError`。
- [ ] 13.4 Playwright 覆盖：缺 Stage1、分类 stale、缺 BH 图、Reader 失败、规格冲突、结果未就绪、对象缺失。
- [ ] 13.5 运行：

```bash
cd /home/Creeken/Paper/CAD_research/complete_framework/backend
uv run pytest -q tests/contracts/test_frontend_contract.py tests/workflows/test_workflow_production.py
cd ../frontend
npx playwright test tests/e2e/workflows/workflow-detail.spec.ts
```

- [ ] 13.6 提交：`fix(stage2): expose actionable Chinese workflow errors`

## Task 14：真实 workflow 5/6 端到端验收

**前提：** 只在本机 18080 的测试容器操作；不访问或修改 gg。

**证据目录：**

- 新增：`docs/verification/excel-stage2/workflow-5.md`
- 新增：`docs/verification/excel-stage2/workflow-6.md`
- 新增：`docs/verification/excel-stage2/manifest.json`

- [ ] 14.1 以当前源码构建 development/production-shaped 本机栈，迁移完成且所有核心容器 healthy。
- [ ] 14.2 对 `/workflows/5` 执行 preflight → Stage2 → 两次单文件下载，记录 Job/attempt/Artifact/File ID 和 SHA-256。
- [ ] 14.3 核验 workflow 5：Reader 112/112 OK；图纸多余 2、Excel 缺图 0；整理表 2360 行；part 1123 行；新增 14 行；最短下料长度 400 mm；理论总重差约 -844.806 kg。
- [ ] 14.4 对 `/workflows/6` 重复完整流程，记录独立证据。
- [ ] 14.5 核验 workflow 6：Reader 111/111 OK；图纸多余 2、Excel 缺图 0；整理表 2347 行；part 1112 行；新增 14 行；最短下料长度 400 mm；理论总重差约 -877.871 kg。
- [ ] 14.6 对两个最终 xlsx 逐字段检查：非 BH 整理行/part 不变；BH 左右进、下料长度、数量、公式、缓存、源重量归属正确；J/K 空；L 类型正确；报告精炼。
- [ ] 14.7 明确证明 workflow 5 的四层/三层多余图纸没有出现在 workflow 6 输出，反之亦然。
- [ ] 14.8 关闭并清理测试任务时只清理本次新建数据，不删除 workflow 5/6 原有输入。
- [ ] 14.9 提交：`test(stage2): validate workflows 5 and 6 end to end`

## Task 15：5000 张、并发项目和故障恢复压力门

**文件：**

- 新增：`backend/tests/workflows/test_excel_stage2_load.py`
- 新增：`scripts/release/verify_excel_stage2_load.py`
- 修改：`backend/tests/infrastructure/test_production_load_tools.py`
- 新增：`docs/verification/excel-stage2/load-test.md`

- [ ] 15.1 构造 5000 条分类账本测试：预检与 execute 参数正确、SQL 查询数固定、Job params <4 KiB。
- [ ] 15.2 构造 5000 个参数化有效 BH 输入的 Reader soak：记录耗时、最大 RSS、临时磁盘峰值和 progress 事件数；不生成图片/完整 JSON。
- [ ] 15.3 同时提交两个不同项目的 Stage2，文件名和零件号故意相同但左右进不同；核验结果、Artifact、MySQL batch 和工作目录完全隔离。
- [ ] 15.4 在 25%、70%、95% 阶段分别模拟 worker kill/restart；旧 running Job 被恢复逻辑关闭或安全重试，未结 transfer 被 reconciliation 回收，无半套正式产物。
- [ ] 15.5 模拟 MinIO 短暂失败、MySQL 断连和 SSE 断线；任务状态可恢复，前端轮询继续显示，下载不产生损坏 xlsx。
- [ ] 15.6 验证取消任务在一个进度节流周期内停止，工作目录最终为空。
- [ ] 15.7 concurrency=1 为发布默认；只有 concurrency=2 压测在服务器 CPU、内存、MySQL 和 MinIO 指标均无明显恶化时才记录为可选配置，不改变默认值。
- [ ] 15.8 提交：`test(stage2): gate 5000-file isolation and recovery`

## Task 16：完整回归、保护镜像和文档收口

**文件：**

- 修改：`frontend/src/features/dashboard/DashboardPage.tsx`
- 修改：`docs/guides/operations.md`
- 修改：`README.md` 或当前部署说明中 Stage2 能力表
- 修改：`scripts/verify.sh`（仅在 full gate 尚未覆盖新增测试时）

- [ ] 16.1 更新工人说明：第二阶段使用拆板前 BH 图、上下翼增行、整理表/part 关系、公式和核验方式；不讲 Python 或数据库实现。
- [ ] 16.2 删除“Excel 第二阶段等待上线”等历史文案，不删除 Stage1 独立功能。
- [ ] 16.3 运行 Excel 全回归：

```bash
cd /home/Creeken/Paper/CAD_research/complete_framework/Stages/excel_final
../../backend/.venv/bin/pytest -q
```

- [ ] 16.4 运行 backend 全回归：

```bash
cd /home/Creeken/Paper/CAD_research/complete_framework/backend
uv run ruff check app tests
uv run pytest -q
```

- [ ] 16.5 运行 frontend 门：

```bash
cd /home/Creeken/Paper/CAD_research/complete_framework/frontend
npm run check:architecture
npx tsc -b --pretty false
npm run build
npm run test:e2e
```

- [ ] 16.6 运行仓库完整门：

```bash
cd /home/Creeken/Paper/CAD_research/complete_framework
scripts/verify.sh full
git diff --check
git status --short
```

- [ ] 16.7 构建 protected 镜像，验证 Reader/Stage2 pyc、无源码、MySQL/MinIO 联通、专用 worker healthy，并在本机 18080 做一次最终 smoke。
- [ ] 16.8 审查无用兼容代码、临时脚本、测试输出和僵尸分支；只删除本功能确认无引用的代码。
- [ ] 16.9 提交：`docs(stage2): publish BH Excel processing contract and validation`
- [ ] 16.10 最终发布提交只在全部门通过、worktree 无意外修改、workflow 5/6 和压力证据齐全后创建。

---

## 完成定义

以下任一项未满足，任务不得标记完成：

- 不同项目、不同 workflow、不同 attempt 的图纸和 Excel 没有交叉；
- Reader 正式 Excel 完整，失败图明确且不污染正式 Stage2；
- 整理表增行、数量、下料长度、理论重量、源重量和公式缓存都符合物理含义；
- `part` 与最终整理表逐行可追溯，参数不同绝不合并；
- workflow 5/6 的真实行数和差异结论通过；
- 5000 张批次无大 Job JSON、N+1、内存/磁盘无界增长或虚假进度；
- 前端只显示中文业务提示，两个单文件下载都有真实进度；
- 本机 protected 容器在 18080 可用且没有加密后丢功能；
- 全量测试、完整验证脚本和 Git 检查全部通过。
