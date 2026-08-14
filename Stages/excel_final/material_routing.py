"""D 系列钢材规格按材质族的权威路由。

业务规则（CONTEXT.md「五金手册材质路由」）：D 系列规格按材质族路由到唯一
手册类别——HRB 查询螺纹钢（``rebar``），HPB/Q235B/Q355B 查询圆钢
（``round_bar``）。路由只决定查询类别，不代表手册一定命中；其他材质
不得跨类别借用重量。

镜像契约：后端适配器
（``app.modules.excel_processing.stage_adapter._normalize_lookup_request``）
与前端手册校验复刻同一映射；由跨 seam 测试防止两侧漂移——改动必须三侧
同步。
"""

from __future__ import annotations

# 类别键对应手册仓储消费的 HandbookCategory 值（rebar=螺纹钢，
# round_bar=圆钢）。
D_MATERIAL_CATEGORY_BY_PREFIX = {
    "HRB": "rebar",
    "HPB": "round_bar",
    "Q235B": "round_bar",
    "Q355B": "round_bar",
}


def normalize_material(material: object) -> str:
    """规范化材质记号：去除空白并转大写。"""
    return str(material or "").replace(" ", "").replace("　", "").upper()


def material_class(material: object) -> str | None:
    """返回匹配的材质族前缀（键），或 None。

    前缀匹配（``startswith``），例如 ``HRB400`` 映射到 ``HRB``。
    """
    normalized = normalize_material(material)
    return next(
        (
            prefix
            for prefix in D_MATERIAL_CATEGORY_BY_PREFIX
            if normalized.startswith(prefix)
        ),
        None,
    )


def d_series_category(material: object) -> str | None:
    """把 D 系列材质路由到唯一手册类别，或 None。

    None 表示材质族未知——调用方绝不能猜测类别（禁止跨类别借用重量）。
    """
    family = material_class(material)
    return D_MATERIAL_CATEGORY_BY_PREFIX.get(family) if family is not None else None


__all__ = [
    "D_MATERIAL_CATEGORY_BY_PREFIX",
    "d_series_category",
    "material_class",
    "normalize_material",
]
