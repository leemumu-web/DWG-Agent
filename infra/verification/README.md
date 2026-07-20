# 基础设施验证

`verify.sh` 从仓库根运行，对环境模板、Compose、Dockerfile、Nginx、MySQL 与本地活动依赖做非破坏性检查：

```bash
bash infra/verification/verify.sh
```

静态通过不等于服务启动或生产闭环；活动 MySQL/MinIO/ODA/Windows 依赖缺失时必须保留真实结果，不能把 skip 写成 pass。
