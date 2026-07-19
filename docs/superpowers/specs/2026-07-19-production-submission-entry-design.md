# 生产批次提交入口设计

## 问题

真实数据库没有 workflow 时，生产流程页只显示“新建流程”。DWG/Excel 上传入口必须先创建流程、打开详情、点击启动，随后才满足 `linux_production && status !== draft` 的渲染条件。业务主动作被隐藏在三级导航后，操作员会判断为“没有提交入口”。

## 决策

生产流程页的主动作统一为“提交生产批次”。表单只创建 `linux_production`，收集项目和批次名称；提交后按顺序调用现有 `POST /workflows` 与 `POST /workflows/{id}/start`。创建抽屉不关闭、不切换详情抽屉，而是在原位置扩展为第二步并立即显示现有 `ProductionInputPanel`，继续通过 input-batch API 上传多个 DWG 和一个 Excel。

不新增后端接口，不把文件字节塞进 workflow 创建请求。`/files`、input-batch、conversion-requests 和 freeze 的职责保持不变。

## 恢复路径

- 创建失败：表单保持打开并显示后端错误。
- 创建成功但启动失败：保留已创建的 draft，在同一提交抽屉显示“重试启动并进入上传”，避免抽屉跳转或孤立流程无法继续。
- 已有 draft：详情主体始终显示启动提示，不依赖抽屉右上角的小按钮。
- 已启动流程：直接显示 DWG/Excel 提交面板。

## 验收

空列表页面能直接看到“提交生产批次”；一次表单提交后同一抽屉原地进入上传，不经过详情页；draft 有明显恢复入口；既有转换、冻结和后续留白阶段不变。
