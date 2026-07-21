# HTTP 基础

## 现有实现

`dependencies.py` 提供数据库 session、request ID 等跨域 FastAPI dependency；`exceptions.py` 定义带 code/details 的应用异常；`envelopes.py` 定义稳定成功/分页响应包装。

## 输入与输出

输入是 FastAPI 请求、验证错误和领域异常，输出是带明确状态码、错误码、字段详情与 request ID 的 HTTP 合同。

## 边界

本区不判断项目成员、角色或 Job 状态；领域授权和状态迁移留在 modules，bootstrap 负责注册全局 handler。
