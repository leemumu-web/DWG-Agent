# 工作流 E2E

## 现有场景

`production-dashboard.spec.ts` 证明工作台只读取生产流程/模板，展示真实项目并能打开新建生产项目表单；`workflow-input.spec.ts` 覆盖创建并启动后在详情页上传、多个 DWG + 单 Excel 校验、补交/冲突诊断、禁止人工 DXF、服务器派生 DXF、冻结及分类入口；`workflow-detail.spec.ts` 锁定十阶段工作台、冻结 Excel 第一阶段不再二次选择文件、Excel 第二阶段与 CAM 等待上线、拆板无模拟指标以及产物按类型汇总合同；`workflow-retention.spec.ts` 覆盖终态入口、页面刷新后恢复服务器备份、原生 ZIP 下载、精确确认词和异步清理完成状态。

## 输入与证据边界

输入是 workflows/files/jobs API 或严格 fixture，输出是生产输入闭环和防误操作证据；后续拆板、Windows/CAM 与最终汇总仍应显示占位，不能由此 spec 判为实现。
场景还核对上传/登记失败后的恢复提示和独立详情页连续操作，确保新建批次后直接进入唯一生产工作区。
