# DBA Data Console Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 将现有数据控制台收敛为 MySQL 与 MinIO 两个工作区，让所有登录用户可检查结构和数据，admin 可在受控边界内完成增删改查。

**Architecture:** MySQL 工作区复用 CloudBeaver Community，并通过平台签发的短时会话、Nginx `auth_request` 和两个最小权限数据库账号区分读写。MinIO 工作区继续复用平台现有 StoredFile、FileTransfer、预览、审计和一致性处置能力，在此基础上增加目录树、对象预览和受控写操作，避免绕过业务数据库。

**Tech Stack:** FastAPI、SQLAlchemy、MySQL 8.4、MinIO、CloudBeaver Community、Nginx、React、TypeScript、Ant Design、TanStack Query、pytest、Vitest、Playwright、Docker Compose。

---

## Task 1: 收拢路由与权限边界

**Files:**
- Modify: `backend/app/modules/operations/data_catalog/routes.py`
- Modify: `backend/app/modules/operations/data_catalog/system_routes.py`
- Modify: `backend/app/modules/operations/daily_archive/routes.py`
- Modify: `backend/app/modules/operations/storage_reconciliation/routes.py`
- Modify: `frontend/src/app/router.tsx`
- Modify: `frontend/src/app/layout.tsx`
- Test: `backend/tests/api/test_data_admin.py`
- Test: `frontend/src/app/router.test.tsx`

1. 先写失败测试：任意已登录用户可以读取数据概览、对象结构和详情；非 admin 调用扫描、归档、处置及后续对象写接口返回 403。
2. 将读依赖改为 `CurrentUser`，写依赖保持 `require_roles(ROLE_ADMIN)`；不得把原有管理写接口开放给普通用户。
3. 新增主入口 `/data-console`，旧 `/admin/infrastructure` 重定向到新入口。
4. 导航对所有登录用户显示“数据控制台”，页面根据角色明确展示“完整操作”或“只读检查”。
5. 运行后端权限测试、前端路由测试并提交。

## Task 2: MySQL 短时网关会话

**Files:**
- Create: `backend/app/modules/operations/data_catalog/mysql_gateway.py`
- Create: `backend/app/modules/operations/data_catalog/mysql_routes.py`
- Modify: `backend/app/modules/operations/data_catalog/router.py`
- Modify: `backend/app/platform/config/settings.py`
- Modify: `.env.example`
- Modify: `.env.docker.example`
- Test: `backend/tests/api/test_data_admin_mysql_gateway.py`

1. 先写失败测试，覆盖登录要求、admin/reader 团队映射、短时过期、篡改拒绝、HttpOnly/Path/SameSite cookie。
2. 复用平台签名能力签发最长五分钟的 `dwg_dba_token`，只包含用户 ID、团队和过期时间。
3. 实现 `POST /api/v1/data-admin/mysql-sessions` 和供 Nginx 内部校验的 `GET /api/v1/data-admin/mysql-session`。
4. 校验响应仅返回 CloudBeaver 需要的 `X-User`、`X-Team`，不返回数据库密码。
5. 运行定向测试和身份模块回归并提交。

## Task 3: CloudBeaver 与 MySQL 最小权限账号

**Files:**
- Create: `infra/cloudbeaver/initial-data-sources.conf`
- Create: `infra/cloudbeaver/cloudbeaver.conf`
- Create: `infra/database/mysql/dba-users.sh`
- Modify: `compose.yaml`
- Modify: `infra/gateway/nginx/nginx.conf`
- Modify: `infra/gateway/nginx/nginx.local.conf`
- Modify: `.env.docker.example`
- Modify: `scripts/verify-infrastructure.sh`
- Test: `backend/tests/infrastructure/test_compose_contract.py`
- Test: `backend/tests/infrastructure/test_nginx_contract.py`

1. 先写静态契约测试，固定 CloudBeaver 版本、内部网络、根路径 `/dba/mysql/`、反向代理认证和无宿主机端口暴露。
2. 新增幂等账号初始化：`dwg_console_admin` 仅对 `dwg_agent.*` 拥有完整权限，`dwg_console_reader` 仅有 `SELECT, SHOW VIEW`。
3. 配置两个 CloudBeaver 数据源/团队绑定，密码只从环境变量读取，不写入仓库。
4. Nginx 对 `/dba/mysql/` 使用 `auth_request`，将平台校验结果转发为 CloudBeaver 反向代理用户与团队头。
5. 增加健康检查、持久化 workspace volume 和启动依赖。
6. 运行 Compose 渲染、Nginx 配置与基础设施契约测试并提交。

## Task 4: 数据控制台双工作区前端

**Files:**
- Modify: `frontend/src/features/operations/pages/InfrastructurePage.tsx`
- Create: `frontend/src/features/operations/components/data-console/MySqlWorkspace.tsx`
- Modify: `frontend/src/features/operations/components/data-console/ObjectsPanel.tsx`
- Modify: `frontend/src/features/operations/styles.css`
- Modify: `frontend/src/features/operations/api/dataAdmin.ts`
- Modify: `frontend/src/features/operations/types/dataAdmin.ts`
- Test: `frontend/src/features/operations/pages/InfrastructurePage.test.tsx`

1. 先写失败测试：页面只有 MySQL、MinIO 两个主工作区；角色提示清晰；MySQL 按钮先创建会话再进入嵌入工作区。
2. MySQL 工作区以简洁卡片显示连接范围、权限、状态和“打开数据库管理器”；内嵌失败时提供同路径新窗口打开。
3. MinIO 工作区保留对象操作所需的信息，将登记、流转、一致性检查作为次级抽屉或摘要，不再占据多个主标签。
4. 保持响应式布局、键盘可达和明确的加载/错误/空状态。
5. 运行 Vitest、类型检查和构建并提交。

## Task 5: MinIO Bucket/前缀结构树

**Files:**
- Create: `backend/app/modules/operations/data_catalog/object_browser.py`
- Create: `backend/app/modules/operations/data_catalog/schemas.py`
- Modify: `backend/app/modules/operations/data_catalog/routes.py`
- Modify: `backend/app/platform/storage/base.py`
- Modify: `backend/app/platform/storage/minio.py`
- Modify: `backend/app/platform/storage/local.py`
- Modify: `frontend/src/features/operations/components/data-console/ObjectsPanel.tsx`
- Test: `backend/tests/api/test_data_admin_object_browser.py`
- Test: `backend/tests/unit/test_storage_backends.py`
- Test: `frontend/src/features/operations/components/data-console/ObjectsPanel.test.tsx`

1. 先写失败测试，覆盖 Bucket 根节点、直接子目录去重、直接文件、深层前缀、空目录视图和游标。
2. 在存储抽象中增加分隔符目录列举能力；MinIO 使用原生 prefix/delimiter，Local 后端保持相同行为。
3. 实现 `GET /api/v1/data-admin/objects/tree`，返回稳定的文件夹和文件节点，不把 JSON/CSV 辅助清单冒充目录。
4. 前端左侧展示 Bucket/目录树，右侧展示当前目录对象和登记状态；面包屑可快速回到任意上级。
5. 运行后端、前端定向测试并提交。

## Task 6: MinIO 对象结构和内容预览

**Files:**
- Create: `backend/app/modules/operations/data_catalog/object_preview.py`
- Modify: `backend/app/modules/operations/data_catalog/routes.py`
- Modify: `frontend/src/features/operations/components/data-console/ObjectPreviewDrawer.tsx`
- Modify: `frontend/src/features/operations/components/data-console/ObjectsPanel.tsx`
- Test: `backend/tests/api/test_data_admin_object_preview.py`
- Test: `frontend/src/features/operations/components/data-console/ObjectPreviewDrawer.test.tsx`

1. 先写失败测试，覆盖图片、PDF、文本、JSON、CSV、DXF、未知二进制和大小上限。
2. 已登记 DXF/Excel 复用现有文件预览接口；JSON/CSV/文本仅流式读取受限前缀，返回截断标志和结构摘要。
3. 图片/PDF 通过经过权限复核的短时内容地址预览；未知或过大的对象只展示元数据和下载。
4. 实现 `GET /api/v1/data-admin/objects/preview`，严格校验 Bucket、Key、内容类型和读取上限。
5. 前端抽屉显示元数据、登记关联、结构摘要与适配的内容预览，不直接渲染不可信 HTML/SVG。
6. 运行安全和预览测试并提交。

## Task 7: Admin 上传、下载与受控删除

**Files:**
- Create: `backend/app/modules/operations/data_catalog/object_mutations.py`
- Modify: `backend/app/modules/operations/data_catalog/routes.py`
- Modify: `frontend/src/features/operations/components/data-console/ObjectsPanel.tsx`
- Test: `backend/tests/api/test_data_admin_object_mutations.py`
- Test: `frontend/src/features/operations/components/data-console/ObjectsPanel.test.tsx`

1. 先写失败测试，覆盖 reader 全部 403、admin 上传后同时产生对象/StoredFile/FileTransfer/审计、下载流水、受保护文件拒绝删除。
2. 上传复用文件名、MIME、DXF/DWG 校验和现有存储事务，成功后登记 `StoredFile`。
3. 下载复用已有受控流式下载与 `FileTransfer`。
4. 已登记对象删除走 `soft_delete_file_in_transaction`；被工作流、冻结产物或其他引用使用时拒绝。
5. 未登记对象不提供直接删除按钮，只引导到一致性扫描后的 preview/execute 处置。
6. 前端 admin 显示上传、下载、删除；reader 只显示预览和下载（下载为读取操作）。
7. 运行事务、补偿、权限和审计测试并提交。

## Task 8: Admin 重命名/移动

**Files:**
- Modify: `backend/app/modules/operations/data_catalog/object_mutations.py`
- Modify: `backend/app/modules/operations/data_catalog/schemas.py`
- Modify: `backend/app/modules/operations/data_catalog/routes.py`
- Modify: `backend/app/platform/storage/base.py`
- Modify: `backend/app/platform/storage/minio.py`
- Modify: `backend/app/platform/storage/local.py`
- Modify: `frontend/src/features/operations/components/data-console/ObjectsPanel.tsx`
- Test: `backend/tests/api/test_data_admin_object_move.py`

1. 先写失败测试，覆盖同 Bucket/跨 Bucket、目标冲突、并发版本变化、复制失败、校验失败、源删除失败和补偿状态。
2. 实现 `POST /objects/move-preview`，返回源登记、引用风险、目标冲突与确认 token。
3. 实现 `POST /objects/moves`：复制、stat 校验、带旧 Key 条件的数据库更新、删除源对象、记录 transfer/audit；任一步失败均明确记录补偿状态。
4. 前端以预览确认对话框完成重命名/移动，成功后刷新树、列表与登记详情。
5. 运行故障注入和并发测试并提交。

## Task 9: 配置、启动与上线门禁

**Files:**
- Modify: `README.md`
- Modify: `docs/operations/deployment.md`
- Modify: `docs/operations/troubleshooting.md`
- Modify: `scripts/verify.sh`
- Modify: `scripts/verify-infrastructure.sh`
- Modify: runtime snapshots/catalog files discovered by verification
- Test: `frontend/tests/e2e/operations/data-console.spec.ts`

1. 更新配置样例、首次启动和现有 MySQL volume 的 DBA 账号初始化说明，禁止记录真实密码。
2. 增加 E2E：reader 能浏览 MySQL/MinIO 结构但无写入口；admin 能创建 MySQL 会话、上传并预览 MinIO 对象、查看登记与流水。
3. 执行后端完整测试、前端 lint/typecheck/test/build、Compose 渲染、Nginx 检查和仓库 `scripts/verify.sh full`。
4. 启动真实 Compose，验证 `/data-console`、`/dba/mysql/`、对象树、预览、admin 写入与 reader 拒绝。
5. 检查无 secrets、无用户目录改动、工作树仅包含目标变更。
6. 提交最终文档/修复，推送两个有远程的仓库；没有远程的独立分类器保持原状并明确报告。
