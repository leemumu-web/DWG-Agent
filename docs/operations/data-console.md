# 数据控制台运行手册

## 使用入口

登录平台后打开 `/data-console`。页面只保留两个工作区：

- MySQL：原生查看数据表、字段、主键与分页记录。
- MinIO：按 Bucket 和前缀逐级查看文件夹、文件、登记状态与文件详情。

admin 显示完整操作，可上传、下载、软删除、重命名或移动已登记对象。其他
登录用户只显示检查与下载能力，不能调用数据控制台的写接口。

## 配置和启动

```bash
docker compose --env-file .env.docker config --quiet
docker compose --env-file .env.docker up -d --build
```

数据控制台复用平台登录与现有应用数据库连接，不需要单独数据库账号或外部
管理器。真实配置文件 `.env.docker` 必须保持 Git 忽略且权限为 `0600`。

## 访问链

前端使用平台原生 `/api/v1/data-admin/mysql/tables` 接口读取表、字段、主键和
分页数据，不再嵌入外部数据库页面。所有表名和字段名先由 SQLAlchemy 反射确认，
敏感字段始终掩码显示：

- 管理员可以新增、编辑和删除带主键的数据行，变更写入审计日志；主键、自增列、
  密码、令牌和密钥类字段禁止通过通用控制台写入，应使用对应业务页面维护。
- 其他登录用户只能查看结构和数据。
- 无主键表只允许查看，字段类型错误和数据库约束冲突均以明确错误返回。

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
  tests/operations/test_data_admin_mysql_native.py
cd ../frontend && npm run build
```
