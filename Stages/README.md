# 独立处理 Stage

## 当前产品

`dwg2dxf/` 与 `dxf2dwg/` 封装 ODA 双向格式转换；`dxf2excel/` 从 DXF 批次生成第一份材料工作簿；`steel_dxf_classifier_v1.1.0/` 负责预处理、分类分流和 JSON/CSV 报告；`excel_final/` 处理 Tekla/初始工作簿并输出关系化可导入的最终工作簿。每个 Stage 保持自己的版本、依赖、CLI、测试和 README。

## 平台调用链

FastAPI 不在请求线程导入/执行 Stage。业务 module 先用 MySQL 创建 Job 与 attempt，Worker 从 Local/MinIO staging 输入，在有界临时目录调用 Stage，再由领域 persistence 登记输出 File、AnalysisResult 和专有账本。Stage 输入是已冻结/校验文件，输出是确定性文件/报告，不拥有用户、项目、权限或 Workflow 状态。

## 验收与边界

单元测试只证明仓库内算法/适配合同；ODA 许可与主机环境、历史 corpus、企业 Excel schema、handbook 数据和代表性正反样本仍需各自验收。Stage 不得直接写平台 MySQL/MinIO、覆盖不可变源对象或把需要人工确认的结果升级为无条件成功。
