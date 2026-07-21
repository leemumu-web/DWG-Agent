# Excel 处理 E2E

## 现有场景

`excel-final-flow.spec.ts` 覆盖 Excel Final 上传入口、批次详情、失败重试、结果下载签名刷新、预览和 DXF→Excel 后续“生成零件清单”交互。

## 输入与证据边界

默认 route fixture 验证前端状态与 API 合同；只有配置 `PLAYWRIGHT_EXCEL_SAMPLE_PATH` 并连接真实服务的场景才能证明样本提交，仍需后端/Stage 记录证明真实处理完成。
