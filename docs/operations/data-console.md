# 数据控制台运行手册

## 使用入口

登录平台后打开 `/data-console`。页面只保留两个工作区：

- MySQL：查看数据库、表、字段、索引与记录，使用 CloudBeaver Community。
- MinIO：按 Bucket 和前缀逐级查看文件夹、文件、登记状态与文件详情。

admin 显示完整操作，可上传、下载、软删除、重命名或移动已登记对象。其他
登录用户只显示检查与下载能力，不能调用数据控制台的写接口。

## 首次配置和启动

```bash
./scripts/configure-dba-console.sh
docker compose --env-file .env.docker config --quiet
docker compose --env-file .env.docker up -d --build
```

配置脚本只补齐缺失的 DBA 密钥，不覆盖已有值，也不打印密码。真实配置文件
`.env.docker` 必须保持 Git 忽略且权限为 `0600`。

`dba-bootstrap` 每次启动都会幂等校正两个数据库账号：

- `dwg_console_admin`：仅对 `dwg_agent.*` 有完整权限。
- `dwg_console_reader`：仅对 `dwg_agent.*` 有 `SELECT, SHOW VIEW`。

两个账号都没有全局权限，也不能读取 `hardware_handbook`。

## 访问链

前端先调用 `POST /api/v1/data-admin/mysql-sessions`，平台签发五分钟短时
HttpOnly cookie。Nginx 对 `/dba/mysql/` 的每次请求调用内部校验接口，并将
确认后的用户名与 `dba-admin` 或 `dba-reader` 团队传给 CloudBeaver。
CloudBeaver 仅加入 Compose internal 网络，不发布 8978 端口。

## MinIO 数据安全

- 上传复用平台文件校验、StoredFile 登记、FileTransfer 与审计。
- 删除是数据库软删除，底层对象保留供审计和恢复；生产冻结输入会拒绝删除。
- 重命名/移动先复制并校验目标，再条件更新登记并删除源对象；失败会写入
  `failed` 或 `compensation_required` 流转状态。
- 未登记对象只允许检查，不能从对象列表直接删除或移动；应先运行一致性扫描
  并通过预演/确认处置。

## 检查

```bash
docker compose --env-file .env.docker ps
curl -fsS http://127.0.0.1:${HTTP_PORT:-80}/nginx-health
cd backend && uv run pytest -q tests/operations/test_data_admin_api.py \
  tests/operations/test_data_admin_mysql_gateway.py
cd ../frontend && npm run build
```
