# Windows ZWCAD Worker 占位目录

本目录只有协议占位，没有 Windows service、可执行文件、注册/认证协议、产物传输、Celery CAD task、健康端点、安装器、Compose service 或端到端测试。

`CAD_WORKER_API_BASE`、`CAD_WORKER_API_KEY`、`CAD_WORKER_ENABLED`、队列 `cad` 和 `tasks_cad.py` 只是兼容符号，不是已交付 worker。Windows CAD Worker 与 CAD 构件提取/分类/拆板业务不属于当前项目交付目标，对应 flag 必须保持 false。详见[路线图](../docs/roadmap.md)。
