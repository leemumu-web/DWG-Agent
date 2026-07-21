# 文件管理

## 现有实现

`files.api.ts` 覆盖上传、列表、批次、签名下载、ZIP 和预览端点；`FileUpload.tsx` 处理校验/并发/进度；`FilesLayout.tsx` 管理筛选、分页和批量动作；`DxfPreviewModal.tsx` 展示 SVG；`ZipDownloadModal.tsx` 执行预检、确认和流式下载；`file.ts` 定义合同。

`index.ts` 是文件能力的公共重导出入口；`styles.css` 拥有上传区、pipeline tab、toast 与列表交互样式，`DxfPreviewModal.css` 单独约束大尺寸预览、工具栏和错误/加载状态，避免预览布局污染其他页面。

## 业务流

文件先上传并在 MySQL 登记，再由列表、预览或下载端点访问 MinIO/Local 对象。输入包括项目/批次/用途和授权，输出是 file ID、可追踪反馈及受控出库。

## 边界

浏览器不自行拼 object key；删除/ZIP 必须显示后端冲突、确认范围和 request ID。
