# 生产工作流

## 现有实现

`WorkflowsPage.tsx` 以 Project 为主对象查询完整生产流程；`ProductionProjectCreateDrawer.tsx` 一次提交项目资料并原子创建、启动其唯一工作流；`WorkflowDetailPage.tsx` 在独立 URL 展示十阶段工作台和错误，`WorkflowStageRail.tsx` 提供可点击的阶段导航；`WorkflowArtifactSummary.tsx` 按类型精炼汇总生产产物并下载全量 ZIP；`FutureStageNotice.tsx` 统一呈现 Excel 第二阶段及 CAM/归档等待上线边界；`ProductionInputPanel.tsx` 完成 Excel 单文件、DWG 文件夹、忽略文件确认、配对和冻结；`DxfClassificationPanel.tsx` 展示 Classifier 1.2.0 的类型文件夹、预警、分页逐图详情以及分类/全量 DXF 压缩包下载。

`workflow.ts` 定义 run/stage/artifact/template、阶段执行请求与分类 run/item；`workflow-input.ts` 定义输入批次、计数、问题、item 和转换反馈；`workflows.api.ts` 与 `workflow-inputs.api.ts` 分别拥有流程及输入 HTTP 调用。`styles.css` 拥有生产项目创建、工业化阶段轨道、当前工作区和窄屏布局；`index.ts` 统一重导出页面、API 与合同，其他 feature 不深层导入。

## 业务流

生产项目列表直接消费服务端聚合的 Project 编号、名称和全局状态统计，不再用独立
Project 分页在浏览器中拼接。新建 Project 时原子创建并启动其唯一
`linux_production` Workflow，随后进入 `/workflows/{id}` 分别上传一个 `.xls`/`.xlsx` 与一个 DWG 文件夹；混合文件夹确认后只发送 DWG。Files 登记全部源 DWG 和唯一 Excel，服务器创建
DWG→DXF Job；全部配对无冲突后冻结 `canonical_dxf` 并进入 DXF 分类。DWG 只在输入阶段
留档，后续图纸按 `classified_dxf → processed_dxf → cam_input_dxf → cam_output_dxf →
accepted_dxf → delivery_dxf` 流通。Excel 第一阶段从冻结清单解析唯一源 Excel，报告、清单
和 Excel 保持各自格式。Excel 第二阶段位于第一阶段和设计屏障之间，当前只展示
`stage1_excel + processed_dxf → stage2_excel` 合同及等待上线状态。页面直接展示服务端 `required_inputs`、`artifact_types` 和
`required_outputs`。

`PRODUCTION ROUTE` 按钮只切换所查看的阶段，实际上传、执行和确认始终绑定服务端
`current_stage`。历史阶段可查看和下载已有阶段 ZIP；未来阶段只读展示合同和锁定原因。
分类完成后展示目录型分类文件夹；点击文件夹后按页读取该类逐图详情。待确认和无法读取文件夹
显示预警，已登记目录类型和安全自动发现类型不制造预警。页面不显示文件 ID、JSON 报告或 CSV
清单；可下载任一分类的 DXF-only 压缩包，或下载本次全部分流 DXF 的压缩包。

## 边界

浏览器拒绝人工 DXF；自动拆板只显示能力与数据边界，不显示模拟进度。Excel 第二阶段、
CAM 工作包、Windows CAM、结果接纳和交付归档统一弱化为“等待上线”，且不提供执行、
人工确认或阶段下载操作。
