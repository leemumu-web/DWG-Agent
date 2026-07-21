# Files HTTP 路由

## 现有实现

`uploads.py` 负责 multipart/元数据入口；`catalog.py` 负责列表、详情、软删除；`batches.py` 负责批次/文件夹操作；`previews.py` 负责 DXF SVG；`downloads.py` 负责签名下载与 ZIP；`router.py` 确保静态路径先于 `/{file_id}`。

## 输入、输出与边界

输入是认证用户、project/batch/purpose、文件流和预检确认，输出是 MySQL file/transfer、Local/MinIO 对象、预览或流式下载。写对象后数据库失败必须走域内补偿，route 不直接操作 object key。
