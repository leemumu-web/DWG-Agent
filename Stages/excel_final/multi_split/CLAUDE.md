# multi_split 兼容库边界

`multi_split` 是从历史 SunFire/VBA 行为移植的兼容包。它保留旧 DataFrame/Excel API 及其回归测试，供旧调用者对照；它不是 Excel Final 规范流程的分类器、手册查询器、重量引擎或总编排器。

## 规范流程实际使用部分

规范流程只调用 `multi_split.profile.split_fabricated_geometry` 这一纯几何内核。调用前已经由 `spec_parser.py` 完成类别门控，因此该函数只接收确认的 BH、BOX 或 BT 与四个正尺寸参数。

```python
from decimal import Decimal
from multi_split.profile import split_fabricated_geometry

children = split_fabricated_geometry(
    "BOX",
    Decimal("700"),
    Decimal("700"),
    Decimal("36"),
    Decimal("36"),
)
```

规范几何为：

| 类别 | 腹板厚×宽/数量倍率 | 翼板厚×宽/数量倍率 |
|---|---|---|
| BH | `tw × (H-2tf)` / 1 | `tf × B` / 2 |
| BOX | `tw × (H-2tf)` / 2 | `tf × B` / 2 |
| BT | `tw × (H-tf)` / 1 | `tf × B` / 1 |

标签为 `BH腹/BH翼`、`BOX腹/BOX翼`、`BT腹/BT翼`。非法内嵌尺寸抛出 `ValueError`，由 `splitter.py` 转为严重质量问题。

## 不属于规范流程的兼容行为

包内仍可能存在以下历史能力：

- 按模式列表猜测和拆分型材；
- 普通工字钢或 HA 的历史拆分扩展；
- 对 PL 厚宽排序；
- DataFrame 排序、组合、附件和 Excel round-trip；
- 旧 VBA 标签或默认模式。

这些行为只能作为兼容 API 使用，不能从 `pipeline.py`、`canonical_pipeline.py` 或 backend 生产入口调用。规范规则以根目录 `PROCESS.md` 为准。

## 修改约束

1. 修改 `split_fabricated_geometry` 时，必须同时通过 `tests/test_splitter.py`、`tests/test_weights.py`、`tests/test_rect.py` 和 `multi_split/tests`。
2. 兼容 API 的变化不得改变规范分类结果。
3. 不在本包中连接 MySQL、计算父源重量、生成 `part` 或写质量报告。
4. 不把兼容默认模式描述成生产默认规则。
