# Remnant inventory

本目录是全厂共享余料、材质目录和余料导入账本的唯一业务 owner。`models.py` 定义六张表；`materials.py` 管理材质；`imports.py` 登记、校正和确认批次；`inventory.py` 实现检索、预占、批量归档及正式余料生命周期；`execution.py` 执行 attempt-fenced 转换解析；`stage_adapter.py` 调用独立解析 Stage；`tasks.py` 提供两个专用 Celery 入口；`routes.py` 发布功能开关保护的 HTTP API；`schemas.py` 定义契约；`access.py` 定义角色判定；`interface.py` 是其他领域唯一可导入的余料边界；`__init__.py` 不承担隐式装配。

正式余料生命周期为 `available → reserved → used`，预占可以取消回到 `available`，未使用记录可以归档。厚度始终由工人填写，系统解析的材质、项目编号和多个零件编号候选在确认前只保存在导入项中。

## 批量归档合同

`POST /api/v1/remnants/bulk-archive` 接收 1–200 个余料编号；服务按首次出现顺序去重，并在同一外层事务中用逐条保存点隔离失败：

```json
{
  "remnant_ids": [101, 102, 101]
}
```

响应保持统一成功信封，`data` 同时返回成功编号和中文失败明细。单条失败不会回滚已经成功或阻止后续合法记录：

```json
{
  "data": {
    "archived": [101],
    "failed": [
      {
        "remnant_id": 102,
        "code": "REMNANT_ARCHIVE_FORBIDDEN",
        "message": "只能归档自己导入的余料。"
      }
    ]
  }
}
```

工人只能归档自己导入且状态为 `available` 的余料，管理员可以归档任意工人导入的 `available` 余料。每个成功项独立写入 `remnants.archive` 审计记录；重复编号只处理并审计一次。空数组或超过 200 项由请求校验拒绝。

余料库对工人可见的异常消息使用中文，`REMNANT_*` 代码仍作为日志、测试和程序判断的稳定合同。无材质候选时，确认流程复用 `POST /api/v1/remnant-materials/resolve-or-create` 建档并立即使用；同名停用材质不能由工人绕过管理状态重新启用。`GET /api/v1/remnants/export.xlsx` 始终导出所有已确认余料状态，不受网页历史显示开关、筛选或分页影响。

边界要求：本模块不得保存 DWG/DXF 字节、复制 Job 状态或绕过统一审计。后续服务只能通过 `files.interface`、`jobs.interface`、`cad_processing.interface`、`identity.interface` 和审计公共接口协作；未来其他业务域读取或消耗余料时只能导入本模块的 `interface.py`。
