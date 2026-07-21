# DXF 分类测试

## 现有覆盖

`test_dxf_classification_pipeline.py` 验证 Classifier 1.1.0 CLI/schema/退出码、冻结输入来源、分类命名、run/item、AnalysisResult、Files/MinIO 双登记、重复调用、失败和 workflow 同步。

## 证据边界

输入是分类 Stage fixture 与隔离存储/数据库，输出是分流 DXF、JSON/CSV 报告和账本契约证据；真实企业图纸准确率仍需代表性样本验收。
