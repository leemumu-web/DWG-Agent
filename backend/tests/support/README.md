# 测试支持

## 现有实现

`paths.py` 提供仓库/后端/Stage 绝对路径；`database.py` 提供隔离 engine/session 和表创建；`project_fixtures.py` 提供 DB 级项目创建替代已移除的 /projects HTTP 端点；`workflow_api.py` 集中生产输入 HTTP 构造器；`sample_roots.py` 解析外部 DXF 样本根并在此类样本缺失时跳过图纸回归测试（环境变量可指向 Linux 本地样本路径）；`__init__.py` 只标识 package。全局认证/client fixture 仍在 `backend/tests/conftest.py`。

## 边界

输出是各领域一致的测试基础，不保存业务断言、不创建隐式生产数据；业务场景必须写在 owner 测试目录。
