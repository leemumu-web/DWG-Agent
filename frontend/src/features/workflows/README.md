# 生产工作流

## 现有实现

`WorkflowsPage.tsx` 以 Project 为主对象查询完整生产流程；`ProductionProjectCreateDrawer.tsx` 一次提交项目资料并原子创建、启动其唯一工作流；`WorkflowDetailPage.tsx` 在独立 URL 展示九阶段轨道、当前阶段工作台、产物和错误；`ProductionInputPanel.tsx` 完成完整文件夹上传、配对和冻结；`DxfClassificationPanel.tsx` 展示 Classifier 1.1.0 Job、类型汇总和逐图结果；API/DTO 分别在 `workflows.api.ts`、`workflow-inputs.api.ts`、`workflow*.ts`，展示规则在 `model/`。

`workflow.ts` 定义 run/stage/artifact/template、阶段执行请求与分类 run/item；`workflow-input.ts` 定义输入批次、计数、问题、item 和转换反馈。`styles.css` 拥有生产项目创建、工业化阶段轨道、当前工作区和窄屏布局；`index.ts` 统一重导出页面、API 与合同，其他 feature 不深层导入。

## 业务流

生产项目列表直接消费服务端聚合的 Project 编号、名称和全局状态统计，不再用独立
Project 分页在浏览器中拼接。新建 Project 时原子创建并启动其唯一
`linux_production` Workflow，随后进入 `/workflows/{id}` 上传完整生产文件夹，files 登记全部源 DWG 和唯一 Excel，服务器创建
DWG→DXF Job；全部配对无冲突后冻结 `canonical_dxf` 并进入 DXF 分类。DWG 只在输入阶段
留档，后续图纸按 `classified_dxf → processed_dxf → cam_input_dxf → cam_output_dxf →
accepted_dxf → delivery_dxf` 流通。Excel 第一阶段从冻结清单解析唯一源 Excel，报告、清单
和 Excel 保持各自格式。页面直接展示服务端 `required_inputs`、`artifact_types` 和
`required_outputs`。

## 边界

浏览器拒绝人工 DXF；后续自动拆板、Windows/CAM、最终屏障等未交付阶段只显示真实占位状态。
