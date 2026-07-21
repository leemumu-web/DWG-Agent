# 存储基础设施

## 现有内容

`minio/` 保存 bucket、Compose 内网、凭据/volume、健康与备份说明；Compose `minio` 默认只接 internal 网络，不向宿主发布 9000/9001。FastAPI 经 platform storage adapter 写对象，files 表和 transfer 记录留在 MySQL。

## 输入、输出与边界

输入是 `.env.docker` 凭据、持久 volume 和内部 endpoint，输出是对象存储服务。文件权限、元数据、补偿和 consistency finding/处置分别由 files/operations 负责，不能在 MinIO console 绕过。
