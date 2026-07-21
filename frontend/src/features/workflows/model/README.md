# 工作流展示模型

## 现有实现

`workflowPresentation.tsx` 集中阶段顺序/标签、implemented/partial/placeholder/external 文案、状态颜色、时间/文件格式、错误原因和 Timeline/Table 列生成。

## 输入、输出与边界

输入是 workflow、input、drawing unit 和 classification API 数据，输出是 `WorkflowsPage`、`ProductionInputPanel`、`DxfClassificationPanel` 共用的纯展示模型。本区不发请求、不推进状态；能力标签必须忠实于后端状态，不能把占位阶段渲染成可运行。
