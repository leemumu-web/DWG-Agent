"""Component Bill of Materials (BOM) maker -- the core SunFire module.

Port of the VBA `qdmade` function + frmQD form (~600 lines of VBA).

Algorithm overview:
  1. Auto-map 12 standard keywords to DataFrame columns via substring match
  2. For each unique component (identified by 构件号 + unique_cols):
     a. Filter all parts belonging to this component
     b. Calculate total weight, max length
     c. Detect attachments (连接板/附件/散件)
     d. Detect main materials by weight proportion
     e. Combine main materials into steel profile spec (BH/BT/PL)
     f. Build output row

The core steel profile logic has 5 branches based on how many main materials
are found (flagq = 0, 1, 2, 3, or >3).
"""

from __future__ import annotations

import logging
import numpy as np
import pandas as pd

from .config import SunFireConfig
from .models import ColumnMapping
from .utils import resolve_column, resolve_columns, strip_newlines

logger = logging.getLogger(__name__)


def qdmade(
    df: pd.DataFrame,
    other_cols: list[str | int],
    unique_cols: list[str | int],
    column_mapping: ColumnMapping | None = None,
    config: SunFireConfig | None = None,
    header_row: int | None = None,
) -> pd.DataFrame:
    """Generate a component BOM from a detailed parts list.

    This is the main processing function.  It reads a parts list (each row
    is one part of one component), identifies main materials, combines them
    into steel profile specifications (BH/BT/PL), and outputs one row per
    component.

    Args:
        df: Input DataFrame with parts list.
        other_cols: Column names/indices for additional columns to carry
                    through to the output.
        unique_cols: Columns that uniquely identify a component (subset of
                     other_cols in VBA, but not required here).
        column_mapping: Optional override for the 12 standard keyword mappings.
        config: SunFireConfig (used for attachment/main-material keywords).
        header_row: If df was loaded with header=None, the 0-based header row.
                    If None, df is assumed to already have column names set.

    Returns:
        DataFrame with one row per component and columns:
          图号, 构件号, 主材规格, 长度, 材质, 构件数量,
          单重, 总重, 制作单位, 出厂附件, + other_cols
    """
    if config is None:
        config = SunFireConfig()
    if column_mapping is None:
        column_mapping = config.column_mapping

    # Resolve column selectors
    other_cols = resolve_columns(df, other_cols)
    unique_cols = resolve_columns(df, unique_cols)

    # ---------- Step 1: Map 12 standard keywords ----------
    col_map = _map_standard_columns(df.columns.tolist(), column_mapping)

    # Validate: unique_cols should be a subset of other_cols (VBA lines 82-94)
    for uc in unique_cols:
        if uc not in other_cols:
            raise ValueError(
                "在其他内容选项框中必须选择保证构件号唯一条件中选择的内容！！"
            )

    # Validate: other_cols should not contain standard keyword columns (VBA lines 113-122)
    kw_cols = set(col_map.values())
    for oc in other_cols:
        if oc in kw_cols:
            raise ValueError(
                f"其他选项中选择了与主标题重复的内容 '{oc}'，请重新选择！"
            )

    # Validate header -- no empty header cells (VBA lines 38-46)
    for col in df.columns:
        if col == "" or (isinstance(col, float) and np.isnan(col)):
            raise ValueError("标题行不能为空！")

    # Get column indices
    col_drawing_no = col_map["drawing_no"]
    col_component_no = col_map["component_no"]
    col_component_qty = col_map["component_qty"]
    col_part_no = col_map["part_no"]
    col_spec = col_map["spec"]
    col_width = col_map["width"]
    col_length = col_map["length"]
    col_material = col_map["material"]
    col_total_parts = col_map["total_parts"]
    col_total_weight = col_map["total_weight"]
    col_part_type = col_map["part_type"]
    col_manufacturer = col_map["manufacturer"]

    # ---------- Step 2: Get unique components ----------
    # VBA: AdvancedFilter unique on [component_no] + unique_cols
    unique_key_cols = [col_component_no] + unique_cols
    components = df[unique_key_cols].drop_duplicates().reset_index(drop=True)

    # ---------- Step 3: Process each component ----------
    output_rows = []

    for _, comp_row in components.iterrows():
        # Build filter criteria (match VBA CriteriaRange)
        comp_mask = pd.Series(True, index=df.index)
        for col in unique_key_cols:
            comp_mask &= df[col] == comp_row[col]

        parts = df[comp_mask].copy()
        if len(parts) == 0:
            continue

        # Step 3b: Calculate aggregates
        total_weight = pd.to_numeric(parts[col_total_weight], errors="coerce").sum()
        max_length = pd.to_numeric(parts[col_length], errors="coerce").max()
        if pd.isna(max_length):
            max_length = 0.0

        # Step 3c: Find attachments (连接板/附件/散件)
        fj = _build_attachment_string(
            parts, col_part_no, col_spec, col_width, col_length,
            col_part_type, col_total_parts, col_component_qty,
            config,
        )

        # Step 3d: Detect main materials
        main_mats = _detect_main_materials(
            parts, col_part_type, col_length, max_length,
            col_spec, col_width, col_material,
            col_total_parts, col_component_qty,
            config,
        )
        flagq = len(main_mats)

        # Step 3e: Determine profile
        spec, length_val, material_val = _combine_profiles(main_mats, flagq)

        # Step 3f: Build output row
        comp_qty_val = parts[col_component_qty].iloc[0]
        unit_weight = total_weight / float(comp_qty_val) if float(comp_qty_val) != 0 else 0

        row = {
            "图号": parts[col_drawing_no].iloc[0],
            "构件号": parts[col_component_no].iloc[0],
            "主材规格": spec,
            "长度": length_val,
            "材质": material_val,
            "构件数量": comp_qty_val,
            "单重": round(unit_weight, 2),
            "总重": round(total_weight, 2),
            "制作单位": str(parts[col_manufacturer].iloc[0])
            if not pd.isna(parts[col_manufacturer].iloc[0])
            else "",
            "出厂附件": fj,
        }

        # Add other columns
        for oc in other_cols:
            if oc in parts.columns:
                row[oc] = str(parts[oc].iloc[0]) if not pd.isna(parts[oc].iloc[0]) else ""

        output_rows.append(row)

    return pd.DataFrame(output_rows)


# =============================================================================
# Internal helpers
# =============================================================================


def _map_standard_columns(
    headers: list[str], mapping: ColumnMapping
) -> dict[str, str]:
    """Map each of the 12 standard keywords to an actual column name.

    Uses substring match (VBA InStr equivalent).  Each keyword must match
    exactly one column, otherwise an error is raised.

    Returns:
        Dict mapping keyword field names → actual column names.
    """
    result = {}
    for field_name, keyword in mapping.to_keyword_list():
        matches = []
        for h in headers:
            h_clean = strip_newlines(h)
            if keyword in h_clean:
                matches.append(h)
        if len(matches) == 0:
            raise ValueError(f"未找到标题:{keyword}.")
        if len(matches) > 1:
            # If multiple matches, try exact match first
            exact = [m for m in matches if strip_newlines(m) == keyword]
            if exact:
                result[field_name] = exact[0]
            else:
                # Take the first match
                result[field_name] = matches[0]
        else:
            result[field_name] = matches[0]
    return result


def _safe_str(val) -> str:
    """Convert a cell value to string, returning '' for NaN/None/empty."""
    if val is None:
        return ""
    if isinstance(val, float) and np.isnan(val):
        return ""
    s = str(val).strip()
    return s


def _build_attachment_string(
    parts: pd.DataFrame,
    col_part_no: str,
    col_spec: str,
    col_width: str,
    col_length: str,
    col_part_type: str,
    col_total_parts: str,
    col_component_qty: str,
    config: SunFireConfig,
) -> str:
    """Build the attachment description string (出厂附件).

    VBA equivalent (FRMSPLIT / qdmade):
      For each part where 零件类型 contains 连接板/附件/散件:
        Build: 零件号:规格*宽度*长度=零件总数/构件数量,
        If 规格 is numeric → prepend "PL".

    Multiple attachments are joined with commas.
    Rows with missing part_no / spec are silently skipped
    (mirrors the VBA ``If Not IsEmpty(...)`` guard on each column).
    """
    fj_parts = []
    attachment_kws = config.attachment_keywords

    for _, row in parts.iterrows():
        part_type = _safe_str(row.get(col_part_type))
        # Check if this is an attachment
        is_attachment = any(kw in part_type for kw in attachment_kws)
        if not is_attachment:
            continue

        pno = _safe_str(row.get(col_part_no))
        sp = _safe_str(row.get(col_spec))
        w = _safe_str(row.get(col_width))
        l = _safe_str(row.get(col_length))

        # VBA: If Not IsEmpty check — skip rows missing key fields
        if not pno or not sp:
            continue

        # If spec is numeric → prepend "PL" (VBA IsNumeric check)
        try:
            float(sp)
            sp = "PL" + sp
        except (ValueError, TypeError):
            pass

        try:
            tp = float(row.get(col_total_parts, 0))
            cq = float(row.get(col_component_qty, 1))
            if cq == 0:
                cq = 1
            ratio = tp / cq
            ratio_str = str(int(ratio)) if ratio == int(ratio) else f"{ratio:.2f}"
        except (ValueError, TypeError):
            ratio_str = "0"

        fj_str = f"{pno}:{sp}*{w}*{l}={ratio_str}"
        fj_parts.append(fj_str)

    return ",".join(fj_parts)


def _detect_main_materials(
    parts: pd.DataFrame,
    col_part_type: str,
    col_length: str,
    max_length: float,
    col_spec: str,
    col_width: str,
    col_material: str,
    col_total_parts: str,
    col_component_qty: str,
    config: SunFireConfig,
) -> list[dict]:
    """Detect main materials from parts list.

    A main material is a part whose type contains '主' and whose
    length is within max_length/5 of the max length.

    Returns list of dicts, each with keys:
      spec, width, length, count, is_numeric, material
    """
    main_kw = config.main_material_keyword

    if max_length == 0:
        return []

    main_rows = []
    for _, row in parts.iterrows():
        part_type = _safe_str(row.get(col_part_type))
        if main_kw not in part_type:
            continue

        try:
            length_val = float(row.get(col_length, 0))
        except (ValueError, TypeError):
            length_val = 0
        # Guard: NaN length means we can't classify this part
        if np.isnan(length_val):
            continue

        if max_length - length_val >= max_length / 5:
            continue

        # Extract material properties using resolved column names
        spec_val = row.get(col_spec, "")
        if isinstance(spec_val, float) and np.isnan(spec_val):
            spec_val = ""
        width_val = row.get(col_width, "")
        if isinstance(width_val, float) and np.isnan(width_val):
            width_val = ""
        material_val = row.get(col_material, "")
        if isinstance(material_val, float) and np.isnan(material_val):
            material_val = ""

        is_num = False
        try:
            float(str(spec_val))
            is_num = True
            spec_num = float(str(spec_val))
        except (ValueError, TypeError):
            is_num = False
            spec_num = None

        try:
            width_num = float(str(width_val)) if width_val else 0
        except (ValueError, TypeError):
            width_num = 0

        len_num = float(length_val) if length_val else 0

        # Count: total_parts / component_qty (VBA midprofile(4))
        try:
            tp = float(row.get(col_total_parts, 1))
            cq = float(row.get(col_component_qty, 1))
            if cq == 0:
                cq = 1
            count = tp / cq
        except (ValueError, TypeError):
            count = 1

        main_rows.append({
            "spec": int(spec_num) if is_num and spec_num is not None and spec_num == int(spec_num) else (spec_num if is_num else spec_val),
            "width": int(width_num) if width_num == int(width_num) else width_num,
            "length": len_num,
            "count": count,
            "is_numeric": is_num,
            "material": str(material_val),
        })

    return main_rows


def _combine_profiles(
    materials: list[dict], flagq: int
) -> tuple[str, float, str]:
    """Combine main materials into a steel profile specification.

    Args:
        materials: List of main material dicts.
        flagq: Number of main materials (= len(materials)).

    Returns:
        Tuple of (profile_spec_string, length, material).
    """
    spec = ""
    length_val = 0.0
    material_val = ""

    # ---- Helper: pick profile from max-length material ----
    def _pick_max_length(mats):
        best = mats[0]
        for m in mats[1:]:
            if m["length"] > best["length"]:
                best = m
        if best["is_numeric"]:
            s = f"PL{best['spec']}*{best['width']}"
        else:
            s = str(best["spec"])
        return s, best["length"], best["material"]

    # ---- flagq == 0: No main material ----
    if flagq == 0:
        logger.warning("未找到主材，构件清单中将缺少主材信息")
        spec = ""
        length_val = 0.0
        material_val = ""

    # ---- flagq == 1: Single main material ----
    elif flagq == 1:
        m = materials[0]
        if m["is_numeric"]:
            if m["width"]:
                spec = f"PL{m['spec']}*{m['width']}"
            else:
                spec = f"PL{m['spec']}"
        else:
            if m["width"]:
                spec = f"{m['spec']}*{m['width']}"
            else:
                spec = str(m["spec"])
        # Prepend count if > 1
        if m["count"] > 1:
            cstr = str(int(m["count"])) if m["count"] == int(m["count"]) else str(m["count"])
            spec = f"{cstr}{spec}"
        length_val = m["length"]
        material_val = m["material"]

    # ---- flagq == 2: Two main materials ----
    elif flagq == 2:
        m1, m2 = materials[0], materials[1]

        if m1["is_numeric"] and m2["is_numeric"]:
            # Both are plates → try BH/BT/PL
            s1, w1, l1, c1 = m1["spec"], m1["width"], m1["length"], m1["count"]
            s2, w2, l2, c2 = m2["spec"], m2["width"], m2["length"], m2["count"]

            # BH: one is web (thinner), other is flange (2x count)
            if s1 < s2 and abs(c2 / c1 - 2) < 0.01:
                # m1=web, m2=flange
                spec = f"BH{int(w1 + 2*s2)}*{int(w2)}*{int(s1)}*{int(s2)}"
                if c1 > 1:
                    spec = f"{int(c1)}{spec}"
                length_val = max(l1, l2)
                # material from the one with max length
                if l1 >= l2:
                    material_val = m1["material"]
                else:
                    material_val = m2["material"]

            elif s1 > s2 and abs(c1 / c2 - 2) < 0.01:
                # m2=web, m1=flange
                spec = f"BH{int(w2 + 2*s1)}*{int(w1)}*{int(s2)}*{int(s1)}"
                if c2 > 1:
                    spec = f"{int(c2)}{spec}"
                length_val = max(l1, l2)
                if l1 >= l2:
                    material_val = m1["material"]
                else:
                    material_val = m2["material"]

            elif abs(c1 - c2) < 0.01:
                # Equal counts → try BT
                if abs(c1 - 1) < 0.01:
                    if s1 < s2:
                        # BT: m1=web, m2=flange
                        spec = f"BT{int(w1 + s2)}*{int(w2)}*{int(s1)}*{int(s2)}"
                    else:
                        # m2=web, m1=flange
                        spec = f"BT{int(w2 + s1)}*{int(w1)}*{int(s2)}*{int(s1)}"
                    length_val = max(l1, l2)
                    if l1 >= l2:
                        material_val = m1["material"]
                    else:
                        material_val = m2["material"]
                else:
                    # Equal counts but not 1 → PL fallback
                    spec, length_val, material_val = _pick_max_length([m1, m2])
            else:
                spec, length_val, material_val = _pick_max_length([m1, m2])
        else:
            # Not both numeric → use named profile
            if not m1["is_numeric"]:
                spec = str(m1["spec"])
                length_val = m1["length"]
                material_val = m1["material"]
            else:
                spec = str(m2["spec"])
                length_val = m2["length"]
                material_val = m2["material"]

    # ---- flagq == 3: Three main materials ----
    elif flagq == 3:
        m_a, m_b, m_c = materials[0], materials[1], materials[2]

        # Check conditions for BH combination
        # VBA: (m_a,m_b are numeric) OR (m_c is numeric AND all same material)
        can_combine = (
            (m_a["is_numeric"] and m_b["is_numeric"])
            or (
                m_c["is_numeric"]
                and m_a["material"] == m_b["material"] == m_c["material"]
            )
        )

        if can_combine:
            # All three specs must be numeric (int/float) for BH combine logic
            all_num = (
                m_a["is_numeric"] and m_b["is_numeric"] and m_c["is_numeric"]
            )
            if (
                all_num
                and abs(m_a["count"] - 1) < 0.01
                and abs(m_b["count"] - 1) < 0.01
                and abs(m_c["count"] - 1) < 0.01
            ):
                # All counts = 1 → try BH with web being the thinnest
                specs = [m_a["spec"], m_b["spec"], m_c["spec"]]
                widths = [m_a["width"], m_b["width"], m_c["width"]]
                lengths = [m_a["length"], m_b["length"], m_c["length"]]
                mat = [m_a["material"], m_b["material"], m_c["material"]]

                min_idx = specs.index(min(specs))
                # The other two are flanges
                flange_idxs = [i for i in range(3) if i != min_idx]
                f1, f2 = flange_idxs[0], flange_idxs[1]

                if abs(specs[f1] - specs[f2]) < 0.01:
                    # Same flange thickness
                    if widths[f1] == widths[f2]:
                        spec = (
                            f"BH{int(widths[min_idx] + 2*specs[f1])}"
                            f"*{int(widths[f1])}"
                            f"*{int(specs[min_idx])}"
                            f"*{int(specs[f1])}"
                        )
                    elif widths[f1] > widths[f2]:
                        spec = (
                            f"BH{int(widths[min_idx] + 2*specs[f1])}"
                            f"*{int(widths[f1])}({int(widths[f2])})"
                            f"*{int(specs[min_idx])}"
                            f"*{int(specs[f1])}"
                        )
                    else:
                        spec = (
                            f"BH{int(widths[min_idx] + 2*specs[f1])}"
                            f"*{int(widths[f2])}({int(widths[f1])})"
                            f"*{int(specs[min_idx])}"
                            f"*{int(specs[f1])}"
                        )
                    length_val = max(lengths)
                    material_val = mat[lengths.index(length_val)]
                else:
                    # Different flange thickness → PL fallback
                    spec, length_val, material_val = _pick_max_length(materials)
            else:
                # Not all counts = 1 → PL fallback
                spec, length_val, material_val = _pick_max_length(materials)
        else:
            # Can't combine → use max-length piece
            spec, length_val, material_val = _pick_max_length(materials)

    # ---- flagq > 3: Four+ materials → pick max length ----
    else:
        # Find max length
        length_val = max(m["length"] for m in materials)
        for m in materials:
            if m["length"] == length_val:
                if m["is_numeric"]:
                    spec = f"PL{m['spec']}*{m['width']}"
                else:
                    spec = str(m["spec"])
                material_val = m["material"]
                break

    return spec, length_val, material_val
