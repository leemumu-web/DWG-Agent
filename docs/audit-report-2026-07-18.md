# 2026-07-18 全量审计报告

## 审计结论

`complete_framework` 已具备面向技术人员的 v0.1 初步开发版本基础：主要全栈路径存在，代码门禁覆盖广，详细中文文档可由源码契约校验。本轮将 `Stages/dxf2excel` 从无法还原的 gitlink 改为父仓库跟踪源码，并建立技术预览、贡献、变更和审计入口。

当前等级仍是**内部技术预览**，不是生产就绪。主要原因是 Compose 仅 HTTP、隔离空库迁移本轮被 sudo 阻塞、自动备份恢复/监控告警未交付、外部 CAD/Excel corpus 与许可未形成发布链路、仓库 LICENSE 未确定。

## 范围与方法

审查范围包括 459 个原父仓库跟踪项及当前工作树中的一方源码：

- FastAPI route/schema/service/model/migration、Celery task 与存储适配；
- React API client、状态管理、主要功能页面和 Playwright；
- Compose、Nginx、Dockerfile、MySQL/MinIO、环境模板和运维脚本；
- DWG→DXF、DXF→DWG、DXF→Excel、Excel Final Stage；
- 根文档、组件 README、`docs/`、生成 API 与文档检查器；
- Git 跟踪边界、secret/运行产物 ignore 和 clean-checkout 阻断。

方法包括源码/配置交叉核对、OpenAPI/Alembic 动态事实读取、快速与完整门禁、Stage 测试、浏览器回归、Git 索引检查和文档链接/声明检查。

## 关键发现与处置

| 严重度 | 发现 | 处置/状态 |
|---|---|---|
| P0 | `Stages/dxf2excel` 索引为 `160000` gitlink，缺 `.gitmodules` 且目标对象不存在；clean clone 无法恢复 backend path dependency | 已转为父仓库普通跟踪目录；跟踪源码、锁文件、最小测试和脚本；排除 204MB corpus、生成物、PDF、cache、venv |
| P1 | 根 README 当前 API 数仍为 91/110，而动态 OpenAPI 为 95/114 | 已校正，并让文档检查器动态拒绝再次漂移 |
| P1 | `CLAUDE.md` 当前迁移/表数仍为 `e4a1c7f2b930`、25/34；事实为 `d5e8a1c4b720`、28/37 | 已校正，并加入动态门禁 |
| P1 | 开发入口分散，full gate 未执行 DXF→Excel Stage 测试 | `Makefile` 增加 `verify-quick/full`；full gate 增加 DXF→Excel 必过项 |
| P1 | 缺少面向接手技术人员的版本定义、首次检出路径和当前审计总表 | 新增[技术预览指南](developer-preview.md)与本报告 |
| P1 | 仓库无 LICENSE，ODA/样本/第三方再分发边界未形成 | 不擅自选择许可证；在 README、预览指南和路线图中标为发布阻断 |
| P2 | 多份历史验证数字与当前数字并存，容易被误读为当前状态 | 保留带日期历史证据；当前入口统一指向 2026-07-18 审计和动态门禁 |
| P2 | 自动备份恢复、集中监控、容量、安全成熟度仍主要是文档基线 | 保留路线图，不在本轮伪造为已实现能力 |

## 实际验证证据

本轮在 `/home/Creeken/Paper/CAD_research/complete_framework` 当前工作树执行：

| 门禁 | 结果 | 说明 |
|---|---|---|
| 文档一致性 | pass | 中文文档集、生成 API、链接、端口、schema/head、仓库边界 |
| Ruff | pass | backend app/tests 与 verifier |
| 聚焦后端 | 201 passed | 脚本、Compose、文件/存储和前端契约 |
| 完整后端 | 937 passed / 6 skipped | 15 条 dependency/deprecation warning，无失败 |
| Alembic check | pass | `d5e8a1c4b720`，无新增 upgrade operation；仍有 drawings/version 循环 FK warning |
| 基础设施 | 82/82 pass | Nginx、Compose、Dockerfile、环境和文件契约；当时活动 MySQL 探针跳过 |
| Compose config | pass | 当前生产 Compose 可解析 |
| DWG→DXF Stage | 30 passed | 隔离 Stage 回归 |
| DXF→DWG Stage | 30 passed | 隔离 Stage 回归 |
| Excel Final Stage | 259 passed | `multi_split/tests` |
| 前端 build | pass | TypeScript 6 + Vite 8 production bundle |
| Playwright | 87 passed / 6 skipped | 共 93 场景；部分依赖活动 Job 或真实 XLS 样本的场景跳过 |
| 隔离 MySQL 空库迁移 | blocked | MySQL 可达，但非交互会话没有 sudo 凭据，未创建临时 schema |

DXF→Excel 在 gitlink 修复后的独立测试与最终全量门禁结果应以本任务结束时的交付报告为准。历史 419 文件/76,125 单元格证据没有在本轮重放，因为 corpus 不随源码仓库分发。

## 审计边界

- 未修改或清理真实业务数据、MySQL 表、MinIO 对象或扫描 finding。
- 未启用 Agent/CAD worker，也未实现明确排除的 CAD 业务算法。
- 未增加公网 TLS、自动备份、监控或许可证文本。
- 未把 SQLite/mocked Playwright 当作真实 MySQL/Celery/MinIO 生产证据。
- 未把 blocked 或 skipped 场景写成 pass。

开始审计前工作树已有 `Stages/dwg2dxf/convert/output_dxf/.gitkeep` 删除；Playwright 运行还更新了 `output/playwright/data-console-final.png`。这两项不是本轮功能成果，最终状态报告需单独列出，避免覆盖并发工作。

## 剩余发布风险

1. 在临时 clean checkout 中重放 backend/frontend 锁定安装、全部测试和镜像构建，证明 gitlink 修复后的真实可复现性。
2. 解决 TLS、Secure cookie、证书生命周期与公网边界。
3. 建立 MySQL + MinIO 协调备份/恢复、监控告警、容量和故障演练。
4. 为真实 DWG/DXF/Excel 建立带许可、摘要、预期输出和失败分类的 corpus。
5. 决定 LICENSE 与 ODA/数据/第三方组件分发策略。
6. 处理 Alembic 对 `drawings`/`drawing_versions` 循环 FK 的长期 warning，避免未来 SQLAlchemy/Alembic 升级变为错误。

后续优先级和验收条件见[路线图](roadmap.md)。
