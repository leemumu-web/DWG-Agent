# Windows Node Agent

Status: external target contract; no executable implementation.

目标职责是以 Windows Service 身份注册节点、发送 heartbeat、领取受租约保护的命令、下载工作包、调用本地 CAM Runner、上传结果并上报事件。当前只存在 `GET /api/v1/control-plane/contracts/windows-node-agent` draft；没有认证、注册、heartbeat、lease、fencing token、命令队列或安装器。

核心实现留白期间必须保留：节点/能力/版本 schema、命令与事件 envelope、幂等键、lease/fencing 语义、校验和、错误分类和安全日志要求。
