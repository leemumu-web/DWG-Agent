# DBA 数据控制台设计

## 1. 目标

数据控制台只提供两个主要工作区：

1. `MySQL`：查看数据库结构和真实行数据，并按权限执行增删改查；
2. `MinIO`：查看 Bucket/对象结构和内容预览，并按权限执行对象增删改查。

界面服务 DBA 集中检查和维护数据，不承担生产批次调度，不新增复杂血缘图、管理驾驶舱或任意业务流程执行入口。

## 2. 开源复用决策

### 2.1 MySQL 使用 CloudBeaver Community

CloudBeaver Community 使用 Apache-2.0 许可，提供成熟的 MySQL Navigator、字段/索引/外键结构查看、数据编辑、筛选和 SQL 工具。本项目不重复实现通用数据库编辑器。

CloudBeaver 作为独立 Compose 服务运行，通过 Nginx 的 `/dba/mysql/` 路径访问。它使用独立工作区卷，不把自身元数据表写入 `dwg_agent` 业务库。

### 2.2 MinIO 使用现有平台能力

不引入 Filestash。Filestash 虽支持 S3/MinIO，但不能可靠嵌入子路径/iframe，且直接写对象会绕过本平台的 `files`、`file_transfers`、审计和补偿规则。

MinIO 工作区继续使用现有 React、FastAPI、storage adapter、签名下载、DXF 预览和一致性处置能力，在此基础上补齐目录、预览和受控写操作。

### 2.3 不采用 Directus/NocoDB

当前版本许可不是标准开源许可，并会引入自己的元数据或附件模型。本项目不让第二套数据模型覆盖既有 MySQL/MinIO 事实源。

## 3. 权限

权限只有两级：

- `admin`：拥有 `dwg_agent` 业务库内的 MySQL 完整权限，以及 MinIO 上传、移动/重命名、下载和受控删除权限；
- 其他已登录用户：MySQL 与 MinIO 均只读，只能检查结构、数据、元数据和预览内容。

CloudBeaver 使用反向代理身份：

- 平台生成短期、HttpOnly、仅限 `/dba/mysql/` 的 DBA 网关会话；
- Nginx 通过后端鉴权端点校验会话，并向 CloudBeaver 传递用户和 `admin` / `reader` team；
- `admin` team 只能访问读写 MySQL connection；
- `reader` team 只能访问只读 MySQL connection。

MySQL 凭据使用单独环境变量和数据库用户：

- `dwg_console_admin`：`dwg_agent.*` 范围内完整权限，不拥有全局 root、授权管理或其他 schema 权限；
- `dwg_console_reader`：`SELECT`、`SHOW VIEW` 权限。

凭据不进入前端、不写进 Git、不在日志中输出。

## 4. 前端结构

主入口调整为所有已登录用户可访问的 `/data-console`，只读/读写能力由角色决定。
原 `/admin/infrastructure` 保留重定向兼容。页面只显示两个一级入口：

### 4.1 MySQL

- 显示 MySQL 是否可达、当前 schema、表数量和只读/读写身份；
- “打开 MySQL 管理器”创建短期网关会话并进入 `/dba/mysql/`；
- CloudBeaver 内显示 schema/table tree、columns、indexes、foreign keys、constraints 和行数据；
- reader 看不到数据修改与 DDL 权限，admin 在 `dwg_agent` 范围内可执行 CRUD。

### 4.2 MinIO

- 左侧显示配置的 Bucket 和对象前缀树；
- 右侧显示对象列表和元数据；
- 预览支持图片、PDF、UTF-8 文本、JSON、CSV 和已实现的 DXF；
- 其他类型显示有限元数据并提供签名下载；
- admin 显示上传、移动/重命名、下载、软删除或清理入口；
- reader 只显示列表、元数据、预览和下载。

现有文件登记、对象列表、流转流水、归档和一致性功能收进 MinIO 工作区的二级页签，不再占用九个一级页签。审计日志继续保留独立页面入口。

## 5. MinIO 数据规则

MinIO 不是独立文件盘，所有写操作必须维护 MySQL 事实：

### 5.1 新增

上传必须：

1. 校验 Bucket、对象键、扩展名、大小和内容；
2. 建立 `file_transfers` inbound 记录；
3. 写入对象；
4. 建立 `files` 登记；
5. 结算 transfer；
6. 失败时执行现有补偿。

不提供“只写 MinIO、不登记 MySQL”的普通上传。

### 5.2 查询与预览

- 已登记对象复用现有文件权限和签名下载；
- 未登记对象只允许 admin 查看元数据，内容预览必须通过有界、审计化的 data-admin 端点；
- 文本/JSON/CSV 预览限制字节数和行数；
- DXF 继续使用现有安全 SVG 预览，不执行任意脚本。

### 5.3 修改

对象存储没有原地重命名。移动/重命名按以下事务执行：

1. 预检源对象、目标键冲突和数据库引用；
2. 创建 internal transfer；
3. copy 到目标键并校验大小/摘要；
4. 条件更新 `files.storage_key`；
5. 删除源对象；
6. 结算 transfer；
7. 任一步失败进入补偿状态，不伪装成功。

### 5.4 删除

- 已登记对象走文件软删除和保留规则；
- 被 Workflow/Artifact/冻结输入引用的文件禁止删除；
- 未登记对象只能通过既有 consistency finding 的 preview、确认词和幂等 remediation 清理；
- 不提供无预检的批量永久删除。

## 6. 接口边界

新增或扩展接口：

- `POST /api/v1/data-admin/mysql-sessions`：创建短期 DBA 网关会话；
- `GET /api/v1/data-admin/mysql-session`：Nginx auth request 校验；
- `GET /api/v1/data-admin/objects/tree`：Bucket/前缀树；
- `GET /api/v1/data-admin/objects/preview`：受限对象预览；
- `POST /api/v1/data-admin/objects`：上传并登记；
- `POST /api/v1/data-admin/objects/move-preview`：移动预检；
- `POST /api/v1/data-admin/objects/moves`：执行幂等移动。

读取端点允许所有已登录用户；写端点只允许 admin。原有公开文件、下载、transfer、scan 和 remediation 接口继续作为事实源，不复制业务规则。

## 7. 部署

Compose 增加一个 `cloudbeaver` 服务和独立 workspace volume：

- 镜像使用明确版本或 digest；
- 只连接应用内部网络；
- 不直接发布宿主端口；
- 通过 Nginx `/dba/mysql/` 访问；
- 健康检查验证 CloudBeaver HTTP；
- 数据库连接只指向单一 `mysql` 服务；
- CloudBeaver 内部工作区不使用 `dwg_agent` 业务 schema。

本地运行仍使用同一 MySQL 与同一 MinIO，不启动第二套数据库或对象存储。

## 8. 错误与审计

- 结构和列表读取失败保留上次成功内容，并显示有限错误；
- 写操作返回稳定错误码、request ID 和修复建议；
- MySQL 网关会话创建、MinIO 上传、移动和删除均写审计；
- 日志不得包含 MySQL 密码、MinIO secret、签名 URL 或对象内容；
- CloudBeaver SQL 行为由其 query history 保存；平台记录 DBA 网关登录和角色，不伪造逐 SQL 审计。

## 9. 验收

### 9.1 MySQL

- admin 可查看结构并对隔离测试表完成 create/read/update/delete；
- reader 可查看结构和行，但写入被 MySQL 权限拒绝；
- CloudBeaver 不能访问 `hardware_handbook` 或其他未授权 schema；
- 平台会话过期后无法继续新建 CloudBeaver 会话。

### 9.2 MinIO

- admin 可上传并看到对象与 `files`/`file_transfers` 同步登记；
- 支持对象结构、元数据及安全格式预览；
- move 后对象字节和摘要不变，MySQL storage key 同步；
- 被引用对象删除失败关闭；
- 未登记对象只能走一致性处置；
- reader 的所有写请求返回 403。

### 9.3 回归

- 后端 CRUD、权限、补偿和审计测试；
- Compose、Nginx、运行时快照与文档合同；
- 前端生产构建；
- MySQL/MinIO 数据控制台 Playwright；
- 真实 MySQL、MinIO、Nginx 和浏览器启动验证。
