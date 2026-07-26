# 文件与 CAD E2E

## 现有场景

`files-page-buttons.spec.ts` 覆盖文件/CAD 页的单文件与文件夹上传、暂停/补交、分页、批量删除/ZIP、重试、下载和错误反馈；`conversion-progress.spec.ts` 用隔离的权威 Job 状态验证批次终态完成度和 ODA 确认阶段；`dxf-preview.spec.ts` 覆盖 SVG 预览、缓存命中、失败及关闭恢复。

## 输入与证据边界

输入是 files/jobs 测试 API 或严格 route fixture，输出是共享转换交互与请求合同证据；fixture 生成的成功状态不代表 ODA 或 MinIO 已实际执行。
