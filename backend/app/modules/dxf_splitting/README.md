# DXF 拆板模块

本模块把版本锁定的 `Steel DXF Split 1.5.2` 与独立 PL Stage 接入生产工作流。
父仓库中的 Stage 运行时来自约 4.3 万行的 `b8302c0` 集成提交。当前版本删除
Stage 内部的重复型材分流，改为消费上一步冻结分类，并用完整 20 对 BOX 样本
重新完成发布认证。

- 输入只来自最新 DXF 分类运行的已分类文件登记，不接受路径或临时上传。
- 后端把每个文件的冻结 `part_type` 写入严格清单；同一个隔离子进程按清单把
  BH、BOX 直接交给各自领域核心。
- 没有拆板核心的其他类型不会进入拆板子进程，继续保留其分类结果。
- 正常拆板、余量增长、报告、验证报告、批次清单和 `BH拆板信息表.xlsx`
  都通过文件登记写入现有 MinIO 桶。
- 面向排版员的正式结果 ZIP 只含已通过独立校验的普通版与余量版 DXF，
  并固定放入 `原长/`、`余量增长后短文件/` 两个中文一级目录；报告、台账和清单
  保留在内部审计存储，不混入交付下载。创建 ZIP 前必须证明两类文件数量都等于
  自动通过数；任一文件引用缺失或不可用时明确拒绝，不生成可能缺图的压缩包。
- 工作流阶段只绑定一个权威拆板 Job，整批只执行一次；单图缺失或无法证明时记录
  具体图名和原因，不自动重跑整批。
- 存在未形成正式结果的图纸时，run 保留 `completed_with_review` 审计状态；只要本批
  至少存在一组完整的普通版/余量版产物，第三阶段仍成功并把这些正式结果交给 Excel。
  未形成结果的图纸不进入正式 ZIP 或 Excel 交接。
- 每处理 30 张执行一次数量守恒校验，最后不足 30 张的尾批单独校验。
- 被取消或被新 attempt 取代的旧 run 会独立关闭，不会覆盖当前 Job。
- JobStep 固定先记录整批算法执行，再以第二步记录独立重开校验，最后登记正式产物。
- Excel 交接只传递稳定的运行 ID、文件 ID 和冻结清单摘要，不传本地路径或临时 URL。

文件职责：`adapter.py` 固定版本、CLI 和来源契约；`validation.py` 独立重开并核对成对产物；
`execution.py` 编排整批 attempt；`persistence.py` 负责数据库与对象存储登记；
`selective_exports.py` 按当前分类、拆板账本生成四类互斥原始 DXF 清单，并签发短期流式下载能力；
`pl_adapter.py` 只调用独立 PL Stage；`pl_execution.py` 编排 PL/XBOX 合并 attempt；
`pl_validation.py` 对保存后的 PL DXF 做第二套校验；`pl_selective_exports.py` 只导出 PL/XBOX 安全拒绝原图；
`xbox_adapter.py` 只调用独立 XBOX Stage（成对产物）；`xbox_validation.py` 对保存后的 XBOX 原长+余量成对 DXF 做第二套校验；
`review.py` 保留历史候选决定的后端审计兼容；
`models.py`、`schemas.py` 定义持久化和传输结构；`presentation.py` 生成公开读模型；
`tasks.py` 暴露 Celery 入口；`interface.py` 是其他业务模块唯一允许依赖的公开边界。
`__init__.py` 不导出内部实现。任何模块都不能绕过文件登记直接传递 MinIO 凭据、本地路径或临时 URL。
