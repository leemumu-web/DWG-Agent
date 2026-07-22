# Remnant inventory tests

本目录验证余料领域的业务和集成契约。`test_models.py` 检查六张业务表的 owner、固定精度厚度、默认状态、attempt、乐观版本、查询索引和命名唯一约束，并确认模型通过 bootstrap registry 进入统一 SQLAlchemy metadata。`test_materials.py` 验证标准牌号、后缀、别名、同系列扩展、停用规则和 RBAC seed。

`test_import_batches.py` 覆盖 DXF 结构校验、混合 DWG/DXF 登记、配置上限、ZIP 拒绝和两级 SHA-256 重复检测。`test_execution.py` 覆盖单次目录 ODA 调用、解析 attempt fencing、批次计数以及专用 Celery 队列。`test_confirmation.py` 覆盖人工校正、批量厚度、重试、取消和幂等部分确认。`test_inventory.py` 覆盖精确及系列检索、生命周期、乐观并发预占、审计、预览和真实原图下载权限。`test_api.py` 覆盖功能开关、鉴权、multipart 导入、编辑确认、分页检索、生命周期及稳定 HTTP envelope。

`test_real_fixture_regressions.py` 锁定离线样本工具的显式路径、144 份 AC1032 枚举能力、仅元数据输出和不复制源图边界；真实 ODA 转换结果仍必须在安装了平台对应 ODA File Converter 的验收环境复核。

`test_inventory_mysql.py` 在设置 `MYSQL_INTEGRATION_DATABASE_URL` 时使用两个真实 MySQL session 同时预占同一余料，验证唯一胜者、占用人回报、预览/原图权限、释放、重新预占、使用和终态锁定；未设置外部数据库时明确跳过。

测试边界：这里不得复制生产服务算法，也不能把 SQLite 单元测试当作 MySQL 并发或真实 ODA 验收。数据库迁移、Compose 和架构一致性分别由 `tests/infrastructure`、`tests/architecture` 以及最终真实样本门禁补充验证。
