# DXF 拆板测试

本目录验证 Steel DXF Split 1.5.2 接入平台后的边界：

- 只有明确分类为 BH/BOX 的结果进入拆板，PX、其他类型和未分类条目只保留分类账本，不调用拆板器。
- 正常拆板与余量增长 DXF 成对持久化到现有对象存储桶。
- 业务性未通过不打断整批任务，Job 成功而工作流停在待复核。
- 人工复核 ZIP 只包含本 attempt 未通过的分类原始 DXF。
- 技术失败只保留一个不可变 attempt，不自动整批重算。
- 每 30 张图纸和最后一组余数分别做数量核验，报告与附件不计入图纸数。
- 拆板运行、逐图结果和 Excel 交接文件 ID 持久化到数据库。

`test_dxf_splitting_pipeline.py` 覆盖适配器命令、自动完成、混合人工处理、独立校验降级、
单次技术 attempt、30 张检查点、MinIO 键空间、MySQL 账本、Excel 交接和 HTTP 下载合同；
`test_classified_dispatch.py` 锁定冻结分类到 BH/BOX 业务的直接分发。测试不能调用真实
外部 MinIO、MySQL 或拆板子进程；这些依赖分别由 SQLite、本地对象存储和保真 CLI 假实现隔离。
