# Shell 公共库

## 现有实现

`common.sh` 提供路径、日志、env、端口/进程归属；`local_stack.sh` 管 FastAPI/Vite/Nginx 与构建新旧；`database.sh` 管 MySQL/migration/seed/backup；`compose.sh` 管 Compose/备份恢复；`cad_worker.sh` 管 8 个队列 worker、PID/入口归属和 Xvfb。

## 输入、输出与边界

输入是稳定 facade 参数和明确环境变量，输出是有归属检查的生命周期/诊断动作。库不执行业务算法；不得把只存在 PID 的旧入口进程误报为当前 Worker。
