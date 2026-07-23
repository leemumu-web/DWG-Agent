# Excel Processing HTTP 路由

## 现有实现

`processing.py` 处理上传、创建/重试和批次命令；`catalog.py` 查询批次、part、component 和质量摘要；`tools.py` 提供类别明确的五金手册查询；`health.py` 暴露 Stage 健康；`router.py` 按静态路径优先组合 operation。

## 输入、输出与边界

输入是认证、工作簿/file ID、分页/查询条件，输出是 Excel Final batch/Job/关系数据和工具响应。Stage 算法、幂等事务和对象存储 saga 留在域内 service/adapter，不复制进 route。

`GET /weights/lookup` 的 `category` 与 `spec` 必填；D 系列还要求 `material`，材质与 `round_bar/rebar` 冲突返回 `422 INVALID_HANDBOOK_LOOKUP`。基础设施不可用才返回 503。
