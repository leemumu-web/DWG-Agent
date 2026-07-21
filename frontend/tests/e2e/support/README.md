# E2E 测试支持

## 现有实现

`test-env.ts` 读取 Playwright base URL、API URL、管理员/操作员凭据和可选 Excel 样本路径，统一 URL 拼接与环境缺失处理。

## 边界

输出只供各 spec 构造一致执行上下文；这里不保存业务断言、不生成伪业务数据，也不把未提供的真实样本静默替换成“成功”。
环境缺少真实样本/凭据时由调用 spec 显式 skip 并说明原因；base URL 与 API URL 必须指向本轮目标部署，防止浏览器和 API 证据来自不同版本。
