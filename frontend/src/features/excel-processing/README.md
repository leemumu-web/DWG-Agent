# Excel 处理

## 现有实现

`ExcelFinalPage.tsx` 提供处理、批次、零件、五金手册四个 URL 标签并只加载当前标签查询；`excelFinalUi.ts` 提供页面共享的状态常量与纯展示 helper（为控制单文件行数拆出）；`ExcelPreview.tsx` 通过后端 preview 合同提供表格预览、工作表切换、刷新和下载；`api.ts` 覆盖 Excel 第一阶段 operation，`types.ts` 定义批次、part、component、类别感知手册结果和 preview 合同；复杂组件与展示模型分别进入 `components/`、`model/`。

`index.ts` 只重导出页面、预览和跨 feature 必需的 API/类型，是本功能的稳定前端 facade；内部组件和展示模型不作为全局工具暴露。

## 业务流

独立入口把 Excel 经 files 登记后创建第一阶段 Job；生产 workflow 则自动解析冻结输入中的唯一 Excel。页面显示结构化表格错误、关系化批次/零件/构件和类别+规格+材质手册查询，支持结果下载与失败重试。

## 未完成边界

当前只接通既有单文件 Excel Final 切片；全图纸屏障、左右进合并和最终自动汇总未实现。
