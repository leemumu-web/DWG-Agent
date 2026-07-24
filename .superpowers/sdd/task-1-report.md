# Task 1 实现报告：标准余料图纸解析契约

## 实现范围

仅修改 `Stages/remnant_drawing_reader` 及其测试和 README；未修改后端、前端，未读取或提交主工作区 PNG。

解析契约已升级为 `schema_version = "1.1"`、Stage/parser 版本 `0.4.0`。新增 `standard_offcut` 摘要（块类型、原始规格、厚度、长度、宽度、材质、余料编号）：Python API 保留 `Decimal`，`to_dict()` 将三个数值无损转换为 JSON 十进制字符串。

解析器只接受名称大小写不敏感且完整等于 `offcut_zh_cn` 的 `INSERT`，不会从普通文字推断标准字段。`GG` 使用 `Decimal` 解析，支持可选正负号、`x`/`X`/`×`、空格和小数；仅厚度取绝对值，全部结果尺寸必须大于零。`CZ` 和 `YLBH` 分别映射材质和余料编号。缺失块、重复块、缺失/空白必要属性及非法规格分别产生稳定中文告警，并拒绝产生任意标准摘要。原有材料、项目、零件候选字段保持存在。

## TDD 记录

RED（先新增测试，未写生产代码）：

```powershell
uv run pytest tests/test_reader.py::test_extracts_standard_offcut_summary_from_exact_case_insensitive_block -q
```

结果：预期失败，`schema_version` 实际为 `1.0` 而测试要求 `1.1`。

随后新增完整标准块测试集后再次 RED：

```powershell
uv run pytest tests/test_reader.py -q -k 'standard_offcut or standard_summary'
```

结果：预期失败，17 个测试失败；原因是旧 `ParseResult` 没有 `standard_offcut` 字段，且契约版本仍为 `1.0`。

GREEN（最小实现后）：

```powershell
uv run pytest tests/test_reader.py -q -k 'standard_offcut or standard_summary'
```

结果：17 passed。后续独立审查提出“完整等于”名称的回归缺口，补充 `prefix_offcut_zh_cn` 和 `offcut_zh_cn_suffix` 用例后执行全量验证。

## 验证与自查

最终命令：

```powershell
uv run python -m compileall -q src
uv run pytest
git diff --check
```

结果：命令退出码均为 0；pytest 收集 60 项，`60 passed in 1.94s`；`git diff --check` 无空白错误。

自查确认：

- 标准有效块、正/负/无符号厚度、三种分隔符、空格与小数均有测试。
- 零值、负长度、非法规格、缺失块、重复块、每个必要属性缺失或空白均有测试。
- 普通文字和部分块名不能推断/匹配标准摘要均有测试。
- JSON 序列化和旧候选字段兼容均有测试；旧测试随新增“缺失标准块”告警调整为仅忽略该新增告警后继续验证旧候选行为。
- 独立只读审查：无 Critical/Important；审查建议的部分名称匹配测试已补充。

## 提交

- 实现提交：`4d933240b271ff08916a906430cfceb35028f460` — `feat(reader): parse standard offcut blocks`
