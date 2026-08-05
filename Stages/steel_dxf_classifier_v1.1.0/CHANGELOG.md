# Changelog

## 1.3.0 — 2026-08-06

- 修复重复引用误判：同一规格文本出现在多个匿名块（如 `*A5`、`*A7`）时不再被当作 `TITLE_VALUE_CONFLICT`，标题值唯一性改按解析后的规格值判定，`BOX700*400*30*30` 等重复块标题可正确分类；
- 标题栏规格标签新增识别「截面规格」，与「截面 / 截面型材 / 规格」同等处理；
- 同步 Excel Final 输入合同与 dxf2excel 表头别名，支持「截面规格」作为规格列名；
- CLI 与分类报告 schema 保持不变（`STEEL-DXF-CLI-1.2`、`STEEL-DXF-CLASSIFICATION-1.2`）。

## 1.2.0 — 2026-07-25

- 扩充工程型材目录，明确加入 PX、RH、FL、BL、HSS、UPN、UPE、WT 等类型；
- 对满足严格标题栏证据和安全命名规则的新英文前缀执行自动发现，并保留独立分类目录；
- 逐图结果新增 `profile_raw`、`profile_normalized`、`type_source`、`group_key` 和 `next_stage_eligible`；
- 自动发现类型记录 `PROFILE_TYPE_AUTO_DISCOVERED`，待确认和无法读取结果保持不可接入下一阶段；
- CLI 与分类报告 schema 升级为 `STEEL-DXF-CLI-1.2` 和 `STEEL-DXF-CLASSIFICATION-1.2`。

## 1.1.0 — 2026-07-18

- 新增 `--json`：stdout 输出稳定的 `STEEL-DXF-CLI-1.1` 单对象摘要；
- 固定 stdout、stderr 和退出码契约，新增调用/输入契约退出码 64；
- 新增 `--version`；
- 文件分类报告 schema 升级为 `STEEL-DXF-CLASSIFICATION-1.1`；
- 新增正式 [I/O 契约](docs/IO_CONTRACT.md)，并同步 README、规则与真实验证记录；
- 保持标题栏证据规则、fail-closed 策略、文件名预处理和分类目录行为不变。

## 1.0.0

初始独立发布：基于 Tekla 零件图右上标题栏的钢结构 DXF 类型分类。
