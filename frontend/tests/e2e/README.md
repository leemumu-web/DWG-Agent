# 前端端到端测试分区

## 现有实现

9 个 spec 按 contracts、excel-processing、files、jobs、operations、workflows 六类业务场景归档；`support/` 只保存共享环境。Playwright 递归收集 98 个用例，根目录不允许堆放 spec。

## 证据范围

输入是浏览器、Nginx 测试入口和按场景选择的真实 API 或 route fixture，输出是按钮、错误反馈、请求方法/路径和状态流证据。mock 场景只证明 UI/合同，不得写成 MySQL、MinIO、Celery 或真实 Stage 验收。
