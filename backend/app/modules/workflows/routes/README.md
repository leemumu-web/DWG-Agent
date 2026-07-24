# Workflow HTTP 路由

## 现有实现

`templates.py` 查询模板；`commands.py` 创建/启动/确认/取消；`production_projects.py` 原子创建生产项目及其唯一完整工作流；`queries.py` 查询 run/stage；`artifacts.py` 管理内部产物绑定；`archive.py` 复用 Files 能力输出完整生产 ZIP；`execution.py` 暴露阶段推进合同；`intake.py` 处理 Excel 单文件、DWG 文件夹、转换与冻结；`classification.py` 提交/查询分流；`router.py` 组合公开 operation。

## 输入、输出与边界

输入是认证项目用户、workflow/input/job/artifact 参数，输出是批次编排、冻结、分类和工作流压缩包 API。人工输入只接受独立 `.xls`/`.xlsx` 单文件与 DWG 文件夹，人工下载只返回完整 ZIP；路由只能调用领域/公开接口，未交付阶段返回明确 capability/错误，不能伪排队。
