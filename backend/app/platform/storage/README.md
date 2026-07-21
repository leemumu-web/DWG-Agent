# 对象存储基础

## 现有实现

`base.py` 定义 storage adapter 合同；`local.py`、`minio.py` 实现流式 put/get/delete/list/stat；`paths.py` 防目录穿越和字符串前缀绕过；`hashing.py` 计算 SHA-256；`factory.py` 按配置缓存选择 adapter 并暴露健康检查。

## 输入与输出

输入是 bucket/key、流、大小和配置，输出是对象元数据、字节流、列表或删除结果。生产 Compose 默认使用 MinIO，本地开发可使用 Local。

## 边界

本区不知道 file ID、项目权限或业务删除语义；MySQL 登记、transfer saga、补偿和一致性处置由 files/operations 拥有。
