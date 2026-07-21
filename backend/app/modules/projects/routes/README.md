# Projects HTTP 路由

## 现有实现

`projects.py` 暴露项目列表/创建/更新/删除和成员管理；`drawings.py` 暴露图纸与版本查询/登记；`router.py` 组合原有路径和 operationId。

## 输入、输出与边界

输入是认证、项目成员命令和 drawing/file 元数据，输出是生产资料目录 API。上传字节和 CAD 转换必须调用 files/jobs 公共边界，route 不直接访问存储。
静态成员/版本路径由 `router.py` 保持注册优先级；所有列表必须沿用 service 的项目可见范围、稳定分页和资源不存在/无权访问的一致错误语义。
