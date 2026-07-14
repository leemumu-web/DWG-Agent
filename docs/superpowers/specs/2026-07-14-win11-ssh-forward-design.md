# Win11 SSH 反向端口转发脚本设计

## 目标

将 `scripts/forward-to-win11.sh` 纳入主线，提供安全、可重复、可观察且可测试的 SSH remote forwarding 生命周期管理。默认行为仍把 Win11 上的 `127.0.0.1:8080` 转发到本机的 `127.0.0.1:8080`，同时允许通过命令行或环境变量覆盖主机、地址和端口。

## 命令合同

脚本支持 `start`、`stop`、`restart`、`status` 和 `help`；无子命令等同于 `start`，兼容原有直接执行方式，原有 `--stop` 作为兼容别名保留。配置优先级为“命令行参数 > 环境变量 > 默认值”。参数包括远端 SSH host、远端 bind address/port、本地 address/port，以及可选 control socket 目录。

默认远端 bind address 为 `127.0.0.1`，避免把 Web 界面意外暴露到 Win11 的所有网卡。脚本输出实际转发关系和明确的访问 URL，不输出凭据或 SSH 配置内容。

## 生命周期与并发安全

脚本使用 SSH ControlMaster socket 标识自己创建的连接，不再通过 `pgrep -f` 猜测 SSH PID：

- `start` 先在该转发配置的锁文件上取得 `flock`，再用 `ssh -S <socket> -O check` 判断连接是否存在。
- 已运行时 `start` 幂等返回，不主动重启；需要重建时显式使用 `restart`。
- 新连接使用 `ControlMaster=yes`、`ControlPersist=yes`、`ExitOnForwardFailure=yes`、keepalive 和 `-fNT -R`。
- `status` 以 control socket 查询为权威；残留但失效的 socket 会被清除并报告 stopped。
- `stop` 使用 `ssh -S <socket> -O exit` 只终止该脚本管理的 master，不会误杀其他 SSH 会话。

socket 和锁的文件名由有效配置的稳定摘要生成，因此不同主机或端口的隧道可以并存。运行目录默认位于 `${XDG_RUNTIME_DIR:-/tmp}` 下的当前用户专属目录，并以 `0700` 权限创建。

## 前置检查与错误处理

启动前检查 `ssh`、`flock` 和用于检测本地监听的 `ss`。主机、地址和端口在执行任何 SSH 命令前完成校验。若本地目标端口未监听，脚本直接失败并给出本项目的启动提示；SSH host 配置或网络错误则保留 SSH 的诊断并返回非零。

只有确认 master 启动成功后才报告 active。`restart` 在 stop 成功后重新执行 start；任何一步失败均保持非零退出码。脚本不修改 SSH config、防火墙、Win11 网络配置或本项目服务。

## 测试与验收

在 `backend/tests/test_forward_to_win11_script.py` 中使用隔离临时目录和假的 `ssh`、`ss` 可执行文件，不建立真实网络连接。测试覆盖：

- 参数帮助、默认值、命令行覆盖和非法端口；
- 本地端口未监听时拒绝启动；
- 首次启动、重复 start 幂等、status、stop 和 restart；
- control socket 失效后的残留清理；
- SSH 转发失败向调用者传播。

最终验收运行该测试文件、`bash -n`、ShellCheck（若仓库环境可用）、后端相关回归、文档检查和 `git diff --check`。真实 Win11 连通性只有在现有 `win11` SSH host 可用时做非破坏性探测；缺少目标主机不影响对脚本状态机的确定性验收。

## 发布

只暂存本设计、实施计划、脚本及其测试；提交到当前 `main`，确认本地与 `origin/main` 的先后关系后，以普通 fast-forward push 发布，不使用 force push，也不创建额外 PR。
