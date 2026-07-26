# CAD 处理

## 现有实现

`Dwg2DxfPage.tsx`、`Dxf2DwgPage.tsx` 复用 `ConversionPage.tsx`；`Dxf2ExcelPage.tsx` 管理材料表提取。`conversionState.ts` 统一判定活动、卡滞、结果已释放和可重新提交状态；`components/conversion` 拆分上传、文件夹、总览和列模型，`components/dxf2excel` 拆分批次上传/卡片，`hooks/useConversionEvents.ts` 合并 SSE 状态，`styles.css` 保存局部样式。

`index.ts` 是跨 feature 唯一入口，重导出三个页面、共享转换组件和必要类型；其他 feature 不得深层导入本目录内部文件。

## 业务流

选择文件/文件夹后先经 files 上传登记，再创建 jobs；页面展示 pending/running/succeeded/failed、可恢复动作、预览和结果下载。输入是允许格式及 API 权限，输出是服务器登记的 DXF、DWG 或 XLSX 结果。

转换批次总条按“已成功 + 已失败 + 已取消”的终态文件数除以范围内
源文件数计算，不平均运行中任务的内部里程碑。单图使用共享 Job 进度条
显示后端确认阶段；ODA 无逐文件进度时明确显示“转换中”，不使用前端计时估算。

## 运行边界

转换在 Celery/Stage 执行；ODA、Xvfb 和真实样本可用性不能由前端构建证明。
