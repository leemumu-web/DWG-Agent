# CAD 工具脚本

## 现有实现

`benchmark_conversion.py` 对指定真实 DWG/DXF 样本和方向调用现有转换链，记录总耗时、成功/失败、吞吐与输出，不生成伪造业务 Job；`__init__.py` 只标识工具 package。

## 输入、输出与边界

输入是显式样本路径、Stage/ODA 环境和输出目录，输出是离线性能/失败数据。它不登记 MySQL/MinIO、不替代 FastAPI/Celery 生产入口，也不能用小样本宣称格式全覆盖。

`extract_box_yellow_reference_snapshots.ps1` 从只读合并图证据中提取 BOX 黄色正确答案快照，供外部生产标准验收使用。
