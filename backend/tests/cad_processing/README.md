# CAD 处理测试

## 现有覆盖

`test_dxf_pipeline.py`、`test_dxf2dwg_pipeline.py`、`test_cad_batch_jobs.py` 验证单/批转换、版本选择、attempt 与登记；`test_dxf2excel_pipeline.py` 验证批次 staging/产物；`test_dxf_preview_service.py` 验证安全 SVG、entity/output 上限、Local/MinIO cache、durable transfer 和缓存竞态，`test_dxf_preview_api.py` 验证鉴权、源对象 size/SHA、软删除与 preview content 关联。

## 证据边界

输入是小型测试文件与可替换 converter/storage，输出是 Job 参数、状态机、MinIO/Files 登记和 API 合同证据；不证明部署 ODA AppImage 能打开全部生产图纸。
