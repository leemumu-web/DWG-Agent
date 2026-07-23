# Remnant inventory frontend

本 feature 是网页端余料库唯一 owner。`RemnantInventoryPage.tsx` 组合页面和 URL 查询状态；`RemnantSearchPanel.tsx` 管理材质、厚度、系列与状态条件；`RemnantGlobalPanel.tsx` 无需前置条件分页查看全库、按材质/厚度/状态/项目/零件筛选并触发后端全量 Excel 导出；`RemnantDetailDrawer.tsx` 展示占用信息和库存动作；`RemnantEditModal.tsx` 供管理员修订正式库存；`RemnantMaterialCatalog.tsx` 管理标准材质、系列、后缀和别名；`api.ts`、`types.ts` 定义后端契约；`styles.css` 保持现有管理端视觉边界；`index.ts` 是跨 feature 唯一入口。

检索通过 React Query 缓存，预占冲突会失效库存查询；在线图形复用 files feature 公开的 `DxfPreviewModal`。原图下载标签按后端返回的 `.dwg`/`.dxf` 展示，只有预占人和管理员能触发。

`RemnantImportPanel.tsx` 负责多文件登记，`RemnantBatchProgress.tsx` 展示逐图状态与重试，`useRemnantBatch.ts` 负责 URL 批次恢复和轮询终止，`RemnantConfirmationPanel.tsx` 负责批量厚度、候选校正、证据/警告和选择性确认。零件候选是默认全选的标签集合，允许工人删除误识别项或补充新编号；项目候选保持单值并允许编辑。所有组件仍复用同一个文件、Job 和预览状态，不建立第二套存储事实。

后端接口中，`GET /api/v1/remnants` 保持“材质 + 厚度”精确检索契约；`GET /api/v1/remnants/all` 提供全局可选筛选和服务端分页；`GET /api/v1/remnants/export.xlsx` 始终导出全部已确认余料，不受页面筛选和分页影响。三者都沿用余料工人权限。
