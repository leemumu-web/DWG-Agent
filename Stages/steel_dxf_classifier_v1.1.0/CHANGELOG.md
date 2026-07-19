# Changelog

## 1.1.0 — 2026-07-18

- 新增 `--json`：stdout 输出稳定的 `STEEL-DXF-CLI-1.1` 单对象摘要；
- 固定 stdout、stderr 和退出码契约，新增调用/输入契约退出码 64；
- 新增 `--version`；
- 文件分类报告 schema 升级为 `STEEL-DXF-CLASSIFICATION-1.1`；
- 新增正式 [I/O 契约](docs/IO_CONTRACT.md)，并同步 README、规则与真实验证记录；
- 保持标题栏证据规则、fail-closed 策略、文件名预处理和分类目录行为不变。

## 1.0.0

初始独立发布：基于 Tekla 零件图右上标题栏的钢结构 DXF 类型分类。
