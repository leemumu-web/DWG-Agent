# 生产工作流

## 现有实现

`WorkflowsPage.tsx` 创建/启动/查询批次；`ProductionInputPanel.tsx` 在同一抽屉完成多个 DWG + 一个 Excel 的上传、补交、配对和冻结；`DxfClassificationPanel.tsx` 展示 Classifier 1.1.0 Job、类型汇总、逐图结果和下载；API/DTO 分别在 `workflows.api.ts`、`workflow-inputs.api.ts`、`workflow*.ts`，展示规则在 `model/`。

`workflow.ts` 定义 run/stage/artifact/template、阶段执行请求与分类 run/item；`workflow-input.ts` 定义输入批次、计数、问题、item 和转换反馈。`styles.css` 拥有新建批次 hero/checklist、输入抽屉、阶段状态和窄屏布局；`index.ts` 统一重导出页面、API 与合同，其他 feature 不深层导入。

## 业务流

新建批次后立即进入上传，files 登记源文件，服务器创建 DWG→DXF Job；全部配对无冲突后冻结输入并进入 DXF 分类。输出是 workflow/stage 状态、drawing unit 和登记后的分流文件。

## 边界

浏览器拒绝人工 DXF；后续自动拆板、Windows/CAM、最终屏障等未交付阶段只显示真实占位状态。
