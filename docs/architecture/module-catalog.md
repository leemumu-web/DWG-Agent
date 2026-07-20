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

11 个稳定任务名目前由 8 个 Python task module 装配；CAD 的 5 个任务集中在 `cad_processing.tasks`，分类任务集中在 `dxf_classification.tasks`。这是实现文件归并，不改变已入队消息使用的历史任务名和队列。

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
```

检查器验证路径存在、数组确定性排序、36 张 ORM 表唯一归属、135 个 HTTP operation 唯一归属、11 个 Celery 任务唯一归属，以及目标能力的显式状态。
