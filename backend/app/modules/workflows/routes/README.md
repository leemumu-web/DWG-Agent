# Workflow HTTP 路由

## 现有实现

`templates.py` 查询模板；`commands.py` 创建/启动/确认/取消；`queries.py` 查询 run/stage；`artifacts.py` 管理产物；`execution.py` 暴露阶段推进合同；`intake.py` 处理上传/转换/冻结；`classification.py` 提交/查询分流；`router.py` 组合 16 个 operation。

## 输入、输出与边界

输入是认证项目用户、workflow/input/job/artifact 参数，输出是批次编排、冻结和分类 API。路由只能调用领域/公开接口，未交付阶段返回明确 capability/错误，不能伪排队。
