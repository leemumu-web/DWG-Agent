# CAD 处理测试

## 现有覆盖

`test_dxf_pipeline.py`、`test_dxf2dwg_pipeline.py`、`test_cad_batch_jobs.py` 验证单/批转换、版本选择、attempt 与登记；`test_dxf2excel_pipeline.py` 验证批次 staging/产物；`test_dxf_preview_{service,api}.py` 验证解析、SVG 缓存、权限和错误。

## 证据边界

输入是小型测试文件与可替换 converter/storage，输出是 Job 参数、状态机、MinIO/Files 登记和 API 合同证据；不证明部署 ODA AppImage 能打开全部生产图纸。
