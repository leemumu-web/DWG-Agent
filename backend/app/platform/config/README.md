# 配置基础

## 现有实现

`settings.py` 用 Pydantic Settings 解析 MySQL、Celery、Local/MinIO、JWT、CORS、Stage 开关与外部占位配置；`constants.py` 保存稳定任务/阶段/错误常量；`validators.py` 白名单校验排序字段，避免把用户值拼入 SQL。

## 输入与输出

输入是 `.env`、进程环境和安全默认值，输出是启动时已验证、类型化且可被 platform/modules 读取的配置。

## 边界

本区不保存运行状态，不决定项目权限或工作流转换；配置项存在不代表 RabbitMQ、Agent、Windows/CAM 已实现。
