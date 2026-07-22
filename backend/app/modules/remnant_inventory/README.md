# Remnant inventory

本目录是全厂共享余料、材质目录和余料导入账本的唯一业务 owner。`models.py` 定义六张表；`materials.py` 负责牌号、别名和同系列解析；`imports.py` 登记混合 DWG/DXF 批次并执行 SHA-256 重复检查；`schemas.py` 定义输入输出契约；`access.py` 定义角色判定；`__init__.py` 只声明领域包，不承担隐式装配。

正式余料生命周期为 `available → reserved → used`，预占可以取消回到 `available`，未使用记录可以归档。厚度始终由工人填写，系统解析的材质、项目编号和多个零件编号候选在确认前只保存在导入项中。

边界要求：本模块不得保存 DWG/DXF 字节、复制 Job 状态或绕过统一审计。后续服务只能通过 `files.interface`、`jobs.interface`、`cad_processing.interface`、`identity.interface` 和审计公共接口协作；未来其他业务域读取或消耗余料时只能导入本模块的 `interface.py`。
