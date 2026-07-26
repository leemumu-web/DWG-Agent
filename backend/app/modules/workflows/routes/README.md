# Workflow HTTP 路由

## 现有实现

`templates.py` 查询模板；`commands.py` 创建/启动/确认/取消；`production_projects.py` 原子创建生产项目及其唯一完整工作流；`queries.py` 查询 run/stage；`artifacts.py` 管理内部产物绑定；`archive.py` 输出生产 ZIP，并为 Excel 第一阶段提供唯一 `.xlsx` 下载；`batch_exports.py` 提供四类流式 ZIP、下载状态与确认后物理释放；`retention.py` 提供完整生产关系预检、完整备份、最近状态恢复和管理员异步整批清理；`execution.py` 暴露阶段执行及 Excel 同规则预检；`intake.py` 处理 Excel 单文件、DWG 文件夹、转换与冻结；`classification.py` 提交/查询分流并输出本批全部原图 ZIP；`splitting.py` 查询当前拆板 attempt，并输出只含已通过普通版/余量版 DXF 的正式结果 ZIP；`router.py` 组合公开 operation。

## 输入、输出与边界

输入是认证项目用户、workflow/input/job/artifact 参数，输出是批次编排、冻结、分类、工作流压缩包与唯一 Excel 结果 API。人工输入只接受独立 `.xls`/`.xlsx` 单文件与 DWG 文件夹；除 Excel 第一阶段单 `.xlsx` 外，生产下载按完整 ZIP 交付。路由只能调用领域/公开接口，未交付阶段返回明确 capability/错误，不能伪排队。
