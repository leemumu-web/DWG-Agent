# 运维 E2E

## 现有场景

`daily-archive.spec.ts` 覆盖日期预检、创建、重复提示和下载；`data-console.spec.ts` 覆盖 MySQL 文件、对象、transfer、扫描/finding/空历史和处置确认；`infrastructure-console.spec.ts` 覆盖 worker、queue、message、task、broker 与未实现能力标签。

## 输入与证据边界

输入是 operations/control-plane 测试响应，输出是分页、安全确认和能力声明的浏览器证据；测试不得依赖残留扫描数据，也不得将 fixture 状态写成真实基础设施验收。
