# Remnant Drawing Reader

本 Stage 确定性读取单个 DXF 中的材质、项目编号和多个零件编号候选，同时保留文字实体类型、图层、嵌套块路径、坐标、handle 和原始文本证据。`pyproject.toml` 固定独立依赖和 CLI，`src/remnant_drawing_reader/` 拥有读取、归一化、分类及版本化结果模型，`tests/test_reader.py` 覆盖中文标签、牌号后缀、嵌套块、去重、冲突和损坏文件。

```powershell
uv run remnant-drawing-read input.dxf --output result.json
```

输出契约版本为 `1.0`。厚度不属于解析输出，由余料库界面人工填写。

能力边界：Stage 不得访问 HTTP、数据库、对象存储、用户或权限，也不能把候选直接升级成正式库存。标准材质和别名匹配属于后端余料领域；DWG 转换属于 CAD 处理领域。本 Stage 只接受单个已验证 DXF 并输出 JSON，真实业务 DWG 不提交仓库。
