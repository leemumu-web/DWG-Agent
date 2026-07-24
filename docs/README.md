# 项目文档索引

本目录按“架构决策、稳定参考、操作指南、验证证据”分层。文档只陈述仓库当前可证明的能力；架构目标、接口留白和外部系统必须明确标注状态，不以占位文件冒充实现。

## 阅读顺序

1. [系统总览](architecture/overview.md)：运行拓扑、边界、数据与任务路径。
2. [领域词汇](../CONTEXT.md)：Production Batch、File Registry、Job、Workflow、Stage 等统一术语。
3. [Linux 生产工作流](architecture/workflow.md)：多个 DWG、单个 Excel、服务器生成 DXF、分类分流及后续留白契约。
4. [实现状态与差距](architecture/implementation-status.md)：已实现、部分实现、占位和外部能力的证据。
5. [当前验证证据](verification/current.md)：各层测试与尚未执行的真实环境验收。
6. [排版整理算法说明书](../Stages/excel_final/排版整理算法说明书.md)：面向排版人员的 Excel 计算、拆板、五金手册查询和人工核验规则。

## 架构

| 文档 | 负责回答 |
|---|---|
| [系统总览](architecture/overview.md) | 组件如何协作，哪一层拥有什么责任 |
| [平台技术规范](architecture/platform-specification.md) | 必须长期保持的产品、数据、安全和部署约束 |
| [Linux 生产工作流](architecture/workflow.md) | 批次、阶段、输入输出、状态机和留白接口 |
| [实现状态与差距](architecture/implementation-status.md) | 目标架构中哪些已经有代码和证据 |
| [模块目录](architecture/module-catalog.md) | 每个表、HTTP、任务、Stage、前端和测试由谁负责 |
| [架构追溯矩阵](architecture/traceability.md) | 结构图节点如何落到当前模块与真实状态 |
| [领域重构设计](superpowers/specs/2026-07-21-repository-domain-reorganization-design.md) | 本轮仓库分类原则和目标目录 |
| [领域重构计划](superpowers/plans/2026-07-21-repository-domain-reorganization.md) | 可回滚的实施顺序与验收门禁 |

## 稳定参考

| 文档 | 权威来源 |
|---|---|
| [API 参考](reference/api.md) | FastAPI OpenAPI；由脚本生成，不手工维护路由表 |
| [数据库参考](reference/database.md) | SQLAlchemy 模型、Alembic、Celery SQL runtime 表 |
| [配置参考](reference/configuration.md) | Pydantic Settings、环境模板和 Compose 覆盖 |

## 操作指南

| 文档 | 适用对象 |
|---|---|
| [开发指南](guides/development.md) | 本地开发、测试、迁移和提交前检查 |
| [部署指南](guides/deployment.md) | 当前 Compose 拓扑、密钥、启动与验收 |
| [运维指南](guides/operations.md) | 状态检查、归档、备份恢复和事故处理 |
| [安全指南](guides/security.md) | 信任边界、授权、文件安全和剩余风险 |
| [排版整理算法说明书](../Stages/excel_final/排版整理算法说明书.md) | 非程序员排版人员；只讲 Excel 处理算法、公式和核验 |

## 文档门禁

修改端点后重新生成 API 文档，再运行一致性检查：

```bash
make docs-generate
make docs-check
```

检查器验证必需文档、全部维护 README 的相对链接与 Markdown 结构、API 生成结果、数据库连接池默认值、迁移 head、端口和生产文档开关。架构门禁另检查 134 个源码/产品边界的 README 是否有实质业务内容、引用至少一个真实源码文件（存在源码时）并声明能力边界；缺失文件会作为普通检查错误列出，不应产生 traceback。

## 状态表达规则

- `implemented`：存在可调用实现和对应自动化证据。
- `partial`：主路径可用，但仍缺少生产依赖或完整验收。
- `placeholder`：只保留 API、schema、输入输出或错误契约，核心算法留白。
- `external`：能力由仓库外系统承担，当前只定义交接协议。

RabbitMQ、Outbox、Celery Beat、Windows Node Agent、CAM Runner 与 SinoCAM Adapter 当前属于目标能力；除非 Compose、实现、恢复测试和运维证据同时存在，不得写成已部署。
