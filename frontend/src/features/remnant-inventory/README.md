# Remnant inventory frontend

本 feature 是网页端余料库唯一 owner。`RemnantInventoryPage.tsx` 组合页面和 URL 查询状态；`RemnantSearchPanel.tsx` 管理材质、厚度、系列与状态条件；`RemnantDetailDrawer.tsx` 展示占用信息和库存动作；`api.ts`、`types.ts` 定义后端契约；`styles.css` 保持现有管理端视觉边界；`index.ts` 是跨 feature 唯一入口。

检索通过 React Query 缓存，预占冲突会失效库存查询；在线图形复用 files feature 公开的 `DxfPreviewModal`。原图下载标签按后端返回的 `.dwg`/`.dxf` 展示，只有预占人和管理员能触发。

当前第一阶段页面负责检索与详情；批量导入、进度恢复和人工确认组件在同一 feature 内继续追加，不建立第二套文件、Job 或预览状态。
