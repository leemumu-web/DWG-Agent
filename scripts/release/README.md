# 服务器离线发布工具

## 现有实现

`render_server_compose.py` 从仓库 Compose 生成只引用固定发布镜像的服务器配置；`verify_image_archive.py` 逐层检查导出的后端镜像，拒绝业务 Python 源码、测试和样本混入；`verify_live_remnant.py` 在服务健康后用受保护运行时验证余料 DXF 的 MySQL 登记、MinIO 写入与回读、解析、确认、库存查询和预览权限，并自动清理本次唯一标记的测试记录与对象；`server-deploy.sh` 是发布包外部与包内共用的部署器，负责校验、解密、加载镜像、创建受限环境文件，并按 MySQL/MinIO、API、其余服务三层启动和核验。

首次部署或发布更新后，以 root 身份运行：

```bash
/opt/dwg-agent/server/scripts/server-deploy.sh enable-service /opt/dwg-agent/server
```

该命令安装并启用 `dwg-agent.service`。服务器冷启动时，systemd 会在 Docker 和网络之后执行同一套分层恢复；启动失败每 15 秒重试。正常停机通过 Compose 有序关闭，数据卷不删除。单个容器的主进程异常退出仍由 Compose 的 `unless-stopped` 策略恢复，API 和 worker 在自身启动入口再次等待 MySQL，避免只依赖首次 Compose 顺序。

## 输入、输出与安全边界

本目录由根入口 `scripts/release.sh bundle` 调用。输入是已验证镜像、GPG 接收者和无秘密的配置模板，输出是加密 `tar.gz.gpg`、独立部署器及 SHA-256 清单。运行时 `.env.docker`、数据库内容、对象数据和解密明文不进入发布包；部署完成后镜像中的 Python 字节码仍可被拥有服务器 root 权限的人逆向，因此这里提供的是传输/静态加密与降低直接阅读源码的门槛，不宣称防御服务器最高权限人员。

`server-deploy.sh` 只操作明确的发布目录和当前 Compose 项目，不自动备份或删除业务卷。任何清库、卷替换或生产密钥写入都必须由单独受控流程执行。
