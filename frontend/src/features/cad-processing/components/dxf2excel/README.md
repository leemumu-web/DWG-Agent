# DXF→Excel 工作区

## 现有实现

`DxfUploadPanel.tsx` 校验/选择一个或多个 DXF、展示上传进度和格式反馈；`DxfBatchCard.tsx` 展示批次文件、Job 状态、成功/失败统计、重试、结果下载和清理动作。

## 输入、输出与边界

输入由 `Dxf2ExcelPage` 提供，包括批次查询、上传状态、权限和回调；输出是明确的上传、处理、恢复和下载意图。组件不自行创建 Job、不解析 DXF，也不把 Stage 失败隐藏为成功；错误必须保留 code/request ID。
