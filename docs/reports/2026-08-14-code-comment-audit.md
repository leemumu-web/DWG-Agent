# 代码注释完整性审计报告（只读审计，未修改任何代码）

- **审计日期**：2026-08-14
- **审计范围**：仓库全部源码与测试（backend/、frontend/、Stages/、scripts/、infra/、tools/、windows/、agents/、tests/），约 600+ 文件 / 20 万行
- **审计方法**：分区并行子代理只读审查（多轮工作流 + 定向子代理共 14 个审计任务，覆盖 11 个区域；每区先读 CONTEXT.md 领域词汇表对齐业务语言），主代理对 high 级发现抽样复核，所有行号为近似值

## 一、总体结论

共识别 **179 处**注释缺口（High 43 / Medium 109 / Low 27）。

整体注释水平**中等偏上**：后端平台层（platform/）、共享测试支撑（conftest/support）、前端 shared/api 与若干 Stage（dwg2dxf、excel_final 主流程）文档化良好；缺口集中在四类位置：

1. **跨模块契约文件**：jobs.interface、workflows.interface、excel_final/domain.py 等纯 re-export/共享数据结构没有文档化调用方必须知道的不变量、错误模式与顺序约束（仓库 CONTEXT.md 的明确要求）；
2. **算法核心的魔法数字**：拆板/分类/读取器的几何容差、惩罚系数、搜索预算（bh_geometry、bh_extractor、title_block、analyzer 等）几乎全部没有数值来源与失效路径说明；
3. **领域语义与补偿路径**：attempt 世代、fencing、输入冻结哈希、文件账本补偿等关键不变量只在 README 而不在代码现场；
4. **测试中的裸常量**：5000/30/4096/60s 等边界值未引用生产常量，生产调整后测试会悄然失同步。

## 二、统计概览

### 2.1 按区域

| 区域 | 发现数 | High | Medium | Low |
|---|---|---|---|---|
| cad_processing+dxf_classification+dxf_splitting+excel_processing | 21 | 5 | 14 | 2 |
| frontend React 前端 (frontend/src/) | 20 | 6 | 10 | 4 |
| Stages 2/2：转换与 Excel 处理（dwg2dxf / dxf2dwg / dxf2excel / excel_final / remnant_drawing_reader） | 20 | 2 | 15 | 3 |
| jobs+workflows | 20 | 9 | 9 | 2 |
| backend 业务模块 3/4：identity + projects + remnant_inventory | 18 | 4 | 13 | 1 |
| platform+bootstrap+integrations | 18 | 3 | 12 | 3 |
| Stages 1/2: 拆板与分类算法（steel_dxf_split_v1.5.2 / steel_dxf_classifier_v1.1.0 / bh_left_right_reader / BOX左右进读取） | 16 | 4 | 10 | 2 |
| backend/tests/ 与 tests/（后端测试与验证脚本） | 12 | 1 | 8 | 3 |
| files 模块 | 11 | 1 | 7 | 3 |
| scripts+infra（主代理自审补充） | 9 | 2 | 6 | 1 |
| operations+automation | 8 | 2 | 3 | 3 |
| excel_final+remnant_drawing_reader 测试补漏 | 6 | 4 | 2 | 0 |

### 2.2 按缺口类型

| 类型 | 数量 | 说明 |
|---|---|---|
| 魔法数字 | 43 | 见各区域明细 |
| 跨模块契约 | 35 | 见各区域明细 |
| 复杂逻辑 | 33 | 见各区域明细 |
| 领域语义 | 25 | 见各区域明细 |
| 错误/补偿路径 | 16 | 见各区域明细 |
| 模块 docstring | 15 | 见各区域明细 |
| 公开 docstring | 7 | 见各区域明细 |
| 测试意图 | 3 | 见各区域明细 |
| 其他 | 2 | 见各区域明细 |

## 三、High 优先级发现（跨模块契约 / 复杂算法 / 安全相关）

### backend 业务模块 3/4：identity + projects + remnant_inventory

| 文件 | 位置 | 元素 | 类型 | 问题 | 建议 |
|---|---|---|---|---|---|
| `backend/app/modules/remnant_inventory/export.py` | L77-82 | _excel_safe_value | 错误/补偿路径 | 这是防止 Excel/CSV 公式注入（以 = + - @ 开头的单元格值被强制前缀单引号）的安全措施，但代码没有任何注释说明威胁模型，后续维护者可能把它当成无意义的字符串变换而删掉。 | 添加注释说明：导出内容来自工人手工填写的项目编号/备注等字段，若以 =、+、-、@ 开头会被 Excel 当作公式执行（公式注入），故强制转义为文本；保留 lstrip 后判断的原因（防前导空格绕过）。 |
| `backend/app/modules/remnant_inventory/execution.py` | L268-291 | _fail_active_job | 错误/补偿路径 | 该补偿助手执行 'queued 任务先 claim 再 fail' 的两步状态机舞蹈（claim 时 progress=0 是魔法数字），且要同时失败 conversion 与 parse 两个 Job；没有任何 docstring 解释为什么必须 claim 之后才能 fail、各状态下的后果是什么。 | 补充 docstring：fail_job_attempt 只接受 running 状态，所以 queued 的 Job 必须先 claim 过渡到 running 再失败；progress=0 表示重置进度；说明对已 complete/failed 的 Job 是安全 no-op，以及本函数在 dispatch 失败与转换失败两条补偿路径中的角色。 |
| `backend/app/modules/remnant_inventory/execution.py` | L332-521 | run_conversion_batch / run_parse_item | 领域语义 | 代码中 item.attempt 是余料导入项的世代计数（retry 时 +1），而所有 Job 的 claim/complete/fail 硬编码 attempt=1；两套 'attempt' 概念（CONTEXT.md 的 Attempt 世代 vs Job 自身 attempt）的关系与 fencing 依据完全没有注释，读者极易误以为 item.attempt 与 job.attempt 是同一个东西。 | 在文件头部或两函数处说明：每次 retry 会清空 conversion_job_id/parse_job_id 并新建 Job，因此 Job 的 attempt 恒为 1，而 item.attempt 是项的世代；所有 UPDATE 都以 status + item.attempt == expected 双重条件做 fencing，旧世代任务因 status/attempt 不匹配而失效，从而满足'旧 attempt 不得修改新 attempt 状态'的不变量。 |
| `backend/app/modules/remnant_inventory/stage_adapter.py` | L28-81 | parse_staged_dxf | 跨模块契约 | 这是调用独立解析 Stage（remnant_drawing_reader）的 seam 实现，调用者必须知道三路错误映射（超时→REMNANT_PARSE_TIMEOUT、非零退出/缺产物→REMNANT_PARSE_FAILED、载荷结构不符→REMNANT_PARSE_CONTRACT_INVALID）和 .result.json 侧车文件约定，但函数与模块均无 docstring，契约只能靠读代码反推。 | 添加 docstring：说明 subprocess 调用方式、错误码到 REMNANT_* 稳定契约的映射、REMANT_* 码是测试与程序判断的稳定接口而错误文本仅供用户，以及 standard_offcut 可空时 ParseResult 各候选字段的含义。 |

### backend/tests/ 与 tests/（后端测试与验证脚本）

| 文件 | 位置 | 元素 | 类型 | 问题 | 建议 |
|---|---|---|---|---|---|
| `backend/tests/workflows/test_workflow_production.py` | L496-509 | _stage2_ready_workflow | 复杂逻辑 | 这是本文件约 25 个测试共用的最复杂 fixture：串联 _production_workflow → _attach_valid_source_excel → _complete_classification_fixture → _complete_excel_stage1_fixture，返回 7 个值的元组，但没有 docstring 说明它建立了什么状态与不变量（输入批次是否冻结、lineage 各 sha256 是否已固化、stage1 job 是否已绑定），调用者无法判断哪些测试前提已被满足。 | 添加 docstring：说明 fixture 建立的完整状态（冻结输入批次、已完成分类与 stage1 且 job 已绑定、manifest/sha256 已固化）以及调用者可依赖的不变量，并标注与输入冻结/Attempt 领域概念的对应关系。 |

### frontend React 前端 (frontend/src/)

| 文件 | 位置 | 元素 | 类型 | 问题 | 建议 |
|---|---|---|---|---|---|
| `frontend/src/features/workflows/workflow.ts` | L76-78 | WorkflowStageExecutionPayload.execution_kind | 跨模块契约 | 跨模块契约类型：WorkflowDetailPage/DxfClassificationPanel/DrawingProcessingPanel 都要传 execution_kind，但合法取值（'steel_dxf_classification'、'drawing_processing' 及模板 capability.execution_kind）与服务器端契约只散落在各调用点。 | 在类型上注释合法取值来源（模板 capability 或固定字面量）、执行后返回的 {reused, retried} 语义，以及“同一 attempt 幂等、旧 attempt 不得覆盖新 attempt 状态”的不变量。 |
| `frontend/src/features/workflows/WorkflowDetailPage.tsx` | L117-125 | visibleArtifacts 过滤 (metadata job_id/job_attempt 匹配) | 领域语义 | 该过滤实现 CONTEXT.md 的 Attempt 世代语义：旧 attempt 的产物不得冒充当前阶段结果，但代码没有任何注释说明“为什么只展示与当前 stage.job_id/job_attempt 一致的 artifact”。 | 注释：拆板阶段的 artifact 必须匹配当前 stage 的 job_id 与 job_attempt，旧世代产物被隐藏以免与正式结果混淆；其他阶段不过滤的原因也应点明。 |
| `frontend/src/features/workflows/workflows.api.ts` | L242-251 | executeWorkflowStage 返回 {workflow, job, reused, retried} | 跨模块契约 | 调用方按 reused/retried 显示不同提示（'继续跟踪'/'已重新入队'/'已提交'），但契约处未说明这两个布尔值的触发条件（重复执行同一 attempt 复用已有 job？重试生成新 attempt？），调用者无法正确解释结果。 | 为 executeWorkflowStage 加 docstring：reused=本次执行复用了已存在的 job（幂等去重），retried=对失败 attempt 重新入队；以及提交前必须自行校验 current_stage 未漂移（fencing）的约定。 |
| `frontend/src/features/excel-processing/model/requestKey.ts` | L1-12 | createRequestKey (UUID v4 位运算 + 最后回退分支) | 复杂逻辑 | bytes[6]/bytes[8] 的 version/variant 位运算无任何注释，且最后回退分支返回的是 '时间戳-随机数' 而非 UUID 格式——若服务器按 UUID 解析幂等键会直接破坏去重契约。 | 注释：前两分支按 RFC 4122 构造 UUID v4（version=4、variant=10 位）供幂等去重；回退分支是非 UUID 字符串，需说明其可用性与服务器端对 key 格式的约束。 |
| `frontend/src/features/excel-processing/components/ExcelFinalTools.tsx` | L59-86 | handbookValidation 的 D 系列材质路由 (HRB→rebar, HPB/Q235B/Q355B→round_bar) | 领域语义 | 该规则是 CONTEXT.md「五金手册材质路由」在前端的镜像副本，后端 Adapter 按同一映射校验、靠跨 seam 测试防漂移；代码未注明此对应关系，也未解释 /^D\d+(\.\d+)?$/ 正则与 startsWith('HRB') 前缀匹配的边界。 | 注释：此校验须与后端 Handbook Material Routing 映射保持一致（有跨 seam 测试约束），D 系列指 D8 这类直径规格、材质前缀匹配含义，修改任一侧都必须同步。 |
| `frontend/src/features/remnant-inventory/RemnantAutoImportPanel.tsx` | L59-67 | readDirectoryEntries 的 while 循环 | 复杂逻辑 | 循环存在的真正原因是 WebKit readEntries 每次最多返回约 100 条、必须反复调用直至空数组；没有注释时极易被“简化”成单次调用而静默截断大文件夹。 | 注释：WebKit 目录读取按 ~100 条分批返回，必须循环 readEntries 直到返回空数组，否则子目录超过 100 项时会被静默丢弃。 |

### Stages 1/2: 拆板与分类算法（steel_dxf_split_v1.5.2 / steel_dxf_classifier_v1.1.0 / bh_left_right_reader / BOX左右进读取）

| 文件 | 位置 | 元素 | 类型 | 问题 | 建议 |
|---|---|---|---|---|---|
| `Stages/steel_dxf_split_v1.5.2/src/steel_dxf_split/bh_proofs.py` | L58-70 | ProofReport.disposition | 跨模块契约 | 这是拆板结果能否自动发布的唯一验收闸门：critical 义务的 PASS/MISSING/CONFLICT/INCOMPLETE 与 search_complete 的组合直接决定 AUTO_ACCEPT/REVIEW_REQUIRED/REJECTED，且 box/proofs.py 逐行镜像同一逻辑，但没有任何注释说明该映射的业务理由。 | 在 disposition 上补充注释：为什么 CONFLICT/INCOMPLETE 判 REJECTED 而 MISSING 只判 REVIEW_REQUIRED、为什么无 critical 义务或搜索不完整必须 REJECTED（fail-closed），并说明 synthetic blocker（BH.PROOF.SEARCH.COMPLETE / SET.NONEMPTY）对调用方的含义；同时注明 box/proofs.py 必须保持同一语义。 |
| `Stages/steel_dxf_split_v1.5.2/src/steel_dxf_split/bh_geometry.py` | L1993 / 2315 | select_web_polygon / select_flange_polygons 的 grid_candidates 精度网格阶梯 | 复杂逻辑 | 0.001→0.1mm 的 7 级 polygonization 精度阶梯决定几何重建的成败路径，且与后续大量 grid_size*X 容差（cover_tolerance=grid*3、association_tolerance=grid*0.51 等）耦合，但没有任何注释说明阶梯选择依据、各级失败后的回退语义及失败判定。 | 注释说明该阶梯的用途（先细后粗的精度试探）、选择最粗可用网格的判定标准、各级失败后如何处理（failures 列表如何影响结果），以及为什么 0.1mm 是上限（避免吞掉真实倒角/斜切）。 |
| `Stages/steel_dxf_split_v1.5.2/src/steel_dxf_split/bh_geometry.py` | L762-763, 822-841, 892-895 | estimate_flange_developments 启发式阈值 | 魔法数字 | minimum_area=max(100, tf*max(100,0.02*L))、minimum_span=max(100,0.30*min(...))、0.98 覆盖比、strip_tolerance=0.02*tf、双路径阈值 max(5.0,0.005*L)、直条 2.0° 角度容差等直接决定翼缘展开目标长度（制造下料长度），数值来源与失效后果完全未解释。 | 为每组阈值补注释：它过滤什么几何（如 0.98 排除被腹板吸收的面）、为什么取该比例（源自哪些回归图/制造公差），以及阈值判定失败时保守回退到 projection_only 的原因。 |
| `Stages/steel_dxf_split_v1.5.2/src/steel_dxf_split/pipeline.py` | L353-415 | _promote_task_directory | 错误/补偿路径 | 成对发布采用备份-替换-回滚的崩溃一致性协议（同时备份 final 与 obsolete 两条路由、os.replace 顺序、多次 fsync_directory），但只有一行 docstring，未说明崩溃窗口会留下什么状态、为什么必须清掉 obsolete 路由（防旧结果残留）、回滚顺序为何先删 final 再还原备份。 | 补充注释说明：同一构件在 auto_accepted 与 manual_review 之间来回切换时两条路由必须互斥发布；异常时按什么顺序恢复以保持目录原子性；fsync 的两次调用分别保证什么（发布前落盘/回滚后落盘）。 |

### Stages 2/2：转换与 Excel 处理（dwg2dxf / dxf2dwg / dxf2excel / excel_final / remnant_drawing_reader）

| 文件 | 位置 | 元素 | 类型 | 问题 | 建议 |
|---|---|---|---|---|---|
| `Stages/excel_final/domain.py` | L13-100 | SourcePart / ComponentSourceRow / ParentPartEvidence / SplitPart | 跨模块契约 | 这四个 frozen dataclass 是 reader → canonical_pipeline → writer_parts → stage2 跨模块共享的输入/证据契约（CONTEXT.md 的 Excel Final 输入接入核心），但零类 docstring、零字段注释；调用者无法从代码得知 component_qty(构件数) 与 original_qty(原数量) 的区别、invalid_fields 语义、classification 何时为空、各 weight 字段的 None 含义。 | 为每个记录添加契约 docstring：字段语义（如 source_unit_net 为源单净重、component_qty 为构件数量）、不变量（frozen、invalid_fields 列出缺失字段名）、None 表示'源值缺失'的约定，以及跨模块传递顺序。 |
| `Stages/excel_final/material_routing.py` | L1-39 | D_MATERIAL_CATEGORY_BY_PREFIX / d_series_category | 领域语义 | 模块 docstring 只有一句'Authoritative material-family routing'，没有说明 CONTEXT.md 明确要求的领域规则：HRB→螺纹钢(rebar)、HPB/Q235B/Q355B→圆钢(round_bar)，路由只决定查询类别不代表手册命中，其他材质不得跨类别借用重量；dict 值 'rebar'/'round_bar' 与 HandbookCategory 的对应关系也无注释。 | 模块 docstring 写明'D 系列按材质族选择唯一手册类别'的完整规则与边界（不得跨类别借用），并在 dict 上注释每个值对应的 HandbookCategory 枚举与业务名称（螺纹钢/圆钢）。 |

### jobs+workflows

| 文件 | 位置 | 元素 | 类型 | 问题 | 建议 |
|---|---|---|---|---|---|
| `backend/app/modules/jobs/interface.py` | L1-88 | 模块整体（jobs.interface 跨模块边界） | 跨模块契约 | CONTEXT.md 明确要求 Interface 必须文档化『调用者必须知道的不变量、错误模式、顺序和配置』，而本文件只有一行『Public Job/Result/Review boundary』，纯 re-export 无任何契约说明；它被 workflows/excel_processing/dxf_* /files 等多个模块导入。 | 在模块 docstring 中写明：stage_job_dispatch/stage_conversion_dispatch 必须在创建 Job 的同一事务内、commit 之前调用；worker 侧唯一写入口是 claim_queued_job/commit_job_progress/complete_job_attempt/fail_job_attempt 且必须携带精确 attempt（fencing）；run_local_stub_job 是非生产 stub；reconcile_stale_running_jobs 是恢复边界而非 broker 租约。 |
| `backend/app/modules/workflows/interface.py` | L1-33 | 模块整体（workflows.interface 跨模块边界） | 跨模块契约 | 同样是单行 docstring 的纯 re-export 面，却承载了 files 删除保护（find_frozen_input_reference）、dxf_classification、bootstrap 等多个外部调用方的契约，调用方不变量完全靠 README 而非接口自身表达。 | 补充契约文档：bind_stage_job 只能用于 linux_production 且输入已冻结；sync_workflow_from_jobs 是只读投影重放（先跑 workflow_needs_sync 再决定是否同步）；find_frozen_input_reference 只返回冻结清单的不可变标识供 files 删除守卫使用；attach_artifact 要求 artifact_type 在模板 capability 白名单内。 |
| `backend/app/modules/jobs/outbox.py` | L251-281 | lease_next_dispatch 的 invalid 组回退分支 | 复杂逻辑 | 当整组不可认领或 mode/task_type/pipeline 不一致时，函数仍故意以 mode='invalid' 租约并提交，随后 publish 才抛 PermanentDispatchError——这个『故意消费坏组以免毒丸消息永久卡住队列』的设计意图没有任何注释，读者容易误判为 bug。 | 注释说明：对不一致/不可认领的组仍然写入租约并提交，是为了让后续 settle 流程把该组重置（避免 SKIP LOCKED 永远跳过同一坏组导致活锁），并解释为何此时把 mode 置为 'invalid' 而不是直接跳过。 |
| `backend/app/modules/jobs/outbox.py` | L329-393 | _settle_publish_failure（永久/临时失败结算） | 错误/补偿路径 | permanent 分支会通过带 status/attempt 守卫的 UPDATE 直接把 Job 置为 failed 并写 error 事件，transient 分支只重置 pending + 指数退避；触发条件（PermanentDispatchError vs 其他异常）与『失败结算会覆盖排队中的 Job 状态』的后果没有任何函数级注释。 | 为函数补充 docstring：说明永久失败（该发布版本无法处理此快照）直接失败 Job 的原因、守卫条件（仅 JOB_QUEUED 且 attempt 匹配才生效，worker 已认领的行不受影响），以及 transient 分支按 retry_delay 退避重投、delivery_attempts 单调递增的语义。 |
| `backend/app/modules/jobs/dispatch.py` | L170-216 | publish_dispatch（稳定 Celery task ID 契约） | 跨模块契约 | lease.dispatch_uid 被直接用作 Celery task_id 以实现『模糊投递重复同 ID、worker 靠 status/attempt 守卫保证业务幂等』这一核心契约，代码现场无注释；同时 known_pipelines 包含 REMNANT 管线但 TASK_PIPELINES 未映射它们（会落到 PIPELINE_STUB 兜底再被 expected_pipeline 检查拒绝），两处映射的一致性约束未说明。 | 注释说明 dispatch_uid 双用途（组标识 + 稳定 task_id）与 README 中『ambiguous delivery 重复稳定 ID、守卫保证一次生效』的对应关系；并写明 TASK_PIPELINES 与 enqueue_job 分支必须同步维护，未映射 task 会被 expected_pipeline 检查以 PermanentDispatchError 拒绝（有意为之）。 |
| `backend/app/modules/jobs/models.py` | L119-135 | AnalysisResult（缺少 attempt 列） | 领域语义 | Job/JobStep/JobDispatch 都有 attempt 世代，唯独 AnalysisResult 没有 attempt 列、世代只存在 result_json.job_attempt 里；job_sync.py、batch_exports.py 多处靠『最新一条 succeeded result + result_json.job_attempt 过滤』规避旧 attempt 结果污染，这个领域不对称是多个模块正确性的前提，模型文件本身零注释。 | 在 AnalysisResult 上注释：旧 attempt 的 Result 行保留在同一 job_id 下，调用方必须用 result_json 中的 job_attempt 与当前 Job.attempt 比对，否则重试后的旧结果会误入新 attempt 投影；并说明为何不增加 attempt 列（如迁移成本/兼容）。 |
| `backend/app/modules/workflows/lifecycle.py` | L159-196 | recompute_workflow（阶段取消→工作流 failed 分支） | 领域语义 | 阶段被取消时工作流被重算为 status='failed'（error_code=WORKFLOW_STAGE_CANCELLED）而不是 'cancelled'，这是『可重试失败』与『人工整体取消』的刻意区分，但代码无任何注释说明，误读者会把取消阶段当成整体失败处理。 | 注释解释：单阶段取消 → 工作流置为 failed 且 error_code=WORKFLOW_STAGE_CANCELLED，语义是『该阶段可重试、工作流未终态』；只有 cancel_workflow 才产生整体 cancelled；并说明 recompute_workflow 不提交、由调用方持有事务边界的约定。 |
| `backend/app/modules/workflows/intake/freeze.py` | L217-220 | freeze_input_batch 的 canonical manifest JSON 序列化 | 复杂逻辑 | 冻结清单 SHA-256 依赖 json.dumps(sort_keys=True, separators=(",", ":")) 的规范化序列化，任何序列化格式改动都会使历史冻结哈希失效、下游 manifest_sha256 比对全部失败，但这个『哈希的规范化输入』不变量完全没有注释。 | 注释说明：manifest_sha256 是对该规范化 JSON 字节串的 SHA-256，sort_keys/紧凑分隔符保证跨运行确定性；新增字段会改变哈希（有意的版本化行为），任何序列化变更必须走版本迁移而非就地修改。 |
| `backend/app/modules/workflows/retention.py` | L144-292 | _shared_file_ids（跨表共享文件检测） | 复杂逻辑 | 这是 retention 中最脆弱的逻辑：必须穷举 input/artifact/result/classification/split/review 全部可引用文件的列，漏掉任何一张表就会把仍被其它工作流引用的文件物理删除；函数零 docstring，枚举列表也没有维护说明。 | 为 _shared_file_ids 添加 docstring：定义『shared』= 被其它 workflow 的 input batch、artifact、analysis result、classification run/item、split run/item、review decision 任一引用的文件，逐段注释每类查询对应的表/列及新增引用来源时必须同步维护此函数的规则。 |

### excel_final+remnant_drawing_reader 测试补漏

| 文件 | 位置 | 元素 | 类型 | 问题 | 建议 |
|---|---|---|---|---|---|
| `Stages/excel_final/tests/test_weight_validation.py` | L87-105、130-144 | test_theory_to_gross_threshold_boundaries / test_net_above_theory_threshold_boundaries 的参数化边界值 | 魔法数字 | 100.01/100.5/100.5001/102/102.0001 精确对应 weights.py 的绝对容差 0.01、相对 0.5%（0.005）与 2%（0.02）判定边界，但测试零注释，读者看不出这些数值是刻意贴边构造、哪一档触发哪一级。 | 为两组参数化用例各加一句注释，说明每个边界值对应 ABSOLUTE_THEORY_TOLERANCE / PASS_RELATIVE_TOLERANCE / WARNING_RELATIVE_TOLERANCE 的哪条分支（100.5=恰好 0.5% 通过、100.5001 越过即警告、102=恰好 2%、102.0001 越过即严重）。 |
| `Stages/excel_final/tests/test_weight_validation.py` | L158-213 | test_large_geometry_to_gross_deviation_warns_without_isolating_part / test_large_handbook_to_gross_deviation_remains_severe | 测试意图 | 这对用例承载『手册理论重偏差超 2% 硬隔离零件、几何理论重仅警告不隔离』的核心业务规则，但只在单一 10% 偏差点断言结果，未在 2% 边界上验证手册路径的隔离翻转，也没有注释说明策略与 2% 阈值的关系。 | 注释说明 103/100 是刻意越过 WARNING_RELATIVE_TOLERANCE（2%）的代表值，并建议补一个手册路径在恰好 2%（102）与刚越过（102.0001）时的 affects_part 边界断言。 |
| `Stages/excel_final/tests/test_pipeline_end_to_end.py` | L339-419（关键断言 370-375） | test_canonical_pipeline_applies_lookup_split_skip_and_report_rules 中 p-box 的 6.28/7.85/25.12/31.4/56.52 | 复杂逻辑 | BOX100*100*10*10 拆板理单重 6.28 来自腹板取宽 100-2*10=80 的推导，25.12+31.40=56.52 是父子重量精确守恒断言，但测试名笼统且全无推导注释，魔法数对维护者不可解释。 | 为 6.28/7.85 各加一行推导注释（10×80×1000×7.85/1e6、10×100×1000×7.85/1e6，数量 4），并注明 sum==56.52 断言的是『拆板子件理论总重守恒于父理论重』。 |
| `Stages/excel_final/tests/test_handbook_repository.py` | L86-99（96-99 冲突分支） | test_lookup_requires_category_spec_and_material_for_d_categories | 测试意图 | round_bar+HRB400、rebar+Q355B 抛 'conflicts' 的断言把『HRB 只路由到螺纹钢、HPB/Q235B/Q355B 只路由到圆钢』的材质族互斥规则压缩成几行裸 pytest.raises，无注释时无法知道验证的是哪条领域不变量（material_routing.py 也无直接单测）。 | 加注释点明这些分支锁定 d_series 材质族互斥（rebar↔round_bar 不得互查），并说明该用例同时充当 material_routing.d_series_category 的间接覆盖。 |

### platform+bootstrap+integrations

| 文件 | 位置 | 元素 | 类型 | 问题 | 建议 |
|---|---|---|---|---|---|
| `backend/app/platform/messaging/celery_app.py` | L1-30 | 模块整体（559 行 Celery 装配/worker 生命周期） | 模块 docstring | 这是整个仓库最复杂的装配模块，内含 MySQL SQL transport 生命周期、worker 信号钩子、心跳线程与任务路由，但没有模块级 docstring 说明当前 transport 事实（RabbitMQ/Outbox/Beat 仅为目标）、注册顺序保证与'Job 状态以 MySQL 为准'的不变量。 | 在文件头补充模块 docstring：说明当前使用 SQLAlchemy MySQL transport（非 RabbitMQ）、worker 装配顺序（schema 准备→清理→ready 回调→marker→心跳）、以及 platform 不 import modules、组合归 bootstrap 的边界。 |
| `backend/app/platform/messaging/celery_app.py` | L34 / 402-415 | WORKER_READY_MARKER 与 DWG_WORKER_QUEUE/DWG_WORKER_CONCURRENCY 环境变量 | 跨模块契约 | readiness marker（/tmp/dwg-celery-ready）只被 compose.yaml 的 healthcheck 消费，队列/并发通过 DWG_WORKER_QUEUE、DWG_WORKER_CONCURRENCY 环境变量注入，但代码内没有任何注释说明'谁消费、何时写入/删除、marker 必须在 ready 回调全部成功后才发布'这一装配契约。 | 在常量与 _worker_queues/_emit_worker_signal 附近注释：marker 由 worker_ready 在回调完成后写入、worker_shutdown 删除，供 compose healthcheck（test -f + /proc/1/cmdline）使用；DWG_WORKER_QUEUE 逗号分隔队列名、DWG_WORKER_CONCURRENCY 默认 1 的语义。 |
| `backend/app/platform/security/tokens.py` | L24-58 | create_access_token / create_refresh_token / decode_token 的 claim 契约 | 跨模块契约 | 平台签发 jti/type/sub 等 claim 但从不校验：decode_token 不验证 type，'access/refresh 区分'与'jti 黑名单吊销'全靠 app.modules.identity 的调用方（dependencies.py、authentication.py、sessions.py）各自实现，签发方与消费方之间的不变量完全未文档化。 | 在模块或 decode_token 处注释：调用方必须自行校验 payload['type']，吊销通过将 jti 写入 modules.identity 的黑名单表实现；sub 为整数用户 id；decode_token 只做签名/过期校验、不区分令牌用途。 |

### cad_processing+dxf_classification+dxf_splitting+excel_processing

| 文件 | 位置 | 元素 | 类型 | 问题 | 建议 |
|---|---|---|---|---|---|
| `backend/app/modules/cad_processing/interface.py` | L1-135 | interface.py 门面（convert_dwg_directory / run_*_conversion / get_or_create_dxf_preview 等全部函数） | 跨模块契约 | CONTEXT.md 定义 Interface 必须文档化调用者须知的不变量、错误模式、顺序和配置约束，但该文件只是零 docstring 的 re-export 转发层：调用者无法得知预览函数会抛 409/413/422 AppHTTPException、batch 参数 (job_id, attempt) 元组语义、enqueue 必须先于 run 的时序等。 | 为每个公开函数补充契约 docstring：异常类型与错误码、参数顺序约束（job_id/attempt、batch 的成对语义）、以及"interface.py 是其他业务模块唯一导入路径"的边界说明。 |
| `backend/app/modules/dxf_classification/adapter.py` | L58-62 | invoke_classifier 的退出码/stderr 契约（returncode not in {0, 2}、成功退出时 stderr 必须为空） | 跨模块契约 | 退出码 2 是 Stage CLI 的"有待复核/不可读"信号（已核对 Stages/steel_dxf_classifier cli.py: _exit_code 在 review_required_count 或 unreadable_count>0 时返回 2），但后端只写"符合 1.2 契约"，未说明 0 与 2 的业务含义及为何禁止任何 stderr。 | 注释退出码语义：0=全部自动通过、2=存在需人工复核或不可读图纸（两者都算正常业务结果而非失败），并说明 stderr 非空即视为契约违例的原因（防止告警信息被误当结构化输出）。 |
| `backend/app/modules/dxf_classification/execution.py` | L345-349 | run_dxf_classification 中 sha256(output_payload) == sha256(source_payload) 校验 | 领域语义 | 该检查强制"分类器不得改写冻结的服务器派生源 DXF、路由产物必须与输入逐字节一致"这一核心不变式，但代码与报错文案（"分类输出字节与来源不一致"）读起来像反了，且无任何注释说明为什么这里相等才是正确的，极易被后续维护者当作笔误删掉。 | 在检查前注释不变式：分类器对冻结 DXF 只做只读分流，输出必须与不可变源逐字节相同（此校验同时证明产物可溯源）；说明若未来 Stage 允许预处理改写，此检查必须同步升级为显式哈希账本。 |
| `backend/app/modules/dxf_splitting/adapter.py` | L195-233 | invoke_splitter 的退出码↔业务路由一致性契约（returncode in {0,1,2}、rc=3 拒绝、batch_failure 顶层条目） | 跨模块契约 | 已核对 Stages/steel_dxf_split cli.py：rc0=全部 auto_accepted、rc1=存在 manual_review 且无失败、rc2=存在 failed、rc3=输入契约/账本发布失败；后端用四段 if 强校验该映射却零注释，且对 summaries 中 batch_failure 顶层条目与 4000 字符 stderr 截断的约定也未说明。 | 在退出码检查处注释三态业务语义与 rc3（批次级失败）为何直接抛错，说明 batch_failure 条目代表整批收尾失败（如 BH拆板信息表生成失败）必须阻断，而非逐图失败。 |
| `backend/app/modules/excel_processing/stage_adapter.py` | L458-522 | _normalize_lookup_request（D 系列材质路由 _D_MATERIAL_CATEGORY_BY_PREFIX、PIP/PD→steel_pipe/square_tube、D 系列必须带 material 等校验） | 跨模块契约 | 该函数逐条复刻 Stage 的五金手册材质路由规则（HRB→rebar、HPB/Q235B/Q355B→round_bar、PIP/PD 走公式不查手册）作为调用契约校验，但没有任何注释说明这些规则与 Stages/excel_final 的 material_routing 是一一镜像、靠跨 seam 测试防止两侧漂移（CONTEXT.md 明确要求）。 | 在 _D_MATERIAL_CATEGORY_BY_PREFIX 与 PIP/PD 正则处注释：此处是 Stage 规则的后端镜像校验，只验证调用契约不承载查询逻辑，任何规则变更必须同步 Stage 与跨 seam 测试，防止两侧漂移。 |

### files 模块

| 文件 | 位置 | 元素 | 类型 | 问题 | 建议 |
|---|---|---|---|---|---|
| `backend/app/modules/files/interface.py` | L1 | 模块 docstring（Public file-registry boundary…） | 跨模块契约 | 作为跨模块公共边界只有一句话，未文档化调用方必须知道的不变量：注册先于/后于字节写入的顺序、跨 MySQL/对象存储的 saga 与补偿语义（rollback 后删除 pending 对象、compensation_required 终态）、幂等冲突 409 错误模式、以及 MySQL 与 SQLite 双路径差异。 | 在模块 docstring 中补充契约段：注册与字节写入的顺序约束、transfer intent 生命周期（prepared→in_progress→succeeded/failed/compensation_required）、补偿触发条件与幂等键冲突错误码。 |

### operations+automation

| 文件 | 位置 | 元素 | 类型 | 问题 | 建议 |
|---|---|---|---|---|---|
| `backend/app/modules/operations/audit/interface.py` | L16 | write_audit_log | 跨模块契约 | 这是唯一受控的跨域审计写入接口，却无 docstring；全部调用方（agent_runs.create、daily_archive.preview、storage.scan_queue 等）与 routes.py 的 action_domain 前缀过滤都隐式依赖 `{domain}.{action}` 点分命名约定，但约定本身、resource_type 取值、before/after_json 语义均未记录（仅有一处无法解析的外部引用 §20.4）。 | 为 write_audit_log 补 docstring：明确 action 必须为 `<domain>.<action>` 点分格式、resource_type 建议取值、before/after_json 为变更前后快照，并解释 §20.4 出处或移除该引用。 |
| `backend/app/modules/automation/agent/routes.py` | L36 | create_agent_run | 领域语义 | 接口返回 202 并写入 status="queued" 的 AgentRun，但 contracts/interface.py 明确 agent_runtime 为 disabled、无任何执行器，这些 run 将永远停留在 queued，且该端点从不调用会话记忆 memory.py；无注释说明这一『持久化占位、永不执行』语义。 | 在路由 docstring 或注释中说明 Stage 1 只落库占位、队列永不消费、与 memory.py 的关系，避免调用方误以为任务会被执行。 |

### scripts+infra（主代理自审补充）

| 文件 | 位置 | 元素 | 类型 | 问题 | 建议 |
|---|---|---|---|---|---|
| `scripts/lib/database.sh` | L696-711 | backup_cmd（mysqldump 选项） | 复杂逻辑 | 生产备份的 `--single-transaction --routines --triggers --events` 与条件式 `--skip-column-statistics` 是备份一致性语义的核心（InnoDB 一致快照、不锁表；MariaDB/MySQL 版本差异兼容），但零注释；`| gzip > outfile` 管道失败时无说明（pipefail 下退出但已生成半截文件）。 | 注释各 dump 选项的含义（--single-transaction 保证一致性快照且不阻塞写入）、--skip-column-statistics 的版本兼容分支、以及备份文件半截/失败时的清理与重试约定。 |
| `scripts/lib/database.sh` | L713-733 | restore_cmd | 错误/补偿路径 | 恢复直接向现有库灌数据、不先重建库：表结构漂移时恢复会中途失败留下半恢复状态；`file | grep gzip` 探测压缩格式是启发式；MYSQL_PWD 明文环境变量的使用也无注释。 | 注释恢复的前置条件（建议先 reset 或在恢复前 DROP/CREATE）、半恢复失败的后果与重试方式，以及 gzip 探测的局限（改为按扩展名+魔数双判）。 |

## 四、Medium 优先级发现

### backend 业务模块 3/4：identity + projects + remnant_inventory

| 文件 | 位置 | 元素 | 类型 | 问题 | 建议 |
|---|---|---|---|---|---|
| `backend/app/modules/remnant_inventory/execution.py` | L1 | execution.py 模块 | 模块 docstring | 521 行的转换/解析执行引擎（dispatch 失败结算、attempt fencing、批量转换与单条解析）没有任何模块级说明，而这是余料导入流水线的核心状态机。 | 在文件头部写模块 docstring：本文件负责 attempt-fenced 的 DWG→DXF 转换与 DXF 解析执行，说明 prepare/dispatch/settle 三个阶段、Celery 任务只触发执行而 MySQL 是事实源、失败补偿如何通过 _mark_item_failed + _fail_active_job + recalculate_batch_counters 收敛。 |
| `backend/app/modules/remnant_inventory/imports.py` | L1 | imports.py 模块 | 模块 docstring | 621 行的导入账本（登记/校正/重试/取消/确认）承载了 CONTEXT.md 的'文件登记与流转'领域概念，但没有模块级说明，文件内也没有任何关于批次/条目状态机的总览。 | 添加模块 docstring：说明 import batch/item 是余料导入账本，状态流转 uploaded→converting→parsing→pending_confirmation→confirmed/cancelled/failed、attempt 世代与重试语义、REMNANT_* 错误码与中文消息的分工（码是稳定契约、文本面向工人）。 |
| `backend/app/modules/remnant_inventory/imports.py` | L300-322 | retry_import_item | 复杂逻辑 | 重试时 attempt += 1、状态重置为 uploaded、清空两个 job_id，且只有 .dwg 源会清空 dxf_file_id（.dxf 源沿用原文件）——这些世代与输入语义没有任何注释，失败路径的后果（旧任务 fencing 失效）不直观。 | 添加 docstring：解释 attempt 递增即新世代，旧代任务因 status/attempt 条件不匹配而无法写入；.dwg 重试必须清空派生的 dxf_file_id（需重新转换），而 .dxf 源文件仍是有效解析输入故保留；永久性错误（_PERMANENT_IMPORT_ERRORS）禁止重试的原因。 |
| `backend/app/modules/remnant_inventory/imports.py` | L357-406 | cancel_import_batch | 错误/补偿路径 | 整批取消会通过 soft_delete_file_in_transaction 同时删除源文件与 DXF 的文件登记（补偿文件流转账本），而 cancel_import_item（L325-354）只取消 Job 不删文件；这一不对称的补偿语义没有任何注释，调用者无法知道取消整批的后果。 | 注释说明：整批取消是一次性放弃该批所有未确认文件，故同步软删源文件与派生 DXF 的文件登记以保持账本干净；单条取消保留文件登记以便用户重新核对，两者是有意差异而非疏漏；已确认条目跳过不受影响。 |
| `backend/app/modules/remnant_inventory/inventory.py` | L1 | inventory.py 模块 | 模块 docstring | 522 行实现正式余料生命周期状态机（available→reserved→used、归档、删除）与乐观并发（version 列），但没有模块级说明，状态机与 version 计数器的整体约定无处可查。 | 添加模块 docstring：声明正式余料生命周期与 README 中 available→reserved→used 的流转、预占可取消回 available、归档/删除约束，以及 version 列是每次状态或字段变更递增的乐观锁计数器（reserve 依赖它做 CAS）。 |
| `backend/app/modules/remnant_inventory/inventory.py` | L226-273 | reserve_remnant | 跨模块契约 | reserve 是跨 HTTP 层的乐观并发契约：调用者必须传入其最后观察到的 version，否则得到 REMNANT_STATE_CONFLICT 且需重取；函数无 docstring，契约只能从签名和 409 分支反推（L244-246 的 REPEATABLE READ 注释只覆盖了竞争分支）。 | 为函数补 docstring：说明 expected_version 必须来自客户端最近一次读取的 Remnant.version，条件 UPDATE 命中 0 行时的三类后果（404 / 已被他人预留 409 REMNANT_ALREADY_RESERVED / 版本过期 409 REMNANT_STATE_CONFLICT），以及审计 before.version 记录的是期望值而非实际值。 |
| `backend/app/modules/remnant_inventory/models.py` | L158 | Remnant.version | 领域语义 | version 列是库存行乐观锁计数器的唯一实现，所有状态迁移都对其 +1，但模型上没有任何注释说明其用途，建模者容易误以为是业务版本号或删除它。 | 在字段上加注释：version 是并发控制计数器，每次状态/字段变更递增，reserve/update 等操作依赖它做条件更新与冲突检测，不是业务余料版本。 |
| `backend/app/modules/remnant_inventory/materials.py` | L85-140 | resolve_or_create_auto_material | 领域语义 | 自动导入路径会静默重新启用被管理员停用的材质（返回 reenabled 并写 auto_enable 审计），而同文件 resolve_or_create_material（L39-82）对停用材质一律 409——两条解析路径的策略差异与授权依据完全没有注释，容易被视为越权重新启用。 | 添加 docstring：说明本函数仅供服务端自动导入流水线（actor 为 batch 创建者）使用，停用材质在图纸中重新出现时按'自动建档即启用'策略处理并写 remnants.material.auto_enable 审计；工人确认路径必须走 resolve_or_create_material，它拒绝停用材质以防绕过管理状态，两条路径的差异是刻意设计。 |
| `backend/app/modules/remnant_inventory/schemas.py` | L152-153 | RemnantReserveRequest.version | 跨模块契约 | 该字段是前端与后端之间的乐观并发契约（客户端必须提交其最后看到的 version，过期则 409），但只有 Field(ge=1) 没有任何 description，OpenAPI 消费者无法得知冲突语义。 | 为 version 字段添加 description：'调用方最近一次读取到的余料版本号；若已被其他操作修改则返回 409 REMNANT_STATE_CONFLICT，需重新读取后再试'。 |
| `backend/app/modules/remnant_inventory/routes.py` | L1 | routes.py 模块 | 模块 docstring | 1138 行的 HTTP 面（功能开关依赖、批量导入两阶段、bulk 确认/归档、导出清理）没有模块 docstring，各 router 的依赖（_enabled 功能开关）与提交后派发（commit-then-dispatch）的顺序约定没有总览。 | 添加模块 docstring：说明四个 router 均受 remnant_inventory_enabled 开关保护、上传→登记→prepare→commit→dispatch_import_execution 的两阶段模式（必须先 commit 让 Celery worker 看到已登记行，dispatch 失败由 _settle_dispatch_failure 补偿）。 |
| `backend/app/modules/projects/services/drawings.py` | L54-76 | create_drawing_version | 复杂逻辑 | version_no 用 max+1 递增且无行锁、无唯一约束、无注释；README 声称'图纸版本号由事务内当前最大版本递增'是不变量，但并发创建版本时两个事务可能算出相同 version_no，失败路径无人说明。 | 注释说明版本号递增依赖的并发假设（单事务内计算+插入，靠 MySQL 默认隔离级别下的一致性读），指出未加锁时的竞争窗口，以及是否依赖 drawing 行锁（create_version 路由未 with_for_update）需明示，避免维护者误以为有唯一约束兜底。 |
| `backend/app/modules/projects/schemas/project.py` | L21-46 | ProjectUpdate.status / ProjectMemberCreate.project_role | 跨模块契约 | status 与 project_role 都是自由字符串（max_length 32/64），合法取值（active/deleted；project_owner/project_engineer）只散落在 routes 的集合常量里，调用方可写入任意值，契约没有文档化。 | 为字段添加 description 或 Literal 约束：status 仅 active/deleted，project_role 仅 project_owner/project_engineer（与 PROJECT_WRITE_ROLES/PROJECT_OWNER_ROLES 保持一致），说明项目成员策略依赖这两个角色集合判定写入与删除权限。 |
| `backend/app/modules/projects/access.py` | L1-52 | projects/access.py 模块与 require_project_member/require_project_role | 跨模块契约 | 该模块通过 projects/interface.py 成为其他领域（files/jobs/workflows）访问项目范围的唯一稳定边界，但无模块 docstring，且 404（项目不存在/已删除）与 403（非成员/角色不符）的区分、admin 旁路返回 None 的约定都没有注释，跨模块调用者无法预知错误模式。 | 在模块头与各函数补 docstring：admin/super_admin 直接放行（返回 None 表示'无成员行但已授权'）；require_active_project 对不存在或已删除项目抛 404（不泄露项目存在性）；成员检查失败抛 403；require_project_role 在成员角色不在 allowed 集合时抛 403。 |

### backend/tests/ 与 tests/（后端测试与验证脚本）

| 文件 | 位置 | 元素 | 类型 | 问题 | 建议 |
|---|---|---|---|---|---|
| `backend/tests/workflows/test_workflow_production.py` | L973-974 | test_excel_stage2_job_params_stay_small_for_5000_bh_inputs | 魔法数字 | 断言 params_json 序列化后 < 4096 字节，但未说明 4096 对应哪个生产约束（MySQL 列宽/消息上限）以及超限的失败路径，5000 也用了裸数字而非生产常量。 | 注释说明 4096 字节上限的来源（如 params_json 存储列约束）与超限后果，并引用 MAX_BH_STAGE2_INPUTS=5000 常量，避免生产调整后测试失同步。 |
| `backend/tests/workflows/test_workflow_input_api.py` | L149-158, 264 | test_input_folder_manifest_accepts_5000_dwg_files / rejects_more_than_5000_files | 魔法数字 | 裸数字 5000 出现多次（本文件、dxf_classification 的 MAX_BH_STAGE2_INPUTS=5000、excel_stage2 的 5000 inputs），但测试没有引用生产常量 MAX_INPUT_DWG_FILES=5000，也没有说明该上限存在的理由（multipart 数量与清单体积受控），边界值漂移时测试与实现会悄然脱节。 | 改为引用 app.modules.workflows.intake.registration.MAX_INPUT_DWG_FILES（及对应分类/Stage2 常量），并加一行注释说明 5000 是输入批次的上限契约而非任意值。 |
| `backend/tests/dxf_splitting/test_dxf_splitting_pipeline.py` | L1402-1441 | test_platform_records_one_quantity_checkpoint_per_thirty_drawings | 魔法数字 | 30 张/检查点的窗口以及 24/5/1 的结果计数都是 Steel DXF Split 1.5.2 外部 Stage 的数量检查点契约（56 张 → [30,26]），测试硬编码这些数字却没有说明不变量来源，未来外部版本变更时无法判断是测试错还是契约漂移。 | 注释说明每 30 张一个 quantity checkpoint 是外部 Stage CLI 的合同约束，56 的期望输出验证窗口切分与累计结果的推导，而非随意选取的数值。 |
| `backend/tests/excel_processing/test_excel_final_adapter.py` | L430-444 | test_backend_and_stage_share_one_d_series_material_routing_contract | 领域语义 | 断言 HRB→rebar、HPB/Q235B/Q355B→round_bar 映射与 Stage 侧一致，但未说明业务规则内容（CONTEXT.md 五金手册材质路由：HRB 查螺纹钢、HPB/Q235B/Q355B 查圆钢，其他材质不得跨类别借用重量），也未说明该测试防的是后端 Adapter 与 Stage 规则两侧漂移。 | 在断言上方注释该材质路由业务规则及其含义（只定查询类别、不代表手册命中），并说明这是跨 seam 防漂移测试，两侧任一改动都会在此失败。 |
| `backend/tests/jobs/test_job_outbox.py` | L136-141 | test_retry_delay_is_jittered_and_bounded | 魔法数字 | 0.5–1.0 与 15.0–30.0 的边界断言没有说明背后退避公式（指数退避加抖动、封顶 30s）与意图（控制 outbox 重取节奏、防止租约风暴），读者无法判断边界是否与被测实现一致。 | 注释说明 retry_delay 的公式（如 0.5×2^n 封顶 30 秒并加抖动）以及该契约保护的场景（发布失败后的重试节奏与租约竞争）。 |
| `backend/tests/workflows/test_workflow_input_service.py` | L697-800 | test_freeze_* 系列（输入冻结测试组） | 领域语义 | freeze 测试组逐条验证了 CONTEXT.md 输入冻结契约（manifest sha256 幂等、冻结后源文件不可原位替换、drawing_id 关联派生 DXF、冻结前不可手工完成 source_intake），但没有任何注释把断言映射到该领域概念，新读者难以看出这些测试共同守护的是同一个业务不变量。 | 在该测试组开头加一段注释：输入冻结的领域含义（固定文件集合与清单哈希、冻结后修改必须形成新版本），并说明 drawing_id 关联断言守护的是『内部流程不得只靠文件名维持关系』。 |
| `backend/tests/remnant_inventory/test_api.py` | L76-145 | _seed_global_remnants | 复杂逻辑 | 约 70 行的种子矩阵（2 种材质 × 4 条不同状态余料、批次/零件/项目绑定、第二行带额外业务字段）没有 docstring，后续测试靠状态名查询具体行，读者无法知道每行存在的目的以及哪些测试依赖哪一行。 | 添加 docstring：说明 4 条余料覆盖 available/reserved/used/archived 状态矩阵、第 1 行携带副项目号/库位/备注等扩展字段，供哪些测试按状态取用。 |
| `tests/run_full_verify.py` | L21-64 | Check / request_json / envelope_status | 公开 docstring | 作为仓库指定的非破坏性验证入口，其 helper 未文档化 API envelope 契约（data.status 取状态、非 2xx 时 body 仍按 JSON 解析、认证失败即提前返回），默认超时 10s 与 status.sh 的 30s 也是裸数字，无法判断检查之间的顺序依赖（认证必须先于带 token 的列表检查）。 | 为 request_json/envelope_status 补 docstring：说明 envelope 约定、错误路径行为与超时取值理由，并注释 checks 的编排顺序依赖与『认证失败即短路』的语义。 |

### frontend React 前端 (frontend/src/)

| 文件 | 位置 | 元素 | 类型 | 问题 | 建议 |
|---|---|---|---|---|---|
| `frontend/src/features/workflows/workflow.ts` | L342-350 | WorkflowRetentionStatus | 跨模块契约 | WorkflowRetentionControl 整个 UI（轮询、purge 按钮可用性、失败重试提示）都依赖该状态机的转移关系，但类型文件未说明 allowed transitions 与各失败态（download_failed/purge_failed）的补偿语义。 | 注释状态机：prepared→downloading→downloaded→purge_queued→purging→purged，以及 download_failed/purge_failed 时文件仍保留、可安全重试的约定。 |
| `frontend/src/features/workflows/DrawingProcessingPanel.tsx` | L193-207 | notifiedTerminalRun 去重 key (`${run.id}:${run.job.attempt}:${run.status}`) | 领域语义 | key 刻意包含 job.attempt，保证同一 run 跨世代重跑时每个 attempt 只通知一次；这是 Attempt 领域语义的编码，但没有注释说明该设计意图与失效条件。 | 注释：通知按 (run, attempt, status) 去重，避免轮询重复触发 invalidateQueries/onChanged，且新 attempt 开始后旧 attempt 的 terminal 帧不会重复通知。 |
| `frontend/src/features/workflows/DrawingProcessingPanel.tsx` | L43-162 | useNativeWorkflowDownload 状态机 (created/prepared/downloading/downloaded/download_failed + launchFailed) | 复杂逻辑 | 这是导出下载的核心状态机：launch 后浏览器直收 ZIP 与服务器状态轮询并行，失败可重试、取消后文件保留，逻辑含多处时序（launch 后 300/500ms 才 refetch 状态）；只有 useCallback 身份问题有注释，整体缺一张“为什么这样设计”的说明。 | 在 hook 顶部注释状态转移：服务器先 prepared（可轮询）再开始传 ZIP；launchFailed 表示浏览器侧失败需重试；launch 后延迟 refetch 是为了让服务器先落 downloaded 状态；cancel 只停浏览器侧、服务器文件保留。 |
| `frontend/src/features/excel-processing/ExcelFinalPage.tsx` | L160-195, 421-424 | selectedRequestKey 生命周期（选文件时生成、失败后保留、成功才清空） | 领域语义 | requestKey 是幂等键：同一文件重复提交复用同一 key 由服务器去重，换文件才重新生成；代码行为正确但没有任何注释，后续维护者可能改成“每次提交都生成新 key”而破坏去重。 | 在 submit/onSelect 处注释幂等契约：key 绑定一次文件选择，提交失败保留 key 使重试被服务器去重；更换文件必须重新生成，否则会误合并为同一请求。 |
| `frontend/src/features/excel-processing/ExcelPreview.tsx` | L41-68 | loadFast / handleSheetChange 快速切表 | 错误/补偿路径 | 切 sheet 时前一个请求可能仍在飞行中，后到响应会覆盖新 sheet 数据（last-write-wins），且没有 AbortController 或请求序号保护，属于真实竞态。 | 注释并修复：记录请求序号或 AbortController，仅应用最新一次请求的响应；说明快速切表时旧响应必须被丢弃，否则展示错误的 sheet 数据。 |
| `frontend/src/features/cad-processing/hooks/useConversionEvents.ts` | L5 | MAX_STREAM_FILES = 200 | 魔法数字 | 按 200 个 file_id 切分 SSE 流的原因（GET query 长度/后端上限）未说明，未来改动可能破坏 URL 长度限制。 | 注释 200 的来源：单个 EventSource 的 file_ids query 参数受后端/URL 长度约束，超过则需切分多个流并各自监听。 |
| `frontend/src/features/cad-processing/conversionState.ts` | L12-16 | isStuckJob 的 60_000ms 阈值 | 魔法数字 | queued 且 progress=0 超过 60 秒即判定卡死并允许用户重新提交；阈值选择与“服务器端 job 可能仍存活”的副作用没有注释，用户重提可能产生重复任务。 | 注释：60s 是 ODA 队列在前端可见的最小卡死判定窗口；重新提交前页面应提示原任务可能仍在队列，服务端靠幂等/attempt 收敛。 |
| `frontend/src/features/remnant-inventory/api.ts` | L132-138 | reserveRemnant 携带 version 提交 | 跨模块契约 | version 是乐观并发锁：他人已修改时提交会 409，调用方必须刷新后重试；契约处未说明该错误模式，误用会覆盖他人修改。 | 注释：reserve 必须回传读到的 version 做乐观锁校验，version 过期时后端返回 409，调用方应刷新余料后再操作。 |
| `frontend/src/features/files/files.api.ts` | L156-174 | downloadFile 重试循环 (2 次、500ms 退避、403 可重试) | 错误/补偿路径 | 403 被纳入可重试是因为短时签名 URL 过期，每次重试重新取签名；但重试次数、退避与‘401 不重试（由拦截器处理）’的分工没有完整注释，调用方无法判断失败路径。 | 注释重试策略：签名 URL 可能刚生成即过期，故 403/408/429/5xx 重试一次并重新取签名；401 交由全局拦截器刷新会话，不在此重试。 |
| `frontend/src/features/jobs/job.ts` | L1-23 | Job 接口 (attempt / result_available / status 取值) | 跨模块契约 | Job 被 files/cad-processing/workflows/excel-processing 跨模块消费：attempt 是 CONTEXT.md 的世代概念，result_available=false 表示结果已被释放可重新提交，但类型零注释，调用方只能靠猜测。 | 为 Job 加契约 docstring：attempt 为执行代次（旧 attempt 不覆盖新状态）；result_available=false 表示结果已释放、文件可重新提交；status 常见取值及 terminal 集合。 |

### Stages 1/2: 拆板与分类算法（steel_dxf_split_v1.5.2 / steel_dxf_classifier_v1.1.0 / bh_left_right_reader / BOX左右进读取）

| 文件 | 位置 | 元素 | 类型 | 问题 | 建议 |
|---|---|---|---|---|---|
| `Stages/steel_dxf_split_v1.5.2/src/steel_dxf_split/bh_associations.py` | L1-60（模块头） | DrawingGraph / DrawingEdge / AssociationStrength 词汇表 | 跨模块契约 | 该模块产出的 drawing graph 是 compiler/annotation/constraints 共用的跨模块契约（DrawingEdgeKind、rule_id、residual_mm、strength 语义），但模块无 docstring，调用方无法从代码得知节点/边的稳定不变量（如 canonical 成员坐标系、source_ids 排序、边唯一性、residual_mm 的量纲）。 | 加模块 docstring：说明图建立在 canonical member 坐标系、节点/边 ID 由 canonical_sha256 派生故内容变更即 ID 变更、rule_id 是稳定诊断键、AssociationStrength 的判定边界，以及下游消费方应遵守的只读约定。 |
| `Stages/steel_dxf_split_v1.5.2/src/steel_dxf_split/bh_geometry.py` | L485-568 | _reconstruct_proven_rectangular_projection | 魔法数字 | 端点容差 0.15mm、矩形填充比门槛 0.985、fidelity_tolerance 1e-7、near_full_flange_width 的 0.80/1.20 带宽都是决定'是否把投影纠成矩形'的几何门限，直接改变输出的板件轮廓，但没有解释数值来源与误判代价。 | 注明 0.985 填充比与 0.15mm 端点容差分别防什么（避免把真实斜端/倒角轮廓强行矩形化）、0.80/1.20 带宽如何与制造翼缘宽度公差对应，以及门槛不满足时保守保留原轮廓的原因。 |
| `Stages/steel_dxf_split_v1.5.2/src/steel_dxf_split/bh_constraints.py` | L1352-1356 | _repair_complexity 惩罚系数 | 魔法数字 | grid_penalty=min(0.40, grid/0.25*0.20) 与 repair_penalty=min(0.45, count*0.065) 是纯调参系数，决定备选解释的排序质量分，虽不影响证明义务判定但影响 hypothesis 择优，数值依据无任何说明。 | 注释每个系数的作用（0.20/0.25 是网格惩罚斜率与归一化点、0.065/0.45 是干预项上限），说明排序分只用于'择优'不用于'放行'，并记录这些数值校准过的回归集合。 |
| `Stages/steel_dxf_split_v1.5.2/src/steel_dxf_split/bh_fingerprint.py` | L17, 207 | FLOAT_DIGITS=6 / grid_mm=1e-6 | 魔法数字 | 指纹的浮点规范化精度（6 位小数、1e-6 网格）决定跨版本/跨平台哈希稳定性契约：什么量级的几何差异会让同一构件的制造指纹变化，从而破坏下游一致性校验，但未说明 6 位的选取理由。 | 注明 6 位小数对应 0.001mm 的规范化精度、为何低于 DXF 双精度（吸收子微米拓扑噪声）又高于制造公差（不吞真实差异），以及指纹契约承诺的是'相同制造解释必等值、不同解释不等值'。 |
| `Stages/steel_dxf_split_v1.5.2/src/steel_dxf_split/bh_pipeline.py` | L247, 298-302 | split_bh_dxf 的 require_auto_accept 参数 | 跨模块契约 | BH 路径接收 require_auto_accept 但只写入报告、从不强制执行（route 仅由 disposition 决定，L298），而 BOX 路径在 box/delivery.py L162 真正强制；同一参数两种语义且无注释，调用方无法从签名得知 BH 侧当前只是记录。 | 在参数与路由处注释：BH 侧该标志当前仅为审计记录、路由仍由 proof disposition 决定（fail-closed），若需与 BOX 对齐强制执行需显式改造；或从 BH 签名移除该参数避免误导。 |
| `Stages/steel_dxf_split_v1.5.2/src/steel_dxf_split/bh_extractor.py` | L102-154 | _edge_view_symbol_centers | 魔法数字 | Tekla 三笔式侧视螺栓符号识别依赖一组无解释的几何门限：平行度 0.05、间隙对称 max(1,0.2*gap)、长度对称 0.15、中笔长度 0.08、切向散布 0.5、去重距离 1.0mm——误识别会把中心十字误当孔或漏孔，直接影响孔洞归属。 | 逐条注释各门限防什么（如 0.2 间隙比排除不等距箭头、0.08 中笔要求区分中心短划），并注明这些数值来自 Tekla 出图规范还是回归样本。 |
| `Stages/steel_dxf_classifier_v1.1.0/src/steel_dxf_classifier/batch.py` | L118-142 | _promote | 错误/补偿路径 | staging→正式目录的提权带备份-部分回滚逻辑（备份旧输出→逐个 os.replace→失败时删除已提权项并还原备份），零注释；崩溃窗口（无 fsync、backup 为单目录）与回滚不完整时的后果对维护者不可见。 | 补充注释：说明提权前旧结果被整体移入 backup 的意义（保证正式目录要么全新要么全旧）、异常时先删已提权目录再还原的顺序、以及崩溃后残留 .backup 目录的识别与人工恢复指引。 |
| `Stages/steel_dxf_classifier_v1.1.0/src/steel_dxf_classifier/title_block.py` | L39, 62-80 | _title_region_labels / find_title_candidates 配对启发式 | 魔法数字 | 标题栏区域定位（relative_x>=0.55 或 relative_y>=0.65）与标签-取值配对门限（下方 30*scale/0.2*page_height、右侧 60*scale、横向 8*scale、纵向 3*scale）全是未解释的几何魔法数，直接影响分类证据的召回与误配。 | 注明 0.55/0.65 是'右上信息表'的工程约定、各倍数门限（30/60/8/3 倍字高）对应 Tekla 标题栏排版间距的由来，以及门限放太宽/太窄分别造成 TITLE_VALUE_CONFLICT 还是漏检。 |
| `Stages/bh_left_right_reader/src/bh_reader/analyzer.py` | L1820-1837 | _measure 的置信度加权公式 | 魔法数字 | 置信度由 0.66 基准 + 0.14*exp(-depth_err/ratio) + 0.08*depth_support + 0.10*web_confidence + 0.08*exp(-len_err/0.10) + 0.05/0.04 等权重拼出，各权重与 0.66/0.10 分母无任何来源说明，而该置信度决定输出是否低于 minimum_confidence_to_emit 而被拒绝。 | 注释每个权重的含义（深度拟合、腹板证据、长度校验、俯视图佐证）与数值校准依据（199 张回归图的置信度分布），说明 0.72 输出门槛与权重的关系及调参时如何验证。 |
| `Stages/steel_dxf_split_v1.5.2/src/steel_dxf_split/box/projection_geometry.py` | L2956-2999 | source face 子集搜索的 maximum_states 预算 | 复杂逻辑 | 子集枚举在达到 maximum_states=50_000 时置 state_budget_exhausted，使 subset_search_complete=False 并最终传导到 proof 的 search_complete=False → 整体 REJECTED（fail-closed），但该'预算耗尽即整图拒收'的后果链在此处没有任何注释。 | 在 state_budget_exhausted 赋值处注明：预算耗尽不是可选优化而是证据完整性问题，会使搜索不完整并最终把本图路由到拒收/复核，防止后人把 budget 调大当作性能开关而破坏 fail-closed 语义。 |

### Stages 2/2：转换与 Excel 处理（dwg2dxf / dxf2dwg / dxf2excel / excel_final / remnant_drawing_reader）

| 文件 | 位置 | 元素 | 类型 | 问题 | 建议 |
|---|---|---|---|---|---|
| `Stages/remnant_drawing_reader/src/remnant_drawing_reader/classifier.py` | L7-26 | _PART_TOKEN / _MATERIAL_TOKEN / _NON_PART_PREFIXES / _MAX_PROJECT_LENGTH | 魔法数字 | 文件无模块 docstring，且候选提取完全由未解释的正则与魔法常量驱动：_PART_TOKEN 要求'同时含字母和数字且带连字符链'、_MAX_PROJECT_LENGTH=128、_SHORT_CHINESE_ANNOTATION 限 2-3 个汉字、_NON_PART_PREFIXES 排除 DATE/DWG/ISO/REV——这些是'什么算零件号/项目标题'的领域判定，失败路径（误把日期/文件名当候选）没有说明。 | 为模块和每个正则/常量添加 docstring：解释零件号形态（如 SKG-D-4GZ-7）、128 字符上限的用途（避免把整段文字当项目号）、2-3 字中文注释排除规则，以及 DATE/DWG/ISO/REV 前缀防误判的原因。 |
| `Stages/remnant_drawing_reader/src/remnant_drawing_reader/reader.py` | L57-81 | _walk_insert / anomalies 列表参数 | 复杂逻辑 | 模块无 docstring，且 _walk_insert 用可变 list[bool] 作为生成器内的跨层异常标志（anomalies[0]=True），读取方 read_evidence 依赖该技巧判断 STRUCTURE_ANOMALY，但没有任何注释解释为什么用列表而非返回值、哪些异常会被吞掉。 | 补充模块 docstring（从 DXF 提取 Evidence 的用途与证据字段），并在 _walk_insert 上注释 anomalies 标志的用途：遍历 virtual_entities/attribs 时单个实体损坏不中断整体，只记录结构异常；说明嵌套 INSERT 展开与块路径语义。 |
| `Stages/dwg2dxf/src/dwg_converter/framework.py` | L91-100 | health_check 错误码分支 | 错误/补偿路径 | health_check 计算了 xvfb_msgs 但从未使用，两个分支（有 oda 消息 / 无 oda 消息）都返回 ODA_NOT_FOUND，ERROR_CODES 里定义了 XVFB_NOT_FOUND 却永远不会被发出——调用方（FastAPI 5xx 映射）无法区分'缺 ODA'与'缺 xvfb'两种环境错误。 | 修正分支：检测到 xvfb 缺失时返回 ERROR_CODES['XVFB_NOT_FOUND']（或删除死代码并注释为何统一归为 ODA_NOT_FOUND），并注释每种错误码对应的 HTTP 映射意图。 |
| `Stages/dxf2excel/src/dxf2excel/grid.py` | L289-306 | estimate_data_columns | 复杂逻辑 | docstring 声称'窄于中位列宽 10% 的列为分隔列'，但实现用的是 median_w * 0.15（15%），与代码和 README（15%）都不一致——文档与实现漂移，维护者会按错误阈值理解分隔列过滤行为。 | 把 docstring 的 10% 改为 15%（或把 0.15 提取为命名常量并注明'分隔列阈值，来自 SKG 表格数量/单重间的窄分隔线实测'）。 |
| `Stages/dxf2excel/src/dxf2excel/grid.py` | L275-286 | _adaptive_row_height_min 回退分支 | 复杂逻辑 | 无文字高度时收集并排序了 h_lengths，注释声称'行高≈水平跨度 0.05-0.2'，但收集的值从未被使用，两个分支都无条件返回 ROW_HEIGHT_MIN——是死代码/误导性注释，读者会以为实现了基于线长的自适应。 | 要么真正实现基于 h_lengths 的启发式（如 median*系数），要么删除该分支并注释'无文字高度时直接回退固定 ROW_HEIGHT_MIN，不做线长推断'。 |
| `Stages/dxf2excel/src/dxf2excel/grid.py` | L55-64 | classify_line 与 candidate._adaptive_tolerance 的线段阈值 | 魔法数字 | H/V/D 分类的 0.1 阈值、candidate.py 中水平线判定 dy<0.1 且 dx>0.5、以及 1.0<dx<200 的长度带宽，三处用了不同的魔法阈值却没有一处注释来源——CAD 坐标噪声量级是这些阈值成立的依据，改动会直接影响网格恢复。 | 把 0.1/0.5/200 提取为命名常量并注释依据（CAD 端点噪声 0.01-0.5 单位、水平线段典型长度 5-170），说明三个位置阈值为何不同。 |
| `Stages/dxf2excel/src/dxf2excel/grid.py` | L137-147 | recover_grid 回退条件与评分口径 | 魔法数字 | grid_score<0.3 与列数不在 [8,12] 就整体回退 TEXT 聚类的阈值无注释；且 _compute_grid_score_quick 用 '10 边界'（abs(n_cols-10)/5）而 compute_grid_score 用 '9 数据列'（abs(n_cols-9)/5），同一文件两套口径（边界数 vs 数据列数）无说明，容易误改。 | 注释 0.3 与 [8,12] 的来源（9-10 列材料表实测范围、低质量网格回退策略），并注明两处评分一个按边界数(10)一个按数据列数(9)计分的约定差异。 |
| `Stages/excel_final/spec_parser.py` | L155-161, 203-209 | classify_normalized_spec 的 '6*30' 特例 | 魔法数字 | thickness==6 且 width==30 时强制走 FLAT_STEEL 手册而不是 PLATE_CONSTANT，该特例在两处重复出现且零注释——读者无法知道这是'6×30 是标准扁钢规格，手册有此键'还是别的历史约定。 | 把 6*30 特例提取为命名常量并注释'6×30 为手册 flat_steel 表收录的标准扁钢，故优先查手册而非按裸板常量 7.85 计算'；同时消除两处重复。 |
| `Stages/excel_final/weights.py` | L18-21 | SOURCE_CHAIN_TOLERANCE / PASS_RELATIVE_TOLERANCE 等常量 | 魔法数字 | 0.1/0.01/0.005/0.02 四个容差常量只有名字没有依据：为什么 0.5% 内算 PASS、2% 内算 WARNING、超 2% 为 SEVERE，以及绝对 0.01 的舍入容忍——这些对应 README 的'标准型材超 2% 仍为严重，几何偏差只提示复核'规则，但代码现场无对应说明。 | 在每个常量旁注释其领域含义（源单重×数量链的绝对容差、四舍五入保护、人工复核/硬隔离阈值），并引用 README 的核验规则。 |
| `Stages/excel_final/weights.py` | L157-163 | _theory_to_gross_issue_level | 复杂逻辑 | 函数无 docstring：GEOMETRY 依据时把 SEVERE 降级为 WARNING（'几何偏差只提示复核'），HANDBOOK 依据保持 SEVERE——这是 README 强调的核验口径差异，实现里只有一行 if 无解释。 | 添加 docstring：几何理论重与源毛重的偏差只提示复核（不隔离 part），手册理论重偏差超过 2% 仍为严重（硬隔离），并说明这样区分的原因。 |
| `Stages/excel_final/splitter.py` | L123-134 | split_parent 重量守恒校验 | 复杂逻辑 | child_theory != parent.theoretical_unit_weight_unrounded 用 Decimal 精确相等判定守恒（无任何容差），失败则整份拆板被 SEVERE 隔离；为什么不用容差、两个 Decimal 由 7.85 密度计算路径是否可能因舍入恰好不等、失败后果是什么，均无注释。 | 注释'子板理重×块数之和必须与父理论重精确守恒（无容差，防止中间舍入掩盖拆板错误）'，并说明失败时整份拆板被拒并生成 _weight_conservation_issue 的后果。 |
| `Stages/excel_final/handbook.py` | L230-237 | _decimal_weight 的 2000 上限 | 魔法数字 | 查询结果重量 >2000 直接抛 HandbookInfrastructureError，2000 kg/m 这个物理上限没有注释——它防止脏数据被当作命中，但读者无法知道阈值依据（可能是型材理论重物理上限）。 | 把 2000 提取为命名常量（如 MAX_PLAUSIBLE_WEIGHT_KG_PER_M）并注释其用途：拦截手册脏数据行，视为基础设施错误而非查无。 |
| `Stages/excel_final/part_builder.py` | L203-305 | build_part_rows 聚合与冲突检测 | 复杂逻辑 | 核心 part 准入逻辑（按 identity=(构件号,零件号,模型长度,左右进) 检测'同零件多组几何'冲突、板材/扁钢清空构件号后按完整属性跨构件汇总、excluded 行的剔除）全部无内联注释，只有一行模块 docstring；这些规则对应 README 的 part 投影语义，代码现场完全不可自解释。 | 为 build_part_rows 的关键步骤添加注释：identity 签名与 _parameterized_identity 的含义、冲突即 SEVERE 隔离的理由、GLOBAL_SCOPED_TYPES 汇总时构件号置空的原因、summary 累加 child_quantity×component_quantity 的口径。 |
| `Stages/excel_final/quality.py` | L124-205 | QualityLedger.report_rows 分组聚合 | 复杂逻辑 | 处理报告的分组规则（同位置严重问题覆盖次要警告、几何理论重按方向聚合且显示最大相对偏差、手册偏差按规格保留、_REPRESENTATIVE_LIMIT=3 截断）是 README 明确的人工处置规则，但实现里复杂的分组 key 构造与聚合分支完全没有注释。 | 为每个分组分支注释其对应规则：WARNING 被同位置 SEVERE 覆盖的条件、'几何理论重与毛重'按方向/最大偏差聚合的原因、'手册理论重与毛重'按规格保留以便人工确认标准版本，以及 3 条代表说明的截断策略。 |
| `Stages/excel_final/canonical_pipeline.py` | L610-637 | _apply_final_issue_status | 复杂逻辑 | 该函数实现'重量核验'列的降级逻辑（来源行有 SEVERE 或构件被隔离 → 显示'严重'；只有 WARNING 且当前为'通过'才降为'警告'），无 docstring 无注释，'warning 不覆盖已有 severe/警告'的优先级读者无法看出。 | 添加 docstring 与注释：同源严重问题覆盖次要警告的显示规则、构件级隔离（构件编号冲突/构件物理量非法）对整构件行的传播，以及为何 WARNING 不覆盖已非'通过'的行。 |

### jobs+workflows

| 文件 | 位置 | 元素 | 类型 | 问题 | 建议 |
|---|---|---|---|---|---|
| `backend/app/modules/jobs/outbox.py` | L187-190 | retry_delay（指数退避+抖动） | 魔法数字 | 30.0 上限、0.5 基数、指数上限 6、`ceiling/2 ± ceiling/2` 的均匀抖动全部是裸魔法数，没有任何解释说明退避曲线与上限的选择依据。 | 注释说明：指数从 2^attempt 增长但封顶 30 秒（避免长时间阻塞投递），使用 equal jitter（ceiling/2 中值 ± ceiling/2）而非 full jitter 以保证失败组尽快重试，以及 attempt 上限 6 对应约 32 秒封顶的原因。 |
| `backend/app/modules/jobs/lifecycle.py` | L1 | 模块（guarded 状态机核心） | 模块 docstring | 这是整个 fencing 语义的核心文件（attempt 守卫的 conditional UPDATE、MySQL 1020 重试、cancel/retry/rerun 世代推进），却完全没有模块 docstring，只有零散函数 docstring。 | 补充模块 docstring：说明『worker 只在 status+attempt 同时匹配时才可写 Job，未匹配即回滚』的护栏模型、execute_guarded_job_update 对 1020 的重试规则（第二次成功必须回滚），以及 retry_job 与 rerun_succeeded_job 的边界（后者仅限业务复核场景）。 |
| `backend/app/modules/jobs/stub_execution.py` | L46-63 | run_local_stub_job（claim 失败静默返回） | 错误/补偿路径 | claim_queued_job 返回 None 时函数直接 return，不记录任何日志；对调用方而言『任务没跑但也没报错』，在 attempt 不匹配（如旧 Celery 消息重投）时可能掩盖诊断线索。 | 注释（或加一条 info 日志）说明：claim 失败属于预期竞态（其它 worker 已认领 / attempt 已推进 / 已终态），静默返回是安全设计而非吞错，并说明谁负责在此时重投或告警。 |
| `backend/app/modules/workflows/job_sync.py` | L47-142 | sync_workflow_from_jobs（Result 投影与阶段推进） | 领域语义 | 同步逻辑隐含多个领域约定：succeeded Result 用 result_json.job_attempt 过滤旧 attempt（AnalysisResult 无 attempt 列）、next_stage 被置为 'waiting_review' 仅当 stage_code=='excel_process'（遗留模板特例）、绑定 Job 与 stage.job_attempt 不一致时静默跳过——这些都没有注释。 | 注释说明：a) 投影只接受与 stage.job_attempt 完全一致的 Job/Result，旧 attempt 数据不进入阶段状态；b) 'excel_process'→waiting_review 是 excel_delivery 兼容模板的人机交接特例，新模板一律 waiting_input；c) 绑定 Job 缺失/世代不匹配时跳过该阶段的含义与恢复方式。 |
| `backend/app/modules/workflows/intake/presentation.py` | L20-30 | INPUT_BATCH_SYNC_LIMIT = 100（配合 conversion.sync_input_batch 的 max_terminal_items） | 魔法数字 | 100 是『单次描述请求最多同步的终态转换项数』，其存在是因为每次 GET 都触发 sync_input_batch 写事务，超限项被暂时标为 converting 留待下轮；该限流动机与『暂缓同步』的状态语义无任何注释。 | 注释说明：限值防止单次轮询触发全量终态重放（N+1 写放大），被限流项保持 converting 状态是刻意的下轮继续而非错误，并说明增大该值对读路径写事务成本的影响。 |
| `backend/app/modules/workflows/intake/registration.py` | L364-372 | raise_excel_failure 的 409/422 状态码映射 | 领域语义 | EXCEL_INPUT_OBJECT_CHANGED 映射 409（冲突）而其余校验失败映射 422（校验错误），这个『对象已变 vs 输入不合格』的 HTTP 语义区分是接口契约的一部分，但代码只有三元表达式没有注释。 | 注释说明：EXCEL_INPUT_OBJECT_CHANGED 表示冻结/登记期间对象被替换（冲突，需重新上传），故用 409；其余失败是输入内容不合格（422）。并提示该映射属于对外 API 契约，变更需同步前端与测试。 |
| `backend/app/modules/workflows/batch_exports.py` | L620-749 | purge_export 的双重下载证明门（row.status + FileTransfer 流水） | 错误/补偿路径 | 物理删除前既要求 row.status=='downloaded' 又要求存在 operation='workflow_batch_export' 且 batch_ref==export_uid 的 succeeded 流水：前者是进程内流式完成的标记，后者是跨进程持久证明，这个『双门防误删』设计只有错误消息没有解释性注释。 | 注释说明：row.status 可能因进程崩溃与真实下载不一致，FileTransfer 流水才是权威下载证明（_download_succeeded 的查询语义），以及为何还要求无排队/运行阶段（ACTIVE_STAGE_STATUSES 检查）才允许物理删除。 |
| `backend/app/modules/workflows/batch_exports.py` | L125-161 | _current_split_result_file_ids 的 automation_route == 'auto_accepted' 过滤 | 领域语义 | 拆板结果导出只取 auto_accepted 路由的 normal/allowance 文件，人工复核决定的最终文件被排除；『导出=自动接纳结果，人工决定走独立导出通道』的领域规则没有注释，容易让人误解为漏数据。 | 注释说明：该导出通道只覆盖自动接纳的成对拆板结果（normal + weld_allowance 必须成对，见 require_complete_split_pair）；人工复核采纳的文件属于另一类目/通道，不在本函数范围内。 |
| `backend/app/modules/workflows/intake/conversion.py` | L118-152 | sync_input_batch 的 max_terminal_items 限流与状态暂缓 | 复杂逻辑 | 终态同步超过 max_terminal_items 的项被故意标回 'converting' 留待下一轮，且成功 Result 校验链（source_file_id→dxf_file_id→stem→可读性）逐级 _mark_item_error，每步失败路径的领域含义（代次不匹配、源错绑、改名、不可读）无汇总注释。 | 为 sync_input_batch 添加注释：说明限流暂缓是轮询友好的渐进收敛设计；并列出校验链各步骤各自防御的回归（如结果绑到别的 DWG、服务端改名、派生 DXF 损坏），便于后续在链上新增校验时保持风格一致。 |

### excel_final+remnant_drawing_reader 测试补漏

| 文件 | 位置 | 元素 | 类型 | 问题 | 建议 |
|---|---|---|---|---|---|
| `Stages/remnant_drawing_reader/tests/test_reader.py` | L31-38、251-263 | _save_nested_drawing fixture 与 test_nested_insert_preserves_block_path_and_world_position | 魔法数字 | 块引用偏移 (2,3) 与模型空间插入点 (10,20) 相加得到世界坐标 12.0/23.0 的断言，数字间的几何合成关系无任何注释，fixture 也无 docstring，读者难以还原 12.0/23.0 的来源。 | 在 fixture 或测试内注释说明 (2,3)+(10,20)=(12,23) 的嵌套块坐标合成，并注明断言验证的是证据记录世界坐标而非块内局部坐标。 |
| `Stages/excel_final/tests/test_source_corpus.py` | L31、62-63 | test_all_historical_part_lists_enter_source_intake_with_row_conservation 的 11/6680/396 快照断言 | 魔法数字 | 11 个语料文件、6680 个零件、396 个构件是对 Data/十份排版 语料快照的硬编码计数，无注释说明对应哪个语料版本；语料增删时测试会静默失败。 | 注释注明这些计数对应的语料快照（文件名/提交）与更新方式，并说明该用例验证的不变量是『每行非空数据都被分类为零件或构件，行数守恒』。 |

### platform+bootstrap+integrations

| 文件 | 位置 | 元素 | 类型 | 问题 | 建议 |
|---|---|---|---|---|---|
| `backend/app/platform/messaging/celery_app.py` | L475-478 | start_worker_heartbeat 的间隔计算 max(10, min(60, stale//3)) | 魔法数字 | 心跳间隔按 stale 窗口三分之一并钳制在 10–60 秒，10/60/3 三个魔法数字背后的租约设计（心跳频率与 stale 判定窗口的关系、为什么不能超过 stale 的 1/3）没有注释。 | 注释说明：间隔取 stale//3 保证一个 stale 窗口内至少发出 3 次心跳，钳制范围避免空转与探测过密；若改 stale 配置应同步复核该公式。 |
| `backend/app/platform/security/tokens.py` | L16-58 | hash_password / verify_password / create_access_token / create_refresh_token / decode_token | 公开 docstring | 这些是跨模块公开的安全原语，全部没有 docstring；password_hash = PasswordHash.recommended()（Argon2id）的算法选择、与 seed.py 硬编码 password_algo='argon2id' 的一致性也无说明。 | 为每个公开函数补 docstring：注明哈希算法（argon2id/pwdlib recommended）、算法升级时的兼容策略，以及各 token 的过期单位（分钟/天）与 claim 集合。 |
| `backend/app/platform/storage/paths.py` | L19 | ensure_within_root 抛出 AppHTTPException(400) | 跨模块契约 | 存储适配层（base.py 的契约是 StorageError 家族）在路径逃逸时抛 HTTP 层异常，worker 进程等非 HTTP 调用方会收到一个 400 HTTP 异常而不是 StorageError，两种失败语义在代码内没有统一说明。 | 注释说明：路径逃逸走 AppHTTPException(400, INVALID_STORAGE_PATH) 以在 API 层直接返回 400，而非 StorageError；同时说明此异常类不携带存储后端失败信息，调用方应将其与 StorageError 分别处理。 |
| `backend/app/platform/storage/local.py` | L129-142 / 186-218 | bucket_object_counts 与 list_objects 的 rglob 遍历 | 复杂逻辑 | 这两处用 bucket_dir.rglob 直接枚举，未经过 ensure_within_root 的 resolve 守卫；若存储根下存在指向外部的符号链接，枚举/计数会跟随链接统计外部文件，而 _path 在读写时却会因 resolve 逃逸而拒绝，读写与枚举的防护口径不一致且无注释。 | 注释说明存储根内不允许符号链接这一隐含前提（或遍历时同样做 resolve 校验），并解释为何枚举路径不经过 _path 守卫（key 相对化需求）以及该不一致是刻意的还是缺口。 |
| `backend/app/platform/storage/minio.py` | L26-69 | _load_metrics / _parse_capacity_metrics 与 _CAPACITY_METRIC_RE | 跨模块契约 | 容量探测依赖 MinIO 集群 metrics 端点（/minio/v2/metrics/cluster）中 minio_cluster_capacity_raw_total/free_bytes 这两个随 MinIO 版本可能变化的指标名，但代码未注明所依赖的 MinIO 版本范围、指标口径（集群级 raw 字节）与端点的派生来源。 | 注释说明指标名来自哪个 MinIO 版本、为何按集群聚合求和、timeout=3 与 2MB 读取上限的考虑，以及指标缺失/不一致时降级为 unknown 而非报错的设计意图。 |
| `backend/app/platform/storage/minio.py` | L139-152 | _ensure_bucket 的写路径自动建桶 | 领域语义 | put_fileobj 会在写时自动创建桶（含 BucketAlreadyOwnedByYou 竞态兜底），而 delete/stat/list 对缺失桶分别按静默成功/NotFound/空页处理，'写自动供给、读视为缺失'的不对称语义没有注释，调用方可能误以为桶是预配置的（settings.minio_bucket_names）。 | 注释说明自动建桶是有意的幂等供给行为、与注册表/配置桶列表的关系，以及各读路径对缺失桶的降级策略，避免调用方把'桶不存在'误判为数据丢失。 |
| `backend/app/platform/storage/base.py` | L151-160 | AbstractStorageBackend.list_objects 游标契约 | 跨模块契约 | 接口 docstring 只写了'stable cursor page ordered by storage key'，未说明调用方必须遵守的约束：next_cursor 是最后一页最后一项的 storage_key（排他游标）、page_size 必须在 1–200（越界抛 ValueError）、next_cursor=None 表示终止——这些约束目前只存在于两个实现里。 | 在抽象方法 docstring 中写明：cursor 为上一页最后 key 且排他（start_after 语义）、page_size 合法范围 1–200、返回 None 即枚举结束；存储对账模块（scanning.py）依赖该循环终止语义。 |
| `backend/app/platform/config/settings.py` | L235-239 | effective_minio_metrics_url 属性 | 跨模块契约 | 未配置 MINIO_METRICS_URL 时自动拼出 /minio/v2/metrics/cluster 路径，该路径与 minio.py 中硬编码的指标名强耦合，但属性本身没有注释说明'这条路径由 MinIO 集群 metrics 端点约定而来、随版本可能变化'。 | 注释说明派生路径的来源（MinIO 集群 metrics v2 端点）、为何默认拼装、以及指标名不匹配时容量降级为 unknown 的连锁行为。 |
| `backend/app/bootstrap/application.py` | L23-24 | configure_logging() / load_models() 在模块导入期执行 | 复杂逻辑 | load_models() 在 import 期就产生副作用（注册全部 ORM mapper），这是 seed、Alembic、测试共享同一注册表的顺序保证，但没有任何注释解释'为什么必须放在 import 期而不能移入 lifespan'，后续维护者很可能误移导致 mapper 缺失。 | 注释说明：模型必须在引擎与 seed 使用前注册完成（Alembic env.py 与测试同样依赖 model_registry），import 期执行是刻意的装配顺序约束，不可挪进 lifespan。 |
| `backend/app/bootstrap/seed.py` | L61-84 | init_db 中权限/角色的无条件重灌 | 复杂逻辑 | 角色与权限行是'缺失才插入'，但 super_admin/admin 的 permissions 每次启动都被整体重赋、legacy 超管被强制降级——'幂等创建'与'每次启动修复'两种策略混在同一函数且只有账号部分有注释，手工调整的权限会在下次启动被回滚。 | 注释说明：权限重赋是有意的'恢复性修复'策略（保证超管可恢复根权限），任何对 admin/super_admin 角色权限的手工修改都会在下次启动被覆盖；operator/viewer 同理按种子重算。 |
| `backend/app/bootstrap/task_registry.py` | L8-27 | load_tasks() | 跨模块契约 | 函数名为'加载任务模块'，但实际还通过 register_job_worker_maintenance() 和 register_control_plane_worker_observer() 向 platform 的 worker-ready/signal seam 注册回调，调用者仅凭 docstring 无法知道存在这些注册副作用及它们必须在 worker 启动前完成的顺序约束。 | 在 docstring 中说明：本函数除导入任务模块外，还会注册 Job 陈旧恢复与 control-plane 观察者回调（经 register_worker_ready_callback/signal_callback），是 worker 装配顺序的一部分，且被 celery_app 在 import 期调用。 |
| `backend/app/platform/database/base.py` | L17-33 | _localize_loaded_datetimes 及 load/refresh 监听器 | 领域语义 | MySQL DATETIME 无时区，整套持久化约定是'库中一律存业务墙钟时间（Asia/Shanghai），读时补 tzinfo'；但该不变量分散在 base 监听器、mixins 的 business_now 默认值、session 的 SET time_zone 与 time.py 的 MYSQL_TIME_ZONE='+08:00' 中，代码内没有一处完整说明，写入方若混入 UTC 时间会静默偏移。 | 在监听器或 time.py 注释统一说明：所有持久化 datetime 必须为业务时区（aware 或按业务时间解释的 naive），禁止写入 UTC；MYSQL_TIME_ZONE 用 '+08:00' 偏移串而非 IANA 名是为避免依赖 MySQL 时区表，且只影响 TIMESTAMP 列、不影响 DATETIME。 |

### cad_processing+dxf_classification+dxf_splitting+excel_processing

| 文件 | 位置 | 元素 | 类型 | 问题 | 建议 |
|---|---|---|---|---|---|
| `backend/app/modules/cad_processing/preview_rendering.py` | L29-42, 215-217 | 预览防护常量与渲染器配置（MAX_DXF_SIZE_BYTES/MAX_DXF_ENTITIES/MAX_PREVIEW_BYTES、hatching_timeout=2.0、circle_approximation_count=64、max_flattening_distance=0.1、_header_bounds 的 1e19 守卫） | 魔法数字 | 这些阈值/超时秒数直接决定预览的 CPU/内存成本上限与渲染精度，但均无"为什么取这个值"的说明，后续调整者无法判断 2.0 秒 hatch 超时或 64 圆近似数是否是安全边界。 | 用注释说明每个限制对应的资源防护目的（如拒绝超大文档防 DoS、hatching_timeout 防止填充图案拖垮渲染、1e19 过滤损坏的 EXTMIN/EXTMAX 头），并注明哪些值需随 ezdxf 版本复核。 |
| `backend/app/modules/cad_processing/remnant_conversion.py` | L9-32 | convert_dwg_directory（remnant 域单次 ODA 目录调用适配器） | 错误/补偿路径 | 模块无 docstring，且函数对 result.success=False 的图纸静默丢弃（仅返回成功项），remnant 域调用方拿不到任何失败信号或部分成功提示，失败路径完全不可见。 | 补充模块 docstring（说明这是 remnant 域的目录级单调用适配器、不拥有账本），并在函数内注释失败项为何被静默省略、调用方应如何发现/补偿部分失败。 |
| `backend/app/modules/dxf_classification/execution.py` | L159-195 | _replace_classification_artifacts 的 MySQL 1020 重试循环 | 错误/补偿路径 | 仅注释"Retrying after MySQL 1020"，未说明什么并发竞态会产生 1020（旧 attempt 与当前 attempt 并发替换同一 workflow 的 artifacts）、为何固定重试 2 次、重试耗尽后的后果（直接抛 ClassificationError，任务失败）是什么。 | 注释 1020 的来源（artifact 替换与 workflow 行读的 lost-update 竞态）、重试次数选定的理由、以及重试耗尽时事务回滚后调用方应如何恢复。 |
| `backend/app/modules/dxf_classification/persistence.py` | L398 | MAX_BH_STAGE2_INPUTS = 5000 | 魔法数字 | 5000 是单批 BH/BOX 分类账送入 stage2 的上限，无任何来源说明（Stage 内存上限？单 Job 参数体积？），未来调整或对接新 Stage 时无法判断该值是否仍成立。 | 注释该上限的依据（如单 Job params/清单体积或子进程内存预算），并说明超限时拒绝继续而非截断的原因。 |
| `backend/app/modules/dxf_classification/persistence.py` | L505-521 | _load_stage2_classification_batch 的 BH/BOX manifest 载荷（NUL 分隔行 + bh_manifest_version=1） | 跨模块契约 | manifest 的字段顺序、\0/\n 分隔格式与 bh_manifest_version=1 是分类账单向 Excel Stage2/拆板交接的跨模块 seam 契约（sha256 摘要即基于该格式计算），但代码没有任何格式注释，任何一侧改字段都会造成静默摘要失配。 | 在构造 payload 处注释 manifest 线格式（每行 classification_item_id\0input_file_id\0input_sha256\0input_name\0profile_normalized）及版本号递增策略，指明消费方（stage2_execution 的 bh_manifest_sha256 校验）与之一一对应。 |
| `backend/app/modules/dxf_classification/models.py` | L38 | DxfClassificationRun.classifier_version 列默认值 "1.2.0" | 魔法数字 | 模型默认字面量 1.2.0 与 adapter.py 的 CLASSIFIER_VERSION="1.3.0" 及 README 的描述相互矛盾，虽然正常路径总会显式赋值，但这个无人解释的陈旧字面量是版本漂移的隐患。 | 删除或注释该默认值：说明它只是列级兜底，真实版本始终由 adapter.CLASSIFIER_VERSION 显式写入，并建议改为引用同一常量或补一条版本对齐注释。 |
| `backend/app/modules/dxf_splitting/execution.py` | L615-647, 802 | publish_progress 中 run.manual_review_count = manual_count + failed_count 与 finish_split_run/validation 的计数口径 | 领域语义 | CLI 进度回调把 failed 并入 manual_review_count，而独立校验侧（validation.py）把 failed 条目路由为 manual_review 又另计 failed_count，两套计数来源（CLI 进度 vs 独立校验）在 run 上先后覆盖，无注释说明"失败并入待复核"的业务口径及两侧必须一致的不变式。 | 注释计数约定：failed 与 manual_review 都属于"未形成正式配对结果"并合并反映到 manual_review_count/status，而 failed_count 仅为审计细分；并说明 CLI 进度与 validation 重算两处来源为何都必须保持该口径。 |
| `backend/app/modules/dxf_splitting/execution.py` | L120-153 | _portable_value / _portable_report（绝对路径改写为 split-output/、classified-input/ 前缀） | 复杂逻辑 | 该清洗器把 Stage 报告中的临时绝对路径改写为可移植相对前缀再持久化，是防止绝对路径泄漏进 MySQL/对象存储/日志的安全关键逻辑，但函数无 docstring 也无注释说明改写规则与目的。 | 为 _portable_value 补充 docstring：说明其作用是去除持久化报告中的临时工作目录绝对路径（防泄漏与可移植性），列出两种前缀映射规则及不匹配字符串原样保留的行为。 |
| `backend/app/modules/dxf_splitting/persistence.py` | L823-839 | get_excel_split_handoff 的 mode="no_split_candidates"（依赖 drawing_processing 阶段 output_json 的 reason="no_split_candidates"） | 跨模块契约 | Excel 交接的"无拆板候选"模式依赖 workflow 阶段跳过记录里的魔法字符串 reason，这是 dxf_splitting 与 workflows/excel_processing 之间的隐藏字符串契约，任何一侧改写都会静默破坏交接。 | 注释该契约：跳过记录必须同时满足 status=skipped 与 reason=no_split_candidates 才允许 Excel 侧无拆板结果继续，并建议将 reason 提升为共享常量。 |
| `backend/app/modules/dxf_splitting/interface.py` | L1-197 | interface.py 门面（MAX_AUTOMATIC_ATTEMPTS、create_download_token/require_download_token、get_excel_split_handoff 等导出） | 跨模块契约 | 与其余 interface 门面相同，所有转发函数无 docstring；尤其 MAX_AUTOMATIC_ATTEMPTS=1 的含义、下载 token 的 JWT 声明与过期语义、get_excel_split_handoff 的两种 mode 是跨域调用者必须知道的契约，却完全未文档化。 | 为 token 相关函数与 handoff 函数补充契约注释：token 内嵌类别清单并绑定 workflow/run/uid、过期后返回 410，handoff 的 mode 取值与前置条件（必须存在当前正式 attempt 的拆板 run）。 |
| `backend/app/modules/excel_processing/stage_adapter.py` | L1-1068 | 整个模块（父进程唯一的 Excel Final Stage seam 适配器） | 模块 docstring | 1068 行的 seam 核心文件完全没有模块 docstring：protocol_version=1 信封、RESULT/ERROR 前缀、字段白名单、所有边界上限（64/500/8000/20/10 等）的总体设计只能靠逐函数反推。 | 补模块 docstring：说明本模块是父进程与 Stages/excel_final 的唯一边界、DWG_EXCEL_FINAL_RESULT=/ERROR= 行协议与 protocol_version=1、字段白名单+长度上限策略（严格拒绝而非截断）、以及密码仅经子进程环境传递的安全约定。 |
| `backend/app/modules/excel_processing/stage_adapter.py` | L265-300, 333-386 | run_excel_final_pipeline / run_excel_stage2_pipeline 的 token 临时文件 + 原子 replace 发布模式 | 复杂逻辑 | Stage 先写 .{stem}.{publish_token}.xlsx 再 replace 到最终名、finally 清理残留，这一精心设计的原子发布/防半成品可见模式没有任何注释，读者无法判断 token 与两步 rename 的用意。 | 注释发布协议：token 命名保证并发或中断时最终路径永远不会出现半成品文件，rename 为原子提交点，finally 清理是为崩溃残留兜底；说明内部工作簿（internal）与对外工作簿双路径各自只被 DB 导入/下载使用。 |
| `backend/app/modules/excel_processing/stage_runner.py` | L280-296 | main() 的错误通道设计（仅 InputContractError 转结构化 ERROR 信封并 exit 2，其余异常以裸 traceback 交给父进程按 stderr 字符串标记分类） | 错误/补偿路径 | 子进程的错误契约是"结构化失败走 DWG_EXCEL_FINAL_ERROR=，未预期异常走 stderr 启发式（ParserError/BadZipFile 等标记）"，但代码没有任何注释说明这条通道划分及其后果，父进程 _raise_for_failed_stage 的字符串匹配也无解释。 | 注释错误通道约定：输入契约类失败必须走结构化信封（便于前端逐行定位），未预期异常保留为 traceback 且父进程只记录类型与有界片段（4000 字符），说明字符串标记是脆弱启发式、新增解析失败类型需同步两侧。 |
| `backend/app/modules/excel_processing/stage2_execution.py` | L1023-1033 | resolve_excel_stage2_worker_inputs 后 stage_registered_file(source.xlsx) 立即 source_path.unlink() | 复杂逻辑 | 冻结的源 Excel 被下载+sha256 校验后立刻删除、后续从未使用，这段"下载即弃"序列的意图（证明冻结对象可检索且未变化后再进入耗时的读取阶段？）没有任何注释，容易被认为是残留死代码而被误删。 | 注释该步骤的目的：在长时间 BH/BOX 读取前先行验证源对象可检索且摘要一致（尽早失败），下载后即删以节省工作区空间；若仅需校验可改用 stat/校验而不落盘。 |

### files 模块

| 文件 | 位置 | 元素 | 类型 | 问题 | 建议 |
|---|---|---|---|---|---|
| `backend/app/modules/files/registration.py` | L36 | save_upload_file | 公开 docstring | 约 150 行的上传 saga 入口（临时文件、哈希、校验、transfer intent、pending 对象注册、失败补偿）完全没有 docstring，调用方无法得知字节先入对象存储、元数据回滚时由 after_rollback 补偿删除这一关键顺序。 | 为 save_upload_file 补 docstring，说明上传顺序（校验→写对象存储→MySQL 登记）、异常时补偿路径（_settle_storage_write_failure / after_rollback 删除）与 durable_intent 的含义。 |
| `backend/app/modules/files/registration.py` | L85 | save_upload_file 中 dxf_prefix/dxf_tail 的 65536 采样窗口 | 魔法数字 | 仅取 DXF 前 64KiB+尾 64KiB 做结构校验（validate_dxf_structure），65536 这一窗口大小的正确性假设（SECTION/EOF 哨兵必落在首尾采样内）无任何注释，改动极易静默破坏校验。 | 提取为命名常量并注释采样依据（只读首尾窗口即可判定 DXF 结构），同时注明 SpooledTemporaryFile 16MiB 与 1MiB 读块为内存/吞吐权衡。 |
| `backend/app/modules/files/storage_transactions.py` | L572 | _storage_after_transaction_end 事件钩子 | 复杂逻辑 | after_commit/after_rollback 已处理补偿，第三个钩子在 transaction.parent is None 且不在事务中时再次执行同样的删除/结算，触发条件与必要性无注释，维护者可能重复补偿或误删。 | 注释说明该钩子覆盖的场景（如提交失败/无显式 rollback 的事务结束路径），以及为何不会被 after_rollback 重复执行。 |
| `backend/app/modules/files/streaming_zip.py` | L67 | iter_storage_zip 中 force_zip64=True 与中途失败语义 | 复杂逻辑 | 每个成员强制 ZIP64 无注释（推测因不可 seek 的 sink 无法回填尺寸）；且成员读取失败时异常在已向响应产出字节之后抛出，客户端将收到截断 ZIP，此错误模式未对调用方文档化。 | 注释 force_zip64 的原因（流式 sink 无法回写定位），并在 docstring 中明确：任何成员读失败都会以已发送部分字节 + 异常终止，调用方不得把部分响应当成功。 |
| `backend/app/modules/files/exports.py` | L46 | download_signature / DOWNLOAD_URL_TTL_SECONDS | 公开 docstring | download_signature 无 docstring，且复用 settings.jwt_secret_key 作下载签名 HMAC 密钥、TTL 固定 300 秒，这一安全耦合（轮换 JWT 密钥会使旧下载链接全部失效、签名与登录令牌共享密钥）完全未文档化。 | 为下载签名函数补 docstring，说明密钥来源、TTL 选值理由及密钥轮换影响；将 300 提取为带注释的常量。 |
| `backend/app/modules/files/models.py` | L64 | StoredFile.status / FileTransfer.status 字符串状态 | 领域语义 | 流转账本（FileTransfer）的 prepared/in_progress/succeeded/failed/cancelled/compensation_required 与文件状态 available/deleted 均为裸字符串，模型层无任何注释说明各状态含义与合法转移，CONTEXT.md 的「结算/补偿」概念与代码无对应。 | 为两个 status 字段补充注释或在模块顶部给出状态机表（含合法转移与终态），必要时提取为 Literal 常量集中维护。 |
| `backend/app/modules/files/validation.py` | L97 | validate_upload_mime 的分支 | 其他 | if/else 两个分支返回完全相同（均 return normalized），条件判断是死代码；docstring 说 DWG 头是权威校验，但未说明为何 MIME 检查形同虚设，容易误导后人『修复』或误以为存在拒绝逻辑。 | 删除冗余分支并注释『MIME 仅归一化、不拒绝，实际由 DWG 头/结构校验把关』，或补上真正想要的拒绝语义。 |

### operations+automation

| 文件 | 位置 | 元素 | 类型 | 问题 | 建议 |
|---|---|---|---|---|---|
| `backend/app/modules/operations/control_plane/interface.py` | L14 | record_control_plane_event | 跨模块契约 | 作为模块 docstring 宣称的『stable control-plane write boundary』，该公共接口函数无任何 docstring；severity（service.py 中硬编码 info/warning）与 direction（默认 internal）的合法取值、event_type 命名约定均无定义，跨模块调用者只能猜测。 | 补充 docstring 列出 severity/direction 枚举值、event_type 建议格式（如 worker.heartbeat）与 correlation_id 语义，并在 service.py 硬编码处引用同一约定。 |
| `backend/app/modules/automation/agent/routes.py` | L1 | 模块 docstring + list_agent_tools | 模块 docstring | routes.py 无模块 docstring，四个路由重复的 503 禁用模式与 list_agent_tools 恒返回 [] 的占位行为没有集中说明；空工具列表会让 API 消费者误以为是真实能力枚举。 | 加模块 docstring 说明 AGENT_ENABLED=false 时全路由 503、list_agent_tools 为 Stage 1 占位恒返回空列表，避免重复模式漂移。 |
| `backend/app/modules/automation/agent/models/runs.py` | L26 | AgentRun.history_count | 领域语义 | history_count 字段与 AgentRunRead schema 均暴露该字段，但全仓库无任何写入点（恒为默认 0），其语义（创建 run 时会话历史条数？）完全未注释。 | 注释字段语义并在 create_agent_run 中从 get_session_history 实际填充，或标注为预留字段并说明填充时机。 |

### scripts+infra（主代理自审补充）

| 文件 | 位置 | 元素 | 类型 | 问题 | 建议 |
|---|---|---|---|---|---|
| `scripts/lib/database.sh` | L386-689 | migration_test_cmd（约 300 行临时 schema 生命周期） | 复杂逻辑 | 空库迁移测试的完整生命周期（创建临时库→alembic upgrade→种子兼容→表结构验证→清理）没有任何内联注释，cleanup_orphaned_migration_tests（L350）的孤儿库识别规则（按命名前缀？按时间？）也不可见，误删/漏删风险无说明。 | 在迁移测试函数头部注释生命周期各阶段与失败时的清理路径，并说明孤儿库的识别与保留策略（如按前缀 + 时间窗，防止误删正在运行的测试）。 |
| `scripts/lib/compose.sh` | L1-441 | 模块整体（Compose 部署/备份/恢复实现） | 模块 docstring | 441 行仅有 usage 文本与一句头部说明，零内联注释：compose 卷备份（tar 流式导出、容器停止要求）、密钥校验、`docker compose config` 验证的失败分类均无解释，`COMPOSE_PROJECT_NAME` 默认值影响多实例共存的语义也未说明。 | 为备份/恢复函数与密钥校验段补充注释：卷备份的一致性前提（先停服务）、tar 流的中断处理，以及项目名隔离多部署的约定。 |
| `scripts/lib/cad_worker.sh` | L1-323 | 模块整体（worker 生命周期管理） | 模块 docstring | 323 行的 worker 启动/停止/健康检查脚本仅 5 行注释；健康探测的等待轮次与超时（魔法值）、worker 进程的 PID 管理、停止时的优雅退出顺序均无说明，运维者无法判断脚本卡在哪个环节。 | 补充注释：健康探测循环的轮次/超时设计（为什么等待 N 秒）、优雅停止的信号与顺序（先停 worker 再停依赖）、PID/日志文件的约定路径。 |
| `scripts/windows/forward_to_win11.sh` | L1-311 | 模块整体（SSH remote-forward 隧道管理） | 领域语义 | 将 Win11 的 8080 通过 SSH 反连隧道暴露到本机，安全边界取决于 REMOTE_BIND_ADDRESS=127.0.0.1 默认值，但脚本没有注释说明为什么必须绑定回环地址（防局域网直连）、start/stop 的 PID/日志管理约定，以及隧道中断后的自愈行为。 | 注释隧道拓扑与安全约束（远端绑定 127.0.0.1 防暴露）、断线重连/状态检测机制，以及该通道与生产 TLS 边界的关系（仍是明文 HTTP 通道）。 |
| `scripts/verify.sh` | L30-58 | run_gate / run_optional_gate 与 gate 清单 | 领域语义 | 统一门禁的 quick/full 分层、BLOCKED 与 FAIL 的分类语义、--allow-blocked 的行为（跳过还是放行？）、optional gate 的选取理由（为什么 migration-test/DWG 转换可选而其余必选）都没有注释，新增 gate 时无法判断放哪一层。 | 注释 gate 分类规则：必选=无外部依赖且快；可选=需要 MySQL/外部二进制；BLOCKED 与 FAIL 的区别及 --allow-blocked 的用途；quick 与 full 的取舍。 |
| `infra/gateway/nginx/nginx.conf` | L46, 65-67 | client_max_body_size 520m 与 limit_req 限流参数 | 魔法数字 | 520m 上传上限、登录 2r/s 与 API 100r/s 限流、10m zone、429 状态码均无注释：限流档位与批量上传场景（5000 文件批次）的关系、burst 语义、为什么登录限流远低于 API，都是安全与容量设计的关键参数。 | 注释各限流参数的设计依据（登录 2r/s 防爆破、API 100r/s 兼容多 worker 轮询、zone 10m 的 IP 容量）与 520m 与批量上传上限的对应关系，并说明调整时的验证方法。 |

## 五、Low 优先级发现

### backend 业务模块 3/4：identity + projects + remnant_inventory

| 文件 | 位置 | 元素 | 类型 | 问题 | 建议 |
|---|---|---|---|---|---|
| `backend/app/modules/identity/routes/sessions.py` | L129-179 | refresh_token | 错误/补偿路径 | 刷新流程只签发新 access token 与 job-events cookie，不轮换 refresh token 本身（不重设 refresh cookie），这一设计决策与吊销语义（黑名单 + 密码变更时间戳）没有任何注释，调用方可能误以为刷新即轮换。 | 在函数内注释：refresh token 有意不轮换，其生命周期由过期时间、token_blacklist 与 password_changed_at 时间戳共同约束；若未来引入轮换需同步处理旧 token 入黑名单，防止并发刷新互相作废。 |

### backend/tests/ 与 tests/（后端测试与验证脚本）

| 文件 | 位置 | 元素 | 类型 | 问题 | 建议 |
|---|---|---|---|---|---|
| `backend/tests/workflows/test_workflow_production.py` | L1 | 模块级（3140 行） | 模块 docstring | 全套件最大、最重要的测试模块没有模块 docstring，且没有注释说明它与输入冻结/Attempt 世代/阶段契约的对应关系，读者需要通读近 200 个 fixture/测试才能建立整体心智模型。 | 补 2-3 行模块 docstring：说明覆盖 Linux production workflow 的哪条链路（source_intake 冻结 → 分类 → stage1/2 → 交付归档）、共享 fixture 链以及文件级不变量（当前 attempt 才可写结果等）。 |
| `backend/tests/dxf_splitting/test_dxf_splitting_pipeline.py` | L1 | 模块级（2328 行） | 模块 docstring | 第二大测试模块无模块 docstring，涉及 Steel DXF Split 1.5.2 适配、审查路由、账本与重试多个子系统，缺少总览使读者难以定位边界。 | 补简短模块 docstring：说明覆盖的子系统（splitter 适配契约、manual_review 候选/正式产物分离、BH 账本、数量检查点与恢复路径）。 |
| `backend/tests/infrastructure/test_celery_recovery.py` | L49-50 | test_cleanup_removes_only_stale_consumed_sql_broker_rows | 魔法数字 | 5 分钟『新保留』与 24 小时『陈旧』边界是裸时间常量，未链接到生产侧 stale-cutoff 常量或清理窗口的推导；相邻测试虽用 docstring 说明了保留 reserved 行的理由，但边界值本身缺乏出处。 | 注释说明 5 分钟/24 小时来自生产 cleanup 的陈旧窗口配置（reserved 未 ack 的行必须在窗口内存活以便 worker_lost 重投递），并建议引用常量。 |

### frontend React 前端 (frontend/src/)

| 文件 | 位置 | 元素 | 类型 | 问题 | 建议 |
|---|---|---|---|---|---|
| `frontend/src/features/remnant-inventory/RemnantAutoImportPanel.tsx` | L34, 100-103 | maximumFiles = 100 | 魔法数字 | 100 张的上限只在 UI 文案出现，未说明它是后端批量创建接口的约束还是前端自定；两处 100（常量与文案）易漂移。 | 注释 100 的来源（后端一次自动导入批次的文件数上限），并说明超限时直接拒绝而非截断（与 files 的 5000 张截断策略不同）。 |
| `frontend/src/features/remnant-inventory/useRemnantBatch.ts` | L11-14 | refetchInterval 2_000ms 轮询 | 魔法数字 | 2 秒轮询间隔、terminal 状态集合与后端状态机的对应关系没有说明；全仓轮询间隔（2s/3s/4s/10s）各页不一且均无理由注释，难以评估降频设计。 | 注释：2s 是余料批次的轮询节奏（可感知进度且不压垮接口），terminal 集合与 API 状态枚举同步；如需调整请说明与后端快照成本的权衡。 |
| `frontend/src/features/files/FileUpload.tsx` | L96, 129-131 | 并发 worker 数 Math.min(3, total) 与 percent 封顶 99 | 魔法数字 | 3 个并发上传的带宽权衡与 percent 永不显示 100%（避免完成前误判）都是刻意设计，但无注释，容易被改成串行或直接 100%。 | 注释：3 为浏览器并发上传上限（与带宽/服务器压力权衡）；进度条在全部完成后才由 completedTransferProgress 置 100，中间封顶 99 表示“仍在进行”。 |
| `frontend/src/features/jobs/jobs.api.ts` | L20-37, 167-197 | MAX_PARALLEL_BULK_REQUESTS=3 与波次循环 index += 3 | 魔法数字 | 200 有注释说明是后端过滤上限，但 3 路并发波次、内联的 index += 3 与 200/3 的关系没有解释；改其中一个常量会静默破坏另一个。 | 注释：按 200 个 file_id 分块后每次最多 3 块并发（限制瞬时请求数），并把内联 3 改为引用常量以免漂移。 |

### Stages 1/2: 拆板与分类算法（steel_dxf_split_v1.5.2 / steel_dxf_classifier_v1.1.0 / bh_left_right_reader / BOX左右进读取）

| 文件 | 位置 | 元素 | 类型 | 问题 | 建议 |
|---|---|---|---|---|---|
| `Stages/bh_left_right_reader/src/bh_reader/analyzer.py` | L118-121 | BHAnalyzer 类 | 公开 docstring | BHAnalyzer 是读取器核心类（2379 行文件），三个 step 方法各有 docstring，但类本身无 docstring，安全语义（严格向下取整防下料偏短、歧义时拒绝输出而非猜测、不对外推大进尺）只存在于 README，代码内无对应说明。 | 为类补 docstring：概括固定三步流程、安全取整与单位验证的 fail-closed 语义、'几何无法区分时保守齐平而非外推'的工程决策，并引用 README 回归结论。 |
| `Stages/BOX左右进读取/src/box_reader/analyzer.py` | L58-68 | BoxAnalyzer 类 | 公开 docstring | BoxAnalyzer（1294 行）无类 docstring，其特有的领域决策——主视图=带 PartMark 的视图而非品红剖面标识、上下腹板投影重叠时合并输出、端部窗口 max(2*tf,40mm) 扩展——只记录在 README，代码内无总览。 | 为类补 docstring：说明主视图判定（PartMark 优先于 Section 剖面符号）、四板角色与左右进合并/区分输出语义（对应拆板 equivalence 成对合并）、端部窗口与安全取整规则，并标注已知限制（折线构件标红、坡口信息论不可区分）。 |

### Stages 2/2：转换与 Excel 处理（dwg2dxf / dxf2dwg / dxf2excel / excel_final / remnant_drawing_reader）

| 文件 | 位置 | 元素 | 类型 | 问题 | 建议 |
|---|---|---|---|---|---|
| `Stages/remnant_drawing_reader/src/remnant_drawing_reader/text.py` | L19-23 | normalize_text 管线 | 复杂逻辑 | 模块无 docstring，normalize_text 串联 \M+5 GBK 解码 → ezdxf.decode_dxf_unicode → NFKC → 空白折叠四步，但没说明每步解决什么（尤其 NFKC 全角→半角对后续正则分类的影响），失败路径（解码失败保留原文）也无注释。 | 添加模块 docstring 与逐步注释：\M+5 是 ZWCAD BigFont 转义、decode_dxf_unicode 处理 \U+XXXX、NFKC 归一化全角字符使候选分类正则稳定匹配。 |
| `Stages/remnant_drawing_reader/src/remnant_drawing_reader/models.py` | L54-60 | ParseResult.to_dict | 其他 | to_dict 用 format(summary[key], 'f') 把 Decimal 转字符串，但没说明为什么必须用 'f' 格式——避免 Decimal 指数记法（如 1E+2）破坏 JSON 无精度损失的十进制字符串契约（README 承诺的契约）。 | 添加一行注释：format(x,'f') 保证 Decimal 以十进制字符串输出、不带指数记法，满足输出契约版本 1.1 的精度要求。 |
| `Stages/excel_final/tests/test_weight_validation.py` | L1 | 全部测试文件（30+ 个） | 测试意图 | excel_final 与 remnant_drawing_reader 的所有测试文件既无模块 docstring 也无测试函数 docstring（约 300 个用例）；虽然命名较自解释，但'防什么回归'（如 0.1kg 源重量链阈值、2% 手册偏差硬隔离这类人工核验规则的历史回归）没有说明，规则变更时无法判断哪些用例该更新。 | 为承载关键领域阈值的测试文件（test_weight_validation、test_bh_stage2、test_handbook_repository）添加模块 docstring，说明每批用例守护的规则与阈值边界（如 0.1/0.005/0.02、精确守恒），防未来误改容差导致核验口径漂移。 |

### jobs+workflows

| 文件 | 位置 | 元素 | 类型 | 问题 | 建议 |
|---|---|---|---|---|---|
| `backend/app/modules/jobs/event_stream.py` | L25-27 | _POLL_INTERVAL / _MAX_DURATION / jobs 轮询 0.5s | 魔法数字 | 2.0 秒轮询间隔、600 秒会话上限、集合流 0.5 秒间隔均无注释；单任务与多任务流使用不同间隔也没有说明理由，后续调参者无从判断对 MySQL 连接压力的影响。 | 注释说明：间隔与上限是『短会话轮询 + 有界 SSE』的设计参数（2s 平衡实时性与轮询压力；600s 为 SSE 会话硬上限），多任务流用 0.5s 的原因（集合流首帧快速呈现），以及这些值应如何随部署规模调整。 |
| `backend/app/modules/jobs/schemas.py` | L40, 45 | ConversionBatchCreate.file_ids / JobBulkCancellation.job_ids 的 max_length=200 | 魔法数字 | 批量上限 200 在两处出现但没有任何注释说明依据（HTTP 体大小？事务长度？），且与 intake 侧 MAX_INPUT_DWG_FILES=5000 的关系不明，容易在调大输入上限时忘记同步。 | 注释说明 200 的约束动机（单事务/单请求可承受的 Job 行数与参数体大小），并提示该上限与 DWG 输入上限相互独立、调整任一需评估另一侧影响。 |

### platform+bootstrap+integrations

| 文件 | 位置 | 元素 | 类型 | 问题 | 建议 |
|---|---|---|---|---|---|
| `backend/app/platform/storage/local.py` | L1 | 模块整体（LocalFileStorage 218 行） | 模块 docstring | LocalFileStorage 是承载原子写入、目录 fsync、路径逃逸防护等关键语义的生产 adapter，但文件没有模块 docstring，读者无法快速了解它与 MinIO adapter 的语义对齐点（原子 PUT 等价性、容量统计口径）。 | 补充模块 docstring：说明本 adapter 提供原子写（临时文件+rename+父目录 fsync）、健康探测仅验证可写性、容量统计为 shutil.disk_usage 视角，以及与 base 契约的对应关系。 |
| `backend/app/platform/config/settings.py` | L150-152 / 287 | agent_memory_ttl=7200 / agent_max_messages=20 / handbook connect_timeout=5 | 魔法数字 | 多个非显然的调优默认值没有说明依据：agent 记忆保留 7200 秒与 20 条上限的领域含义、五金手册连接超时 5 秒的选取理由，后续调整时无从判断是否破坏约定。 | 为这些字段补充一行注释说明语义与选取依据（如'记忆保留覆盖一个业务日'、'手册连接为只读探测，5s 内超时即放弃'）。 |
| `backend/app/platform/observability/logging.py` | L8-11 | configure_logging() | 公开 docstring | 全局日志装配函数没有 docstring，也未说明 basicConfig 的幂等性（root 已有 handler 时第二次调用为 no-op）、日志格式与请求关联约定（request_id 由 application.py 的 _log_http_failure 单独打点，与审计日志的关联约定缺失）。 | 补 docstring：说明该函数只能在进程早期调用一次、格式字段含义、以及结构化审计/request_id 关联不属于本模块而属于 HTTP 层的约定，避免误以为此处是审计日志装配点。 |

### cad_processing+dxf_classification+dxf_splitting+excel_processing

| 文件 | 位置 | 元素 | 类型 | 问题 | 建议 |
|---|---|---|---|---|---|
| `backend/app/modules/dxf_splitting/adapter.py` | L24 | QUANTITY_CHECK_INTERVAL = 30（数量守恒校验间隔） | 魔法数字 | 30 与 Stage 侧 cli.py 的 QUANTITY_CHECK_INTERVAL 是重复常量，后端用它生成 quantity_checkpoints 供前端/账本展示，但未注释它是镜像 Stage 的检查节奏且必须与 Stage 保持同步。 | 注释该值镜像 Stage 的守恒校验间隔（每 30 张/尾批各做一次），并提示两侧漂移会导致 checkpoint 与 Stage 实际校验点不一致。 |
| `backend/app/modules/dxf_classification/interface.py` | L1-139 | interface.py 门面（run_dxf_classification / load_bh/box_stage2_classification_batch / reconcile_* 等转发函数） | 跨模块契约 | 门面函数无 docstring：load_*_stage2_classification_batch 在 expected_run_id 不匹配或运行未完成时会抛 ClassificationError、reconcile_* 返回 bool 且带关闭孤儿 run 的副作用，这些错误模式与副作用调用者无从得知。 | 为 stage2 batch 加载与 reconcile 函数补充契约注释：加载失败的错误码/前置条件（run 必须 completed 且 id 匹配），reconcile 的副作用（把运行中投影按 Job 终态改写为 cancelled/failed）。 |

### files 模块

| 文件 | 位置 | 元素 | 类型 | 问题 | 建议 |
|---|---|---|---|---|---|
| `backend/app/modules/files/access.py` | L122 | can_read_file 的已删除项目隐藏规则 | 领域语义 | 文件仅关联已删除项目时对所有人（含上传者）返回 False 的『隐藏』语义无注释，与 file_list_access_filter 中注释过的 not_orphaned_by_project_deletion 规则不一致地未被文档化。 | 在 can_read_file 中为『active 为空但含已删除关联 → 不可读』补充注释，说明这是有意隐藏而非 bug，并与列表过滤规则交叉引用。 |
| `backend/app/modules/files/storage_transactions.py` | L267 | settle_transfer 中 error_message[:1000] | 魔法数字 | 错误信息被截断到 1000 字符但列类型是 Text，截断动机（防日志/审计膨胀？）无注释，且与 576 行错误码并存时无一致性说明。 | 提取为常量（如 MAX_ERROR_MESSAGE_LENGTH）并注释截断原因。 |
| `backend/app/modules/files/registration.py` | L115 | request_id 生成中的 sha256 hex 截断 [:43] 与 [:40] | 魔法数字 | upload 用 43 位、generated 用 40 位的截断不一致且无注释，两处长度差异无依据，弱化幂等键可读性也无说明。 | 统一截断策略并注释目的（缩短 request_id 适配列宽/可读性），或直接使用完整 hex。 |

### operations+automation

| 文件 | 位置 | 元素 | 类型 | 问题 | 建议 |
|---|---|---|---|---|---|
| `backend/app/modules/automation/agent/memory.py` | L5 | agent_memory_ttl/agent_max_messages 默认值 | 魔法数字 | settings.py 中 agent_memory_ttl=7200 秒、agent_max_messages=20 条这两个阈值无任何来源说明，memory.py 仅引用它们而未解释取值依据；截断策略 messages[-20:] 只保留尾部也会静默丢弃早期消息。 | 在 memory.py 模块 docstring 中说明 TTL 与 20 条上限的设计理由（如成本/上下文窗口权衡）及被截断消息的处理策略。 |
| `backend/app/modules/operations/audit/routes.py` | L20 | list_audit_logs 查询参数 | 魔法数字 | page_size 上限 200、action_domain 长度 64、search 长度 100 等裸魔数无注释，且 action_domain 前缀过滤 `like(f"{domain}.%")` 正是审计命名约定的唯一强制点，但该约定在本文件内没有说明。 | 为魔数加常量或注释，并注明 action_domain 过滤依赖 write_audit_log 的点分命名约定。 |
| `backend/app/modules/automation/contracts/interface.py` | L1 | 模块 docstring | 模块 docstring | 该文件是能力契约的真相来源（automation_capability_contracts / windows_node_contract），但无模块 docstring；windows_node_contract 返回 version="v1-draft" 的草稿端点却未说明草稿状态的稳定性承诺与消费方约束。 | 补模块 docstring 说明『契约只描述现状、不宣称实现』及 v1-draft 版本策略（何时升级、消费方如何依赖）。 |

### scripts+infra（主代理自审补充）

| 文件 | 位置 | 元素 | 类型 | 问题 | 建议 |
|---|---|---|---|---|---|
| `scripts/doctor.sh / scripts/status.sh` | L1-159 / 1-129 | 诊断输出脚本 | 模块 docstring | 诊断脚本输出面向运维/支持人员的稳定摘要，但脚本内无注释说明输出的稳定性约定（哪些行是解析依赖的稳定格式、新增诊断项时如何保持向后兼容），支持人员依赖的输出格式随时可能被无感改动。 | 在脚本头部注释输出契约：哪些字段是稳定解析格式、如何安全新增诊断项而不破坏既有消费者（如 status.sh 的摘要行）。 |

## 六、建议实施顺序

1. **契约层**（收益最大、成本低）：jobs/interface.py、workflows/interface.py、projects/access.py、excel_final/domain.py、material_routing.py、stage_adapter.py —— 每个文件补 10-20 行契约 docstring；
2. **领域语义与补偿路径**：AnalysisResult 无 attempt 列的约定、freeze 规范化哈希、outbox 失败结算、batch_exports 双门防误删、remnant execution 的 attempt 语义 —— 每个点 2-5 行注释；
3. **算法核心魔法数字**（需作者参与）：bh_geometry、bh_extractor、bh_constraints、title_block、analyzer 置信度公式、dxf2excel grid —— 数值来源与失败后果；
4. **模块 docstring**：remnant_inventory 四个核心文件、jobs/lifecycle.py、box/reader 两个 analyzer 类、remnant_drawing_reader 整包；
5. **测试意图**：把 5000/30/4096/60s 等裸常量改为引用生产常量并加一行说明；给两个最大测试文件补模块 docstring；
6. **低优先级**：轮询间隔、并发数、进度显示等前端魔法数字；死代码/误导性注释清理（dxf2excel grid 的两处、dwg2dxf health_check 的 XVFB_NOT_FOUND）。

> 注：`dxf2excel/src/dxf2excel/grid.py` 存在两处**注释与实现不一致**（10% vs 15% 阈值、`_adaptive_row_height_min` 收集未使用的数据），`dwg2dxf` 的 health_check 存在死代码（XVFB_NOT_FOUND 永不发出）——这类问题比缺注释更应优先处理。

## 七、审计附带发现（注释之外的真实问题，均经主代理核实）

| 位置 | 问题 | 影响 |
|---|---|---|
| `backend/app/modules/dxf_classification/models.py` L38 | `classifier_version` 列默认值 `"1.2.0"`，而 `adapter.py` L13 的 `CLASSIFIER_VERSION = "1.3.0"` | 版本字面量漂移；正常路径会显式赋值故暂不影响运行，但 README/模型/适配器三处版本描述不一致，是版本账本隐患 |
| `Stages/dxf2excel/src/dxf2excel/grid.py` L289-306 | `estimate_data_columns` docstring 声称"窄于中位列宽 10%"，实现与 README 均为 15% | 文档与实现漂移，维护者按错误阈值理解分隔列过滤行为 |
| `Stages/dxf2excel/src/dxf2excel/grid.py` L275-286 | `_adaptive_row_height_min` 收集排序 `h_lengths` 后从未使用，两分支无条件返回 `ROW_HEIGHT_MIN` | 误导性注释/死代码，读者以为实现了基于线长的自适应行高 |
| `Stages/dwg2dxf/src/dwg_converter/framework.py` L91-100 | `health_check` 计算 `xvfb_msgs` 未使用，两个分支都返回 `ODA_NOT_FOUND`，`XVFB_NOT_FOUND` 错误码永不发出 | 调用方（FastAPI 5xx 映射）无法区分"缺 ODA"与"缺 xvfb"两种环境错误 |
| `frontend/src/features/excel-processing/ExcelPreview.tsx` L41-68 | 快速切换 sheet 时旧请求响应可能覆盖新 sheet 数据（last-write-wins），无 AbortController 或请求序号 | 真实竞态：展示错误的 sheet 数据 |

