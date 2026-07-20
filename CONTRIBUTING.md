# 贡献指南

## 开始之前

本项目当前是内部 v0.1 技术预览版。先阅读[开发指南](docs/guides/development.md)、[企业平台技术规范](docs/architecture/platform-specification.md)和受影响子系统文档。不要通过启用占位 Agent/CAD worker、增加内存 fallback 或放宽授权来让测试通过。

```bash
cd backend && uv sync --locked && cd ..
cd frontend && npm ci && cd ..
make verify-quick
```

## 分层规则

- FastAPI route 处理 HTTP schema/dependency；service 处理事务、不变量和权限；Celery task 调用 service。
- 所有 Job claim、progress、terminal、cancel、retry 和补偿更新必须匹配当前 status + attempt。
- storage adapter 管字节；MySQL 管权限元数据、SHA-256、Job 和流转事实。
- SQL 列表先按资源权限过滤再分页；不要改成 Python 逐行授权或 N+1 查询。
- 前端 guard 只改善交互，不能替代 API authorization。
- Redis/Valkey 不是当前依赖，也不能作为正确性 fallback。

## 变更要求

### API

1. 增加/修改 schema、service、route 和权限负例测试。
2. 运行 `make docs-generate` 更新生成的 `docs/reference/api.md`。
3. 更新相关中文文档并运行 `make docs-check`。

### 数据库

```bash
cd backend
uv run alembic revision --autogenerate -m "description"
uv run alembic check
cd ..
bash scripts/db.sh migration-test
```

检查自动生成操作、唯一约束、MySQL 类型与循环外键。不要修改已经发布的历史 migration 来制造单一 head。

### Stage

Stage 源码、锁文件和最小测试可以跟踪；真实 corpus、ODA 许可资产、生成工作簿、cache 和 virtualenv 不得因测试方便进入提交。平台调用契约写入[Linux 生产工作流](docs/architecture/workflow.md)，算法细节保留在 Stage README。

### 前端

运行 `npm run build` 和受影响 Playwright。SSE/polling 必须在终态收敛；401 refresh 不能递归；下载重试必须获取新签名；错误界面保留服务端安全 message/code/request ID。

## 验证

```bash
# 日常
make verify-quick

# 发布候选
make verify-full
# 外部依赖确实不可用时，可用于审计记录：
bash scripts/verify.sh full --allow-blocked
```

`--allow-blocked` 不会放过代码、文档、构建或必过测试失败。提交说明必须列出实际 pass、skip 和 blocked，不能只写“全部通过”。

## 文档

只维护根入口、组件 README 和 `docs/` 分类目录中的中文文档。`docs/reference/api.md` 是生成文件，不手改。能力声明须分别说明：代码是否存在、默认 flag、外部依赖、验证环境/日期和剩余限制。

## 安全与仓库卫生

禁止提交 `.env`、`.env.docker`、密钥、令牌、签名 URL、真实数据库、对象存储、`Data/`、浏览器 trace、日志、虚拟环境和生成测试输出。客户端错误不得包含 traceback、child stderr、DSN、secret、主机路径或存储签名凭据。

仓库尚未声明 LICENSE。除非负责人确认授权和第三方/样本分发边界，不得把内部源码或数据发布到外部服务，也不得声称项目是开源软件。
