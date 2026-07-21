# Projects Schema

## 现有实现

`project.py` 定义项目/成员创建、更新、列表与响应 DTO；`drawing.py` 定义图纸/版本登记、查询和响应 DTO；`__init__.py` 聚合公共 schema。

## 输入、输出与边界

输入是不可信 JSON/查询参数，输出是经 Pydantic 验证的项目目录合同。持久化关系、权限与文件存在性校验由 service 完成。
Schema 只约束字段形状、长度、枚举和 nullable 语义；不能通过在 DTO 中接受任意 project/file ID 绕过 service 的成员关系与跨域引用校验。
