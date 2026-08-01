# 分层持续集成设计

## 目标

为 `complete_framework` 建立可作为主分支合入门禁的 GitHub Actions 持续集成体系。CI 必须覆盖 Python、前端、独立 Stage、架构契约和生产容器形态，并能明确指出失败所属层级。CI 只验证、不发布镜像或服务器安装包，也不接触生产密钥、生产数据库和生产对象存储。

## 当前基线

- 仓库当前没有 `.github/workflows`。
- 后端固定使用 Python 3.12 和 `backend/uv.lock`，统一入口为 `scripts/verify.sh`。
- 前端使用 Node、npm 锁文件、TypeScript/Vite 和 Playwright。
- 多个 Stage 是独立 uv 工程；部分 Stage 通过后端锁定的本地路径依赖接受集成测试。
- 生产容器包含 Nginx、FastAPI、dispatcher、MySQL、MinIO 和 11 个 worker。生产卷名称固定，不能直接复用于长期存在的 CI runner。
- 后端、Stage 和容器构建依赖仓库内受版本控制的 ODA AppImage；CI 不从外部下载或替换该二进制。

## 非目标

- 不推送 GHCR、Docker Hub 或其他镜像仓库。
- 不生成或上传加密服务器发布包。
- 不向 GitHub Secrets 存储 GPG 私钥、生产数据库密码或 MinIO 凭据。
- 不自动部署到 `ssh gg` 或其他服务器。
- 不在 CI 中使用本机、测试服务器或生产服务器的持久卷。

## 工作流结构

### 1. 主门禁 `ci.yml`

触发条件：

- 所有 pull request，不限制目标分支、文件路径或草稿状态；
- 所有推送到 `main` 的提交；
- 手动触发。

同一 pull request 或同一分支只保留最新运行，旧运行自动取消。工作流默认权限仅为 `contents: read`，不得使用 `pull_request_target`，不得读取仓库 Secrets。

Job 划分：

1. **CI 契约与快速质量门禁**
   - Shell 语法；
   - Ruff；
   - 架构快照、模块目录和分区文档检查；
   - API/文档一致性；
   - CI 工作流自身的结构契约测试。
2. **完整后端回归**
   - 从 `backend/uv.lock` 冻结安装；
   - 运行完整后端 pytest；
   - 测试不得连接外部 MySQL/MinIO。
3. **Stage 回归矩阵**
   - 独立锁定工程分别运行自身测试：DWG→DXF、DXF→DWG、DXF→Excel、Steel DXF Classifier、Excel Final、余料读取器；
   - BH 左右进读取器和 Steel DXF Split 使用后端锁定环境运行其测试或集成契约，避免在 CI 中生成未提交的新锁文件；
   - 每个矩阵项独立显示结果，一个 Stage 失败不掩盖其他 Stage。
4. **前端生产构建与浏览器契约**
   - `npm ci`；
   - 架构检查、TypeScript 编译和 Vite 生产构建；
   - Playwright Chromium 回归；
   - 失败时上传 trace、截图和 HTML 报告，成功时不保留大体积产物。
5. **生产容器验收调用**
   - 在前四类门禁通过后调用仓库内可复用的 `container-ci.yml`；
   - 每一个 pull request 和每一次 push 到 `main` 都必须执行，不使用路径过滤跳过；
   - 容器验收失败会直接阻止主门禁通过。
6. **门禁汇总**
   - 依赖上述代码门禁和生产容器验收；
   - 任一必需 Job 失败或被取消时汇总 Job 失败；
   - 分支保护只需绑定这一稳定名称，内部矩阵可继续扩展。

所有 Job 固定运行时版本、使用锁文件缓存，设置明确超时，并在日志中输出工具版本。缓存键包含对应锁文件哈希，不能缓存 `.env`、测试数据库或业务文件。

### 2. 可复用生产容器验收 `container-ci.yml`

触发条件：

- 由 `ci.yml` 对每一个 pull request 和每一次 push 到 `main` 调用；
- 每日定时一次；
- 手动触发。

`workflow_call` 入口只在主门禁的代码、Stage 和前端检查通过后运行。定时和手动运行使用当时的 `main`。该工作流仍保持 `contents: read`，不发布任何镜像或成功产物。

容器验收顺序：

1. 校验 Compose 和 Dockerfile 契约；
2. 以 BuildKit 构建受保护后端和前端镜像，使用 GitHub Actions 构建缓存但不推送；
3. 检查后端镜像不包含业务 Python 源码、样本、测试或运行时密钥；
4. 在受保护镜像内执行模块导入、Alembic 单头、Stage CLI 和 ODA 双向转换检查；
5. 生成仅本次运行使用的 `.env.docker`，所有测试凭据随机生成且不输出；
6. 使用独立 Compose 项目名和独立命名卷启动完整栈；
7. 等待 MySQL、MinIO、API、dispatcher、Nginx 和全部 worker 健康；
8. 验证公开健康接口、生产功能矩阵、MySQL↔MinIO 文件事务和余料真实链路；
9. 无论成功或失败都执行 `down --volumes --remove-orphans`，删除本次 CI 容器、网络、卷和临时环境文件。

CI 必须通过专用 Compose override 重命名 `app_var`、`mysql_data` 和 `minio_data`，名称包含 GitHub run ID 与 attempt。即使未来改用 self-hosted runner，也不能连接或覆盖 `dwg-agent_*` 生产卷。运行时端口仅绑定回环地址，避免暴露到 runner 网络。

## 失败处理与可观测性

- 每个 Job 有独立、稳定的中文或英文名称，日志开头打印 Python、uv、Node、npm、Docker 和 Compose 版本。
- pytest 和 Playwright 使用 GitHub 原生日志分组；不隐藏失败返回码。
- Playwright 失败产物保留 7 天。
- 容器验收失败时，在清理前输出 `docker compose ps`、最近容器日志、磁盘空间和卷引用；日志必须经过密钥屏蔽。
- 清理步骤使用 `if: always()`，但清理失败不能覆盖原始测试失败原因。
- 定时任务失败与 main 提交失败均在 Actions 页面形成明确红色结论；本设计不自行发送外部通知。

## 安全与供应链约束

- GitHub Actions 使用完整提交 SHA 固定第三方 Action，注释标明对应版本。
- pull request 只执行仓库内验证命令和临时容器启动，不执行发布、远程连接或部署脚本，不授予 `packages: write`、`id-token: write` 或部署环境权限。
- 不把 `.env.docker`、测试凭据、镜像 tar、GPG 文件、Excel/DXF 样本输出上传为 Artifact。
- 依赖安装只使用已提交的 uv/npm 锁文件；CI 发现锁文件漂移应失败，不能自动改写锁文件。
- 生产镜像验证复用仓库现有发布检查逻辑，避免 CI 与正式发布形成两套不一致规则。

## CI 自身的回归契约

新增基础设施测试解析工作流和 CI Compose override，至少锁定：

- 触发分支、最小权限、并发取消和超时；
- `pull_request` 没有 branches/paths 过滤，`push` 只允许 `main`；
- 后端、Stage、前端、容器四类门禁均存在；
- 完整后端测试与前端 Playwright 没有被 `continue-on-error` 弱化；
- 容器工作流没有 push、发布、生产 Secrets 或 `pull_request_target`；
- CI 三个持久卷名称与 `dwg-agent_*` 正式卷完全不同；
- 清理步骤始终执行；
- 容器验证调用与正式发布相同的运行时能力检查。

## 验收标准

1. 工作流 YAML 通过语法与结构检查。
2. 新增 CI 契约测试先失败、实现后通过。
3. 本机能分别执行 CI 中的后端、Stage、前端和容器命令。
4. 完整仓库验证通过，现有测试不因 CI 改动退化。
5. 临时容器验收前后，正式 `dwg-agent_*` 卷引用和业务数据不变。
6. Git 提交不包含 `.env.docker`、测试产物、发布包或样本数据。
7. 推送 `main` 后，GitHub Actions 能识别两个工作流并开始运行；若远程运行受 GitHub 配额或平台状态阻塞，必须如实记录，不能声称通过。
