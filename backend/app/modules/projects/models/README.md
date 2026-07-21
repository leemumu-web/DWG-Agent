# Projects 持久化模型

## 现有实现

`project.py` 定义 Project 与 ProjectMember 的 owner/member/role 关系；`drawing.py` 定义 Drawing、DrawingVersion 及关联 file/version 元数据；`__init__.py` 聚合模型注册。

## 输入、输出与边界

输入是 identity user、files file ID 和项目目录数据，输出是 project/member/drawing/version 数据库事实。文件字节/object key 和转换 Job 分别归 files/jobs。
