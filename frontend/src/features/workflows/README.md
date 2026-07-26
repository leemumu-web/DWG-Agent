# 生产工作流

## 现有实现

`WorkflowsPage.tsx` 以 Project 为主对象查询完整生产流程；`ProductionProjectCreateDrawer.tsx` 一次提交项目资料并原子创建、启动其唯一工作流；`WorkflowDetailPage.tsx` 在独立 URL 展示十阶段工作台和错误，`WorkflowStageRail.tsx` 提供可点击的阶段导航，`WorkflowStageArchiveCard.tsx` 负责当前或历史阶段的批量 ZIP 下载和传输进度；`WorkflowArtifactSummary.tsx` 按类型精炼汇总生产产物并下载全量 ZIP；`WorkflowRetentionControl.tsx` 仅在终态批次提供“核对范围→下载完整备份→管理员确认清理”三步界面，并从服务器恢复后台状态；`FutureStageNotice.tsx` 统一呈现 Excel 第二阶段及 CAM/归档等待上线边界；`ProductionInputPanel.tsx` 完成 Excel 单文件、DWG 文件夹、忽略文件确认、配对和冻结；`DxfClassificationPanel.tsx` 展示 Classifier 1.2.0 的类型文件夹、预警、分页逐图详情以及分类/全量 DXF 压缩包下载；`DrawingProcessingPanel.tsx` 展示当前拆板 attempt、数量与生产结果，并分别下载正式拆板 DXF 和本批全部原图，不展示报告或逐图复核工作台；API/DTO 分别在 `workflows.api.ts`、`workflow-inputs.api.ts`、`workflow*.ts`，展示规则在 `model/`。

`workflow.ts` 定义 run/stage/artifact/template、阶段执行请求、分类 run/item 和导出合同；`workflow-input.ts` 定义输入批次、计数、问题、item 和转换反馈；`workflows.api.ts` 与 `workflow-inputs.api.ts` 分别拥有流程及输入 HTTP 调用。`DrawingProcessingExportActions.tsx` 在 Stage A3 “03 · 图纸拆板与独立校验”卡片标题栏组合两个独立入口：`DrawingSelectiveExportControl.tsx` 按未通过的 BH、未通过的 BOX、PL、其他多选流式下载且不删除，`WorkflowBatchExportControl.tsx` 提供四类生产文件下载、状态轮询和二次确认物理删除；两者都不属于下方 `WorkflowArtifactSummary.tsx`，禁用时必须说明原因。正式拆板结果的原长版和余量增长版复用同一后端导出账本，但只由正式结果下载入口成对选择，不进入四类清理弹窗。`styles.css` 拥有生产项目创建、工业化阶段轨道、当前工作区和窄屏布局；`index.ts` 统一重导出页面、API 与合同，其他 feature 不深层导入。

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
`current_stage`。阶段完成后服务端只解锁下一阶段，工作区继续显示刚完成阶段，直到操作员主动
点击阶段轨道；普通历史阶段可下载已有 ZIP，Excel 第一阶段只下载唯一 `.xlsx`；未来阶段只读
展示合同和锁定原因。
分类完成后展示目录型分类文件夹；点击文件夹后按页读取该类逐图详情。待确认和无法读取文件夹
显示预警，已登记目录类型和安全自动发现类型不制造预警。页面不显示文件 ID、JSON 报告或 CSV
清单；可下载任一分类的 DXF-only 压缩包，或下载本次全部分流 DXF 的压缩包。

## 边界

浏览器拒绝人工 DXF。第三步“图纸拆板与独立校验”通过
`POST /workflows/{id}/stages/drawing_processing/executions` 创建整批 Job，并通过
`GET /workflows/{id}/drawing-processing` 读取当前 attempt 的权威进度、正式配对数量、未形成
正式结果数量和逐图原因。页面分别提供只含 `原长/`、`余量增长后短文件/` 的正式拆板 ZIP，
以及本批全部分类原图 ZIP；不展示候选图、算法报告或逐图人工复核工作台。Excel 第一阶段执行
前调用同规则预检，成功后只下载唯一 `.xlsx`，不使用阶段 ZIP。Excel 第二阶段、
CAM 工作包、Windows CAM、结果接纳和交付归档统一弱化为“等待上线”，且不提供执行、
人工确认或阶段下载操作。

分批导出使用统一 Axios Blob 下载器接收字节进度，完成后再触发浏览器保存。选择弹窗显示
`原 DXF`、`正常拆板 DXF`、`原 Excel`、`产出 Excel` 四个 UI 标签；机器类型和数据库中的
原文件名保持不变。关闭弹窗、下载中断或不点击确认均保留服务器文件；只有状态变为
`downloaded` 后，用户点击“已保存，删除服务器文件”并通过第二次不可恢复确认，才调用
物理清理接口。

相邻的“导出”按钮同样复用带认证的 Blob 下载器和字节进度，但只处理当前拆板
run 中未自动接纳的分类 DXF。四个 UI 类别为 `未通过的 BH`、`未通过的 BOX`、`PL`、
`其他`；自动接纳 BH/BOX 被排除，叶子文件名保持数据库登记值。该入口没有删除动作。
