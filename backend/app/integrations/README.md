# 跨产品集成边界

## 现有内容

当前只有 `__init__.py` package 标识，没有可执行 adapter，也没有 `zwcad/`、MCP client 或网络调用。Agent、MCP、ZWCAD 与 Windows 的机器可读能力状态统一由 `app.modules.automation.contracts` 暴露，避免散落空文件让人误判为实现。

## 进入条件与输出

只有真正被多个领域共享、具有独立产品生命周期的外部 adapter 才进入本区；输入必须包含真实 SDK/协议、认证、超时重试、幂等恢复和验收样本，输出必须是有测试的 adapter/interface。

## 未完成边界

当前不提供任何执行能力；禁止通过配置项、目录或空 client 宣称 ZWCAD/Windows/CAM 已接通。
