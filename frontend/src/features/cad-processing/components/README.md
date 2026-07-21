# CAD 处理组件

## 现有实现

`conversion/` 服务 DWG→DXF 与 DXF→DWG 共用页面，`dxf2excel/` 服务批次材料表提取。两组组件只接收显式 props/callback，不访问其他 feature 私有文件。

## 输入、输出与边界

输入是页面层查询/mutation 状态、文件选择和允许动作，输出是上传、进度、结果、文件夹和批次交互。Job 创建、权限、错误 request ID 和轮询/SSE 恢复仍由页面/hook 编排；组件不宣称 Worker 可用。
