# DXF 拆板测试

本目录验证 Steel DXF Split 1.5.2 接入平台后的边界：

- 只有明确分类为 BH/BOX 的结果进入拆板，PX、其他类型和未分类条目只保留分类账本，不调用拆板器。
- 正常拆板与余量增长 DXF 成对持久化到现有对象存储桶。
- 业务性未通过不打断整批任务；至少有一组正式配对结果时 Job/阶段成功并解锁 Excel，
  未形成结果的图纸保留明确原因且不进入正式交接。
- 正式结果下载只包含成对验收通过的两类 DXF；本批全部分类原图由独立入口完整保留。
- 技术失败只保留一个不可变 attempt，不自动整批重算。
- 每 30 张图纸和最后一组余数分别做数量核验，报告与附件不计入图纸数。
- 拆板运行、逐图结果和 Excel 交接文件 ID 持久化到数据库。

本目录测试文件清单：

- `test_dxf_splitting_pipeline.py` 覆盖适配器命令、自动完成、混合人工处理、独立校验降级、
  单次技术 attempt、30 张检查点、MinIO 键空间、MySQL 账本、Excel 交接和 HTTP 下载合同；
- `test_classified_dispatch.py` 锁定冻结分类到 BH/BOX 业务的直接分发；
- `test_bh_weld_allowance_terminal_chain.py` 锁定斜腹板、复合右端轮廓、歧义关闭与旧合同兼容；
- `test_box_regressions.py` 锁定拆板回归样本的行为契约；
- `test_box_release_attestation_runtime.py` 锁定内置 BOX 认证与当前受保护实现指纹一致；
- `test_box_equivalence_grouping.py` 锁定 BOX 板件等价分组的成对/校准/验收计数与行为契约；
- `test_box_role_hypotheses.py` 锁定 BOX 板件角色假设与证据不足转人工的边界；
- `test_box_review_materialization.py` 锁定 BOX 人工复核结果物化到制造输出的契约；
- `test_bh_annotation_physical_hole_capacity.py` 锁定 BH 标注物理孔容量校验；
- `test_bh_dimension_ordering_regression.py` 锁定 BH 尺寸排序回归；
- `test_bh_flange_projection_regressions.py` 锁定 BH 翼缘投影(嵌套投影、同长端步)回归；
- `test_bh_manufacturing_ir.py` 锁定 BH 制造 IR 的角色标签契约；
- `test_bh_nested_physical_flange_supervision.py` 锁定嵌套物理翼缘与手工标准几何比对；
- `test_bh_weld_allowance_feature_binding.py` 锁定焊接余量特征绑定与切面契约。

依赖外部 DXF 样本的图纸回归测试,样本缺失时自动跳过;样本根目录可用环境变量指向
Linux 本地路径(见 `backend/tests/support/sample_roots.py`)。测试不能调用真实
外部 MinIO、MySQL 或拆板子进程；这些依赖分别由 SQLite、本地对象存储和保真 CLI 假实现隔离。
