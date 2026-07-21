# Jobs HTTP 路由

## 现有实现

`commands.py` 创建/批建/取消/重试；`queries.py` 列表/详情/步骤；`events.py` 提供当前快照 SSE；`results.py` 查询/下载 Result；`reviews.py` 管理人工结论；`router.py` 保持静态路径优先。

## 输入、输出与边界

输入是认证、project/file/task 参数和 attempt 命令，输出是 Job/Step/Result/Review/SSE 合同。任务实现归业务 task，broker transport 归 platform messaging；route 不直接改 Celery runtime 表。
