# 文档工具脚本

## 现有实现

`generate_api.py` 从当前 `app.main:app` 确定性生成 `docs/reference/api.md`；`check.py` 检查权威文档集合、相对链接、Markdown hygiene、端口、设置默认值、36 表/17 revisions、生成 API 与能力声明；`__init__.py` 供测试导入。

## 输入、输出与边界

输入是可导入后端与维护文档，输出是同步文档或具体错误列表。生成器只覆盖 API reference，人工架构/运维结论仍必须核对当前实现。
