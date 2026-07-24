# 工作流 E2E

## 现有场景

`workflow-input.spec.ts` 覆盖创建并启动后在详情页上传、多个 DWG + 单 Excel 校验、补交/冲突诊断、禁止人工 DXF、服务器派生 DXF、冻结及分类入口；`workflow-detail.spec.ts` 锁定十阶段工作台、冻结 Excel 第一阶段不再二次选择文件、Excel 第二阶段与 CAM 等待上线、拆板无模拟指标以及产物按类型汇总合同。

## 输入与证据边界

输入是 workflows/files/jobs API 或严格 fixture，输出是生产输入闭环和防误操作证据；后续拆板、Windows/CAM 与最终汇总仍应显示占位，不能由此 spec 判为实现。
场景还核对上传/登记失败后的恢复提示和独立详情页连续操作，确保新建批次后直接进入唯一生产工作区。
