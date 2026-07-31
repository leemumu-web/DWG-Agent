# 服务器离线发布工具

## 现有实现

`render_server_compose.py` 从仓库 Compose 生成只引用固定发布镜像的服务器配置；`verify_image_archive.py` 逐层检查导出的后端镜像，拒绝业务 Python 源码、测试和样本混入；`verify_runtime_features.py` 在容器内核对公开的生产功能矩阵和 Excel 第二阶段常开模板/队列，不读取或输出密钥；`verify_live_remnant.py` 在服务健康后用受保护运行时验证余料 DXF 的 MySQL 登记、MinIO 写入与回读、解析、确认、库存查询和预览权限，并自动清理本次唯一标记的测试记录与对象；`server-deploy.sh` 是发布包外部与包内共用的部署器，负责校验、解密、加载镜像、创建受限环境文件，并按 MySQL/MinIO、API、其余服务三层启动和核验；`server-timezone-migrate.sh` 提供北京时间切换专用的 `preflight`、`migrate`、`rollback`，在安装时一并进入服务器 `scripts/`。

首次部署或发布更新后，以 root 身份运行：

```bash
/opt/dwg-agent/server/scripts/server-deploy.sh enable-service /opt/dwg-agent/server
```

该命令安装并启用 `dwg-agent.service`。服务器冷启动时，systemd 会在 Docker 和网络之后执行同一套分层恢复；启动失败每 15 秒重试。正常停机通过 Compose 有序关闭，数据卷不删除。单个容器的主进程异常退出仍由 Compose 的 `unless-stopped` 策略恢复，API 和 worker 在自身启动入口再次等待 MySQL，避免只依赖首次 Compose 顺序。

## 输入、输出与安全边界

本目录由根入口 `scripts/release.sh bundle` 调用。输入是已验证镜像、具有本地私钥的专用 GPG 接收者和无秘密的配置模板，输出是加密 `tar.gz.gpg`、独立部署器及 SHA-256 清单。脚本写完密文后立即用对应私钥解密并遍历 tar，无法解密或归档损坏时删除本次不完整密文并拒绝发布。运行时 `.env.docker`、数据库内容、对象数据和解密明文不进入发布包；部署完成后镜像中的 Python 字节码仍可被拥有服务器 root 权限的人逆向，因此这里提供的是传输/静态加密与降低直接阅读源码的门槛，不宣称防御服务器最高权限人员。

`server-deploy.sh` 只操作明确的发布目录和当前 Compose 项目，不自动备份或删除业务卷。任何清库、卷替换或生产密钥写入都必须由单独受控流程执行。

## 北京时间短维护窗

活动上传、非终态文件传输、作业、dispatcher 投递或上传会话任一不为零时，禁止迁移。生产旧 schema 必须是 `d1e7f3a9c520`，并精确识别 126 个业务 `DATETIME` 与 3 个保持 UTC 的 Celery/Kombu 协议列。先只读运行：

```bash
/opt/dwg-agent/server/scripts/server-timezone-migrate.sh preflight /opt/dwg-agent/server
```

安装、恢复启动、启用系统服务、生产 smoke、停栈和时区维护共用同一个排他锁，任何容器或验证数据变更都不能与迁移/回滚交错；`status` 保持只读。安装器会在覆盖服务器 compose 前，原子保存旧 compose、RELEASE、镜像清单及原 `.env.docker` 到 mode `0700` 的 `.rollback-candidate/`；重复安装不会覆盖第一份旧版本候选。`migrate` 优雅停止 Nginx 并再次确认输入完整、写入静止、schema 未漂移，建立 mode `0700` 的 `backups/timezone-YYYYMMDD-HHMMSS/`，生成 MySQL 一致性压缩备份、SHA-256、核心表计数和 MinIO 对象数/字节清单。只有校验和、随机临时 schema 完整恢复、核心表计数、落盘同步、旧 compose 可解析且四个旧镜像 ID 全部匹配，才原子写入 `VERIFIED` 完成标记并切换容器。新 API 完成 Alembic、MySQL `+08:00`、核心数据及 MinIO 对账后，才允许启动 dispatcher 和 workers；后续任一门禁失败都会保持 Nginx 与写服务关闭：

```bash
/opt/dwg-agent/server/scripts/server-timezone-migrate.sh migrate /opt/dwg-agent/server
```

如需回滚，`BACKUP_DIR` 必须使用绝对路径、是上述 `backups/` 的直接子目录，并具有绑定当前目标、数据库、旧迁移版本、dump、数据清单和旧运行时文件哈希的有效 `VERIFIED` 标记。回滚同样取得排他锁，先停止入口并拒绝活动写入，然后恢复旧 compose、旧环境文件和数据库备份；旧 API 启动后必须再次证明 Alembic 回到 `d1e7f3a9c520`、MySQL 墙钟回到 UTC、核心表/文件字节与 MinIO 对象数/字节一致，才会启动 workers 和 Nginx。三个命名数据卷不会删除：

```bash
/opt/dwg-agent/server/scripts/server-timezone-migrate.sh rollback \
  /opt/dwg-agent/server \
  /opt/dwg-agent/server/backups/timezone-YYYYMMDD-HHMMSS
```
