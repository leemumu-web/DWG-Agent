# Remnant inventory frontend

本 feature 是网页端余料库唯一 owner。`RemnantInventoryPage.tsx` 组合页面和 URL 查询状态；`RemnantSearchPanel.tsx` 管理材质、厚度、系列与状态条件；`RemnantGlobalPanel.tsx` 无需前置条件分页查看全库、按材质/厚度/状态/项目/零件筛选、批量归档并触发后端全量 Excel 导出；`RemnantDetailDrawer.tsx` 展示占用信息和库存动作；`RemnantEditModal.tsx` 供有权限的用户修订正式库存；`RemnantMaterialCatalog.tsx` 管理标准材质、系列、后缀和别名；`errors.ts` 统一余料库中文错误与解析警告；`api.ts`、`types.ts` 定义后端契约；`styles.css` 保持现有管理端视觉边界；`index.ts` 是跨 feature 唯一入口。

检索通过 React Query 缓存，预占冲突会失效库存查询；在线图形复用 files feature 公开的 `DxfPreviewModal`。原图下载标签按后端返回的 `.dwg`/`.dxf` 展示，只有预占人和管理员能触发。

`RemnantImportPanel.tsx` 负责多文件登记，`RemnantBatchProgress.tsx` 展示逐图状态与重试，`useRemnantBatch.ts` 负责 URL 批次恢复和轮询终止，`RemnantConfirmationPanel.tsx` 负责批量厚度、候选校正、证据/警告和选择性确认。零件候选是默认全选的标签集合，允许工人删除误识别项或补充新编号；项目候选保持单值并允许编辑。即使解析结果没有材质候选，工人仍可填写完整牌号，通过正式材质解析/新建接口建档并自动选中；已停用的同名材质仍需管理员重新启用。确认表格固定右侧操作列并支持横向滚动，窄屏下“预览、编辑”不会被裁切。

全局余料默认只查询 `available,reserved`；“显示历史余料”开关默认关闭，打开后查询 `available,reserved,used,archived`，切换时回到第一页。批量归档只允许选择当前用户有权归档的可用余料；接口允许部分成功，成功项从选择中移除，失败项保持选中并逐条显示中文原因。工人只能归档自己导入的记录，管理员可归档任意工人的可用记录。

材质管理的启用状态是可交互开关，切换前使用中文确认，提交时仅显示当前行加载状态；操作失败不会预先改变显示值。余料库所有面向工人的校验、请求错误和解析警告均通过 `errors.ts` 转为中文，不直接显示 `REMNANT_*` 或解析器内部代码。

共享 DXF 在线预览使用浅色全宽画布，隐藏左下角状态小字和右侧统计栏，并按图纸真实宽高比适配；缩放后不限制拖动边界，仍保留缩放、适合窗口、重载和源文件下载。所有组件继续复用同一个文件、Job 和预览状态，不建立第二套存储事实。

后端接口中，`GET /api/v1/remnants` 保持“材质 + 厚度”精确检索契约；`GET /api/v1/remnants/all` 提供全局筛选和服务端分页；`POST /api/v1/remnants/bulk-archive` 接收 1–200 个编号并返回成功项与失败明细；`GET /api/v1/remnants/export.xlsx` 始终导出全部已确认余料和全部库存状态，不受历史开关、页面筛选或分页影响。以上功能均沿用余料工人权限。
