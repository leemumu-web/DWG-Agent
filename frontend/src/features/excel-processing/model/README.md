# Excel 展示模型

## 现有实现

`excelFinalUrlState.ts` 解析/生成 batch、tab 等 URL 查询状态；`excelPreviewModel.tsx` 把后端 preview 或 LuckyExcel cell 数据转换为列、行、合并单元格、宽度和格式化 React 内容。

## 输入、输出与边界

输入是不可信的 URLSearchParams 和两类预览 JSON，输出是经过边界检查、可供 Ant Design Table/预览页消费的纯展示模型。这里不访问网络、不写数据库、不决定处理状态；格式不支持时返回可解释的降级模型。
