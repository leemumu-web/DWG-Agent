# 文件登记与对象流转

## 职责

本模块负责文件元数据登记、批次查询、项目范围访问、上传/下载流水、MySQL 与 Local/MinIO 的补偿以及预览/导出入口。它不实现 CAD 预览算法，不执行运维一致性处置，不将对象字节保存在 MySQL。

## 公共边界

其他业务模块只导入 `app.modules.files.interface`。`platform/storage` 提供 Local/MinIO 字节适配器与工厂；本模块在其上建立 `StoredFile` 登记和 `FileTransfer` 补偿账本。HTTP 装配仅在 `routes/router.py`，不经由 `interface.py` 加载。

## 数据归属

- `files`：文件业务元数据、SHA-256、对象位置和软删除状态。
- `file_transfers`：入库、出库、复用、软删除与永久清理的 saga 流水。
- `storage_scan_runs` / `storage_scan_findings`：一致性扫描事实；扫描和处置用例属于 operations。

## 数据流与失败模式

写入先建立 transfer 意图，再写对象，同一业务事务登记文件并结算流水。数据库回滚时删除新对象；删除失败记为 `compensation_required`。下载按实际消费字节结算。软删除不立即删除对象，冻结生产输入不允许删除。

## 依赖、测试与差距

身份、项目、Job、CAD 与 workflow 能力均通过各自 `interface.py` 使用。冻结输入删除保护只请求 workflow 的不可变引用投影，不直接查询其模型；为避免公开接口循环，该查询在删除命令内延迟解析。边界测试位于 `tests/architecture/test_files_boundaries.py` 和 `test_workflow_boundaries.py`，回归覆盖文件权限、传输、存储一致性、上传、批次、DXF 预览和冻结清单保护。生产 MinIO 与运维处置仍需按部署环境实测。
