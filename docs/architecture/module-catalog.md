# 模块目录

[`module-catalog.json`](module-catalog.json) 是机器可检查的归属事实源，[`runtime-contract.json`](runtime-contract.json) 锁定重构期间不得变化的外部契约。本文解释模块边界，不重复 JSON 的每一条路径。

## 当前覆盖

| 模块 | 状态 | 主表 | HTTP operation | Celery task | 主要责任 |
|---|---:|---:|---:|---:|---|
| `identity` | implemented | 6 | 20 | 0 | 登录、token、用户、角色、权限 |
| `projects` | implemented | 4 | 17 | 0 | 项目成员、图纸与版本目录 |
| `files` | implemented | 4 | 17 | 0 | 上传、登记、下载、预览、对象补偿 |
| `jobs` | implemented | 4 | 18 | 1 | Job attempt、步骤、结果、复核、SSE |
| `workflows` | partial | 5 | 16 | 0 | 生产批次、输入冻结、阶段和产物编排 |
| `cad_processing` | partial | 0 | 0 | 5 | DWG/DXF 格式转换、DXF 预览解释与材料表提取 |
| `dxf_classification` | partial | 2 | 0 | 1 | Steel DXF Classifier 1.1.0 分类分流 |
| `excel_processing` | partial | 3 | 14 | 1 | Excel Final 与关系化导入 |
| `operations` | partial | 5 | 29 | 3 | 审计、数据控制台、控制平面、归档、扫描 |
| `automation` | placeholder | 3 | 4 | 0 | Agent 账本与只读/禁用契约 |
| `messaging_target` | placeholder | 0 | 0 | 0 | RabbitMQ、Outbox、Beat 的目标边界 |
| `windows_execution` | external | 0 | 0 | 0 | Node Agent、CAM Runner、SinoCAM Adapter |
| **合计** |  | **36** | **135** | **11** | 所有运行契约唯一归属 |

“HTTP operation 为 0”不表示模块不可用。例如 CAD 转换与分类由 Job/Workflow 公共端点触发，模块拥有任务和 Stage，而 HTTP 入口由 `jobs` 或 `workflows` 拥有。归属只设一个主 owner，避免同一契约由多个目录同时负责。

## 状态含义

- `implemented`：主要路径有实现和自动化测试，但仍可能需要外部依赖验收。
- `partial`：已有可执行纵向切片，同时存在默认关闭、真实样本或后续阶段缺口。
- `placeholder`：保留 schema、API、配置或错误契约，核心执行明确留白。
- `external`：仓库定义交接边界，执行进程或商业系统在仓库外。

当前 11 个任务名包含 `app.workers.tasks_dxf_classification.classify_steel_dxf`。早期人工基线误记为 10；运行时快照已纠正，后续以脚本从 Celery registry 读取的集合为准。

11 个稳定任务名目前由 7 个真实 Python task module 装配：CAD 的 5 个任务集中在
`cad_processing.tasks`，分类与 Excel Final 各一个，report stub 归 `jobs.tasks`；归档、对账与
stale recovery 分别归 daily archive、storage reconciliation 和 control plane。历史
`app.workers.tasks_*` 名称与队列不变；10 条 `pattern -> queue` 映射也进入
`runtime-contract.json`。空 Agent/CAD/dispatch task module 已删除，但对应路由仍保留，路由不
等于任务注册或核心实现。

`workflows` 的 5 张表和 16 个 operation 现集中在 `app/modules/workflows/`。模型/Schema、模板、
状态机、Job 同步、阶段执行计划、输入登记/转换/冻结/展示以及七类 route 均可从目录直接
追溯；人工输入仍严格为多个 DWG + 一个 Excel，DXF 只允许服务器派生。该归并没有增加
Celery task，也没有把四个 placeholder/external 阶段升级为已实现。

`operations` 现按 audit、daily archive、data catalog、storage reconciliation 和 control
plane 五个 owner 分层；`automation` 把已交付的三张表/会话记忆/API 与未实现的
Agent/MCP/ZWCAD/Windows 执行契约物理分开。旧 `app/api`、`models`、`schemas`、`services`、
`workers` 横向业务源码均已退出。扫描 run/finding 表仍由 files 拥有，operations 只经
`files.interface` 使用；目录迁移没有改变表 owner。

后端测试现以相同 owner 分层到 `backend/tests/<domain>/`，共享路径、会话和 workflow HTTP
构造器集中在 `backend/tests/support/`。根目录只保留全局 `conftest.py` 和 package 边界；
跨领域历史审计保留在 `regression/`，避免为了目录纯度拆散原始审计证据。架构测试同时检查
目录完整性和字符串 patch target，防止后续移动留下静默失效的 mock。

前端也按 owner 收拢为 11 个 `src/features/<feature>/` 目录。身份与通用会话基础分别位于
`features/identity` 和 `shared/auth`；CAD 转换、Excel Final、文件登记、Job、工作流与运维控制台
各自拥有 API、类型和页面。`src/app` 只从每个 feature 的 `index.ts` 组合路由，跨 feature
调用同样只允许经过该公共入口。旧顶层 `api/components/hooks/stores/types/utils` 已退出；
`npm run check:architecture` 与 production build 会阻止这些目录或私有跨域导入重新出现。

前端第二层分区也已落地：CAD 通用转换拆为上传、文件夹、总览和表格列模型，DXF→Excel
拆为上传与批次卡片，Excel 预览解析进入 model，数据控制台拆为六个面板；共享/feature 样式
按 owner 分文件。架构门禁同时限制 TypeScript 单文件不超过 600 行。Playwright 的 9 个
spec 按 7 个工作区归档，当前可收集 98 个浏览器用例。

仓库当前检查 134 个维护边界，均有就地 `README.md`，说明真实源码/接口、业务流、
依赖、输出与未实现边界。`scripts/architecture/check_partition_docs.py` 不再信任固定数量的
手工清单：backend、frontend、tests、infra、scripts 中直接拥有源码的目录会自动纳入，Stage、
Agent 与 Windows 外部产品边界显式纳入，并检查实质内容、至少一个真实文件名和能力边界；
该门禁已加入 `make architecture-check` 与统一 quick
验证，避免新增或移动分区后文档再次失联。

## 修改规则

1. 移动实现时先更新 JSON 路径，再执行 `make architecture-check`。
2. 路由、operationId、表、任务名、前端 URL 或 Compose 服务需要有意变更时，先写兼容性说明和测试；不得直接覆盖快照掩盖差异。
3. 新表、端点和任务必须有唯一主 owner；跨模块调用通过公开接口，不通过复制模型或双向导入解决。
4. placeholder/external 只有在实现、故障恢复、文档和真实验收证据齐备后才能升级状态。
5. `source_diagrams` 指向用户提供的当前结构图；干净克隆不依赖这些仓库外文件运行，节点映射保存在 catalog 中。

## 检查命令

```bash
make architecture-check
backend/.venv/bin/python scripts/architecture/snapshot_contracts.py --check
cd backend && .venv/bin/pytest -q tests/architecture
cd ../frontend && npm run check:architecture
```

检查器验证路径存在、数组确定性排序、36 张 ORM 表唯一归属、135 个 HTTP operation 唯一归属、11 个 Celery 任务唯一归属、10 条任务路由稳定，以及目标能力的显式状态。
