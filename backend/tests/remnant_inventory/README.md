# Remnant inventory tests

本目录验证余料领域的业务和集成契约。`test_models.py` 检查六张业务表的 owner、固定精度厚度、默认状态、attempt、乐观版本、查询索引和命名唯一约束，并确认模型通过 bootstrap registry 进入统一 SQLAlchemy metadata。

后续测试文件按实施阶段加入，覆盖材质目录与别名、混合 DWG/DXF 导入、异步 attempt fencing、人工校正与幂等确认、库存检索、并发预占、原图下载权限和 HTTP envelope。

测试边界：这里不得复制生产服务算法，也不能把 SQLite 单元测试当作 MySQL 并发或真实 ODA 验收。数据库迁移、Compose 和架构一致性分别由 `tests/infrastructure`、`tests/architecture` 以及最终真实样本门禁补充验证。
