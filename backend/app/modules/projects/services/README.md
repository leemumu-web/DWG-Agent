# Projects 应用服务

## 现有实现

`projects.py` 实现项目 CRUD、成员角色、可见性和所有者保护；`drawings.py` 实现图纸/版本登记、唯一性、file 关联和目录查询；`__init__.py` 只聚合服务入口。

## 输入、输出与边界

输入是数据库 session、actor、identity/files 公共接口和已验证 DTO，输出是经授权的 Project/Drawing 状态变化。不得直接操作其他领域私有模型或对象存储 adapter。
