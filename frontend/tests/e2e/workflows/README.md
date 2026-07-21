# 工作流 E2E

## 现有场景

`workflow-input.spec.ts` 覆盖创建并启动后原抽屉上传、多个 DWG + 单 Excel 校验、补交/冲突诊断、禁止人工 DXF、服务器派生 DXF、冻结及分类入口。

## 输入与证据边界

输入是 workflows/files/jobs API 或严格 fixture，输出是生产输入闭环和防误操作证据；后续拆板、Windows/CAM 与最终汇总仍应显示占位，不能由此 spec 判为实现。
场景还核对上传/登记失败后的恢复提示和同一抽屉连续操作，确保新建批次后无需离开上下文寻找提交入口。
