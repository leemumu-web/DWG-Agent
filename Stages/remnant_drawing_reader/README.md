# Remnant Drawing Reader

确定性读取单个 DXF 中的材质、项目编号和多个零件编号候选，同时保留文字实体证据。Stage 不访问 HTTP、数据库、对象存储或用户信息。

```powershell
uv run remnant-drawing-read input.dxf --output result.json
```

输出契约版本为 `1.0`。厚度不属于解析输出，由余料库界面人工填写。
