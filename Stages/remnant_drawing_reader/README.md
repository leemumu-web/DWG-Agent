# Remnant Drawing Reader

本 Stage 确定性读取单个 DXF 中的材质、项目编号和多个零件编号候选，同时保留文字实体类型、图层、嵌套块路径、坐标、handle 和原始文本证据。`pyproject.toml` 固定独立依赖和 CLI，`src/remnant_drawing_reader/` 拥有读取、归一化、分类及版本化结果模型，`tests/test_reader.py` 覆盖中文标签、牌号后缀、嵌套块、去重、冲突和损坏文件。

```powershell
uv run remnant-drawing-read input.dxf --output result.json
```

输出契约版本为 `1.1`。在保留 `material_candidates`、`project_candidates` 和
`part_candidates` 的基础上，`standard_offcut` 会在图纸含有唯一标准块时提供可序列化
的摘要：`block_type`、`raw_specification`、`thickness`、`length`、`width`、`material`
和 `remnant_number`。三个尺寸在 Python API 中为 `Decimal`，JSON 中为无精度损失的十进制
字符串。

标准块仅识别名称（大小写不敏感）完整等于 `offcut_zh_cn` 的 `INSERT`。它必须带有非空
`GG`、`CZ`、`YLBH` 属性；`GG` 支持正负号、`x`/`X`/`×`、空格和小数，厚度取绝对值，而
三个结果尺寸都必须大于零。缺失标准块、重复标准块、必要属性缺失或非法规格分别会输出
稳定的中文告警，且不会从普通图纸文字推断这些标准字段。

能力边界：Stage 不得访问 HTTP、数据库、对象存储、用户或权限，也不能把候选直接升级成正式库存。标准材质和别名匹配属于后端余料领域；DWG 转换属于 CAD 处理领域。本 Stage 只接受单个已验证 DXF 并输出 JSON，真实业务 DWG 不提交仓库。
