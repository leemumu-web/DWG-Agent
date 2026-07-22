# Remnant inventory frontend

本 feature 是网页端余料库唯一 owner。`RemnantInventoryPage.tsx` 组合页面和 URL 查询状态；`RemnantSearchPanel.tsx` 管理材质、厚度、系列与状态条件；`RemnantDetailDrawer.tsx` 展示占用信息和库存动作；`api.ts`、`types.ts` 定义后端契约；`styles.css` 保持现有管理端视觉边界；`index.ts` 是跨 feature 唯一入口。

检索通过 React Query 缓存，预占冲突会失效库存查询；在线图形复用 files feature 公开的 `DxfPreviewModal`。原图下载标签按后端返回的 `.dwg`/`.dxf` 展示，只有预占人和管理员能触发。

`RemnantImportPanel.tsx` 负责多文件登记，`RemnantBatchProgress.tsx` 展示逐图状态与重试，`useRemnantBatch.ts` 负责 URL 批次恢复和轮询终止，`RemnantConfirmationPanel.tsx` 负责批量厚度、候选校正、证据/警告和选择性确认。所有组件仍复用同一个文件、Job 和预览状态，不建立第二套存储事实。
