# 任务 E2E

## 现有场景

`jobs-page-buttons.spec.ts` 覆盖 Job 列表筛选/分页、冒烟创建、详情时间线、状态/进度、取消、重试、新 attempt 跟随和 Result 下载动作。

## 输入与证据边界

输入是 jobs/results API 或受控 fixture，输出是界面动作与端点对接证据；并发 worker fencing、broker 恢复和 MySQL 锁仍由后端测试负责。
场景同时检查按钮禁用、确认弹窗、错误码/request ID 与终态刷新，避免只有请求发出而缺少操作员反馈的假通过。
