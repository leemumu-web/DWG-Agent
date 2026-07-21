# Excel 处理

## 现有实现

`ExcelFinalPage.tsx` 组合批次列表、上传、处理工具和详情；`ExcelPreview.tsx` 提供后端快速预览与 LuckyExcel 增强预览；`api.ts` 覆盖 14 个 Excel Final operation，`types.ts` 定义批次、part、component、weight 和 preview 合同；复杂组件与展示模型分别进入 `components/`、`model/`。

## 业务流

Excel 先经 files 登记，再创建 Excel Final Job；完成后页面查询关系化批次、零件、构件和重量，支持结果下载与失败重试。输入是有效工作簿和权限，输出是可核对的处理结果界面。

## 未完成边界

当前只接通既有单文件 Excel Final 切片；全图纸屏障、左右进合并和最终自动汇总未实现。
