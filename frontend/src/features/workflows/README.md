# 生产工作流

## 现有实现

`WorkflowsPage.tsx` 只负责创建和查询批次；`WorkflowDetailPage.tsx` 在独立 URL 展示九阶段轨道、当前阶段工作台、产物和错误；`ProductionInputPanel.tsx` 完成多个 DWG + 一个 Excel 的上传、补交、配对和冻结；`DxfClassificationPanel.tsx` 展示 Classifier 1.1.0 Job、类型汇总、逐图结果和下载；API/DTO 分别在 `workflows.api.ts`、`workflow-inputs.api.ts`、`workflow*.ts`，展示规则在 `model/`。

`workflow.ts` 定义 run/stage/artifact/template、阶段执行请求与分类 run/item；`workflow-input.ts` 定义输入批次、计数、问题、item 和转换反馈。`styles.css` 拥有新建批次、工业化阶段轨道、当前工作区和窄屏布局；`index.ts` 统一重导出页面、API 与合同，其他 feature 不深层导入。

## 业务流

新建批次后进入 `/workflows/{id}` 上传，files 登记源文件，服务器创建 DWG→DXF Job；全部配对无冲突后冻结输入并进入 DXF 分类。Excel 第一阶段从冻结清单解析唯一源 Excel，浏览器只发送 execution kind。输出是 workflow/stage 状态、drawing unit、分流文件和阶段 Excel 产物。

## 边界

浏览器拒绝人工 DXF；后续自动拆板、Windows/CAM、最终屏障等未交付阶段只显示真实占位状态。
