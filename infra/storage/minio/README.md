# MinIO 存储边界

Status: implemented storage service in current Compose.

本目录不保存业务对象。Compose 的 `minio_data` 命名卷保存字节，MySQL `files` 与 `file_transfers` 保存登记、摘要和补偿事实。原始 DWG、服务器生成 DXF、分类分流 DXF、Excel、报告与归档按配置 bucket/key 存储。

本地开发可切换为 local storage，但不能在 MinIO 故障时自动降级到本地目录。备份与恢复必须把 MySQL 和全部 bucket 当作同一恢复集合。
