# 余料库与上游 Excel 工作流集成设计

## 目标

将当前余料库功能分支适配到 `Creeken-Harrans/DWG-Agent:main` 最新提交，在不改写已发布历史、不强推且不丢失双方功能的前提下，形成无 Git 冲突、数据库只有一个 Alembic head、测试通过并可向上游自动合并的 Pull Request。

集成范围包括当前尚未提交的余料附加信息安全修复，以及面向工人和运维人员的完整余料库使用说明。

## 仓库与分支策略

- 上游基线：`Creeken-Harrans/DWG-Agent:main@eff938e`。
- 功能来源：当前 `codex/remnant-auto-import` 分支及其未提交修复。
- 交付分支：`codex/remnant-upstream-integration-2026-07-24`。
- 推送仓库：`Ranbaixin/DWG-Agent-left-and-right-reader`。
- PR 目标：`Creeken-Harrans/DWG-Agent:main`。

先完成当前余料附加信息修复的测试与提交，再从当前分支创建交付分支并执行三方 merge。不得 rebase 已推送提交、不得强推、不得使用整文件 `ours` 或 `theirs` 覆盖语义冲突。

## 功能归属

- Excel、工作流 Stage 1、生产输入验证和 Excel Final 逻辑以上游实现为准。
- 余料材质、导入、解析、库存生命周期、预览、下载、导出和删除后重导以当前分支实现为准。
- 共用基础设施、契约快照、迁移测试和参考文档必须组合双方能力。
- 当前余料附加信息修复必须保留：
  - 批量更新只修改工人明确选择的字段；
  - 显式空值代表清空，省略字段代表保持原值；
  - 空批量操作返回中文错误且不写无效审计；
  - Excel 导出将公式型文本作为普通文本处理。

## 冲突解决

已确认的文本冲突集中在：

- `backend/tests/architecture/test_contract_snapshot.py`
- `backend/tests/infrastructure/test_migrations.py`
- `docs/architecture/runtime-contract.json`
- `docs/reference/api.md`
- `docs/reference/database.md`

处理规则：

- 迁移测试同时保留余料迁移和上游工作流迁移断言。
- 运行时契约由合并后代码重新生成，不手工删除任一路由、表、任务、队列或 Compose 服务。
- API 和数据库参考文档同时记录 Excel 工作流与余料库。
- 合并结束后必须扫描并确认不存在冲突标记。

## 数据库迁移

合并后会形成两个合法 head：

- `6f4a8c2d1e90`：余料自动导入及附加库存信息。
- `5f8d3b0c2e41`：Linux Excel Stage 规范化。

新增一个无 DDL 的 Alembic merge revision：

- `down_revision = ("6f4a8c2d1e90", "5f8d3b0c2e41")`
- `upgrade()` 和 `downgrade()` 均不执行 DDL。

迁移测试必须继续使用 DAG 规则验证一个 base、一个 head、无循环、所有父 revision 存在且所有 revision 可达。

数据库验收使用临时 MySQL schema：

1. 空 schema 执行 `upgrade head`。
2. 从 `6f4a8c2d1e90` 升级到新 head。
3. 从 `5f8d3b0c2e41` 升级到新 head。
4. 验证完成后删除临时 schema。

不得升级或清空当前本地验收数据库。

## 余料库使用说明

新增 `docs/guides/remnant-inventory.md`，并从根 `README.md` 与 `docs/README.md` 链接。说明必须覆盖：

- `REMNANT_INVENTORY_ENABLED` 功能开关；
- `remnant_worker`、管理员和超级管理员权限边界；
- 普通批量导入 DWG/DXF；
- 自动导入、递归文件夹导入和文件夹名项目编号预填；
- `offcut_zh_cn` 块的 `GG`、`CZ`、`YLBH` 字段格式；
- 厚度、项目编号一/二、库存位置、备注一/二和多个零件编号；
- 新材质自动创建、停用材质自动启用和材质管理；
- 全局查询、独立字段筛选、预占、释放、领用、归档和已归档删除；
- 删除后同一源图重新提交；
- 在线预览、原始 DWG/DXF 下载和 Excel 全量导出；
- `remnant_convert`、`remnant_parse` 队列、部署检查和常见中文错误。

现有 `docs/operations/remnant-inventory.md` 保留为上线与运维手册，新的 guide 面向日常使用；两者相互链接，避免重复复制部署细节。

## 验证

静态与架构：

- 无冲突标记；
- `git diff --check`；
- Ruff；
- 前端架构检查与生产构建；
- 运行时契约快照匹配；
- Alembic 只有一个 head。

后端与 Stage：

- 完整余料后端测试；
- architecture、infrastructure、workflows、excel_processing、files 和 CAD preview 重点测试；
- `Stages/remnant_drawing_reader` 完整测试；
- 上游 Excel Stage 1 和 Excel Final 测试。

网页：

- 在独立端口运行余料检索、全部余料、批量导入、自动导入、材质管理和预览 Playwright；
- 运行上游 Excel 工作流相关 Playwright；
- 不使用旧容器页面代替当前源码验收。

容器：

- 校验 Compose 配置；
- 构建后端、Nginx、Excel worker 和余料 worker 镜像；
- 不启动会迁移现有数据库的完整服务栈。

## PR 交付

推送交付分支后，向 `Creeken-Harrans/DWG-Agent:main` 创建非草稿 PR。PR 描述必须包含：

- 上游基线和余料提交范围；
- 两个迁移 head 的汇合方式；
- 余料库主要功能与使用说明链接；
- 完整测试证据；
- 已知但不阻塞合并的环境限制。

PR 创建后检查 GitHub mergeability 和 CI。CI 全部通过时尝试启用 merge commit 方式的自动合并；若上游权限或仓库设置不允许，仅报告限制，不绕过保护规则或直接推送上游 `main`。

