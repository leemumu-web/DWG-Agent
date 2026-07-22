# Remnant inventory

本目录是全厂共享余料、材质目录和余料导入账本的唯一业务 owner。`models.py` 定义六张表；`materials.py` 管理材质；`imports.py` 登记、校正和确认批次；`inventory.py` 实现检索、预占及正式余料生命周期；`execution.py` 执行 attempt-fenced 转换解析；`stage_adapter.py` 调用独立解析 Stage；`tasks.py` 提供两个专用 Celery 入口；`schemas.py` 定义契约；`access.py` 定义角色判定；`interface.py` 是其他领域唯一可导入的余料边界；`__init__.py` 不承担隐式装配。

正式余料生命周期为 `available → reserved → used`，预占可以取消回到 `available`，未使用记录可以归档。厚度始终由工人填写，系统解析的材质、项目编号和多个零件编号候选在确认前只保存在导入项中。

边界要求：本模块不得保存 DWG/DXF 字节、复制 Job 状态或绕过统一审计。后续服务只能通过 `files.interface`、`jobs.interface`、`cad_processing.interface`、`identity.interface` 和审计公共接口协作；未来其他业务域读取或消耗余料时只能导入本模块的 `interface.py`。
