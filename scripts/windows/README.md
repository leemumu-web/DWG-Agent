# Windows 通信脚本

## 现有实现

`forward_to_win11.sh` 以 PID/命令归属管理 SSH remote-forward，把 Windows 侧请求转回 Linux Nginx `:8080`，支持 start/stop/status、重复启动保护和明确退出码。

## 输入、输出与未完成边界

输入是 Windows SSH host/user/key、远端/本地端口和已运行 SSH 服务，输出只是一条受控通信通道。它不是 Windows Node Agent、租约协议、CAM Runner 或 SinoCAM adapter；这些仍需外部实现。
