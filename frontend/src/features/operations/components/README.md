# 运维组件

## 现有实现

`DailyArchivePanel.tsx` 选择归档日期、预检文件范围、创建/查询归档并下载 manifest/ZIP；`RemediationDrawer.tsx` 对 missing、orphan、hash mismatch 等 finding 展示影响范围、确认词、签名和执行反馈；`data-console/` 保存六个查询面板。

## 业务流与边界

输入是运维 API、当前角色和用户显式确认，输出是可追溯 archive、scan、remediation 记录。所有破坏性操作必须显示数量、字节、对象样本和服务端 request ID；页面不得默认确认。
