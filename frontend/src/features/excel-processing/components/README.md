# Excel 处理组件

## 现有实现

`ExcelFinalOverview.tsx` 汇总状态和关键计数；`ExcelFinalBatchDrawer.tsx` 展示 batch、part、component 明细及重试/下载；`ExcelFinalTools.tsx` 提供重量查询和辅助工具；`ExcelFinalPage.css` 只服务这些界面块。

## 输入、输出与边界

输入是页面层已查询的批次/关系数据、加载/权限状态与显式回调，输出是状态、详情、恢复和下载交互。组件不自行决定 URL、项目访问、Job attempt 或 API 幂等键；这些仍由页面/API 层负责。
