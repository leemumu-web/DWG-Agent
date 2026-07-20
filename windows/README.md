# Windows 执行面边界

`windows/` 只描述 Linux 平台与 Windows 进程的目标边界。当前没有 Windows service、可执行文件、注册/认证实现、产物传输实现、Celery CAD task、健康端点、安装器、Compose service 或端到端测试。

`CAD_WORKER_API_BASE`、`CAD_WORKER_API_KEY`、`CAD_WORKER_ENABLED`、队列 `cad` 和 `tasks_cad.py` 只是兼容符号，不是已交付 worker。Windows Node Agent、CAM Runner、SinoCAM Adapter 与通信协议分别归档在子目录；对应 flag 必须保持 false。详见[实现状态](../docs/architecture/implementation-status.md)。
