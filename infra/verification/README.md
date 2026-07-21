# 基础设施验证

`verify.sh` 从仓库根运行，对环境模板、Compose、Dockerfile、Nginx、MySQL 与本地活动依赖做非破坏性检查：

```bash
bash infra/verification/verify.sh
```

静态通过不等于服务启动或生产闭环。活动 MySQL 检查优先复用 `scripts/db.sh check` 的应用账号路径；仅在无交互 root 可用时追加 grants 审计，因此缺少 sudo 不会再被误报为数据库不可达。MinIO/ODA/Windows 依赖缺失时仍必须保留真实结果，不能把 skip 写成 pass。
脚本输出逐项区分 pass、fail 与 skip，并检查分类后的 gateway/database/storage/messaging/operations 路径仍被 Compose/脚本引用。正式发布还需结合 `docker compose config --quiet`、后端/Stage/前端测试以及 `scripts/status.sh` 的当前进程证据。
