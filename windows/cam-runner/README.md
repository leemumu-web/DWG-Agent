# CAM Runner

Status: external target contract; core CAD automation is blank.

目标 Runner 是本机隔离进程，接收已校验工作包，在明确超时和取消边界内驱动 CAD/CAM 应用，并输出结构化 manifest。它不得直接写 Linux MySQL、伪造 Job 成功或绕过 Node Agent 的 lease/fencing。

尚未实现：进程协议、Named Pipe/本地 IPC、CAD 会话所有权、崩溃回收、超时、截图/日志脱敏、结果摘要和真实 Windows 测试。
