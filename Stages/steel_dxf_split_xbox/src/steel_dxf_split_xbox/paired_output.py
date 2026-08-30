from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

import ezdxf
from ezdxf.lldxf.const import DXFError
from ezdxf.lldxf.const import DXFValueError


_XDATA_APPID = "STEEL_DXF_SPLIT"
_CUT_XDATA_SCHEMA = "BH-CUT-FEATURE-1.0"


class PairedOutputValidationError(ValueError):
    """A normal/allowance pair is incomplete, inconsistent, or unverified."""


def _cut_hole_geometry(
    document: ezdxf.document.Drawing,
) -> tuple[tuple[object, ...], ...]:
    values: list[tuple[object, ...]] = []
    for entity in document.modelspace():
        if entity.dxf.get("layer", "") != "CUT_HOLE":
            continue
        if entity.dxftype() == "CIRCLE":
            values.append(
                (
                    "CIRCLE",
                    float(entity.dxf.center.x),
                    float(entity.dxf.center.y),
                    float(entity.dxf.radius),
                    int(entity.dxf.color),
                )
            )
        elif entity.dxftype() == "LWPOLYLINE":
            values.append(
                (
                    "LWPOLYLINE",
                    bool(entity.closed),
                    int(entity.dxf.color),
                    tuple(
                        tuple(float(value) for value in point)
                        for point in entity.get_points("xyb")
                    ),
                )
            )
        else:
            values.append(
                (
                    entity.dxftype(),
                    int(entity.dxf.get("color", 256)),
                )
            )
    return tuple(sorted(values, key=repr))


def _bh_bound_cuts(
    document: ezdxf.document.Drawing,
) -> dict[str, tuple[float, float, float, int]]:
    result = {}
    for entity in document.modelspace().query("CIRCLE[layer=='CUT_HOLE']"):
        try:
            tags = list(entity.get_xdata(_XDATA_APPID))
        except DXFValueError as exc:
            raise PairedOutputValidationError(
                "BH CUT_HOLE feature contract is missing"
            ) from exc
        if (
            [tag.code for tag in tags] != [1000, 1000, 1000]
            or tags[0].value != _CUT_XDATA_SCHEMA
        ):
            raise PairedOutputValidationError(
                "BH CUT_HOLE feature contract is invalid"
            )
        cut_id = str(tags[2].value)
        if cut_id in result:
            raise PairedOutputValidationError(
                "BH CUT_HOLE feature contract has duplicate IDs"
            )
        result[cut_id] = (
            float(entity.dxf.center.x),
            float(entity.dxf.center.y),
            float(entity.dxf.radius),
            int(entity.dxf.color),
        )
    return result


def _bh_cut_feature_contracts_match(
    normal: ezdxf.document.Drawing,
    allowance: ezdxf.document.Drawing,
    report: dict[str, Any],
) -> bool:
    normal_cuts = _bh_bound_cuts(normal)
    allowance_cuts = _bh_bound_cuts(allowance)
    if set(normal_cuts) != set(allowance_cuts):
        return False
    for cut_id, before in normal_cuts.items():
        after = allowance_cuts[cut_id]
        if (
            not math.isclose(after[0], before[0], abs_tol=1e-6)
            or not math.isclose(after[1], before[1], abs_tol=1e-6)
            or not math.isclose(after[2], before[2], abs_tol=1e-9)
            or after[3] != before[3]
        ):
            return False
    normal_inner = tuple(
        item for item in _cut_hole_geometry(normal) if item[0] != "CIRCLE"
    )
    allowance_inner = tuple(
        item for item in _cut_hole_geometry(allowance) if item[0] != "CIRCLE"
    )
    return normal_inner == allowance_inner


def _entity_counts(
    document: ezdxf.document.Drawing,
) -> dict[tuple[str, str], int]:
    counts: dict[tuple[str, str], int] = {}
    for entity in document.modelspace():
        key = (entity.dxftype(), str(entity.dxf.get("layer", "0")))
        counts[key] = counts.get(key, 0) + 1
    return counts


def _load_report(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise PairedOutputValidationError(
            f"weld allowance report is unreadable: {path}"
        ) from exc
    if not isinstance(payload, dict):
        raise PairedOutputValidationError(
            "weld allowance report must be a JSON object"
        )
    return payload


def _load_dxf(path: Path, description: str) -> ezdxf.document.Drawing:
    try:
        document = ezdxf.readfile(path)
    except (OSError, DXFError) as exc:
        raise PairedOutputValidationError(
            f"{description} DXF is unreadable: {path}"
        ) from exc
    audit = document.audit()
    if audit.has_errors:
        raise PairedOutputValidationError(
            f"{description} DXF audit failed with {len(audit.errors)} errors"
        )
    return document


def _positive_extension_count(
    report: dict[str, Any],
    *,
    family: str,
) -> int:
    key = "plates" if family == "BH" else "groups"
    items = report.get(key)
    if not isinstance(items, list) or not items:
        raise PairedOutputValidationError(
            "weld allowance report has no verified plate groups"
        )
    positive = 0
    for item in items:
        if not isinstance(item, dict):
            raise PairedOutputValidationError(
                "weld allowance plate result is invalid"
            )
        try:
            before = float(item["before_main_length_mm"])
            allowance = float(item["allowance_mm"])
            after = float(item["after_main_length_mm"])
        except (KeyError, TypeError, ValueError) as exc:
            raise PairedOutputValidationError(
                "weld allowance plate lengths are invalid"
            ) from exc
        if (
            not all(math.isfinite(value) for value in (before, allowance, after))
            or before <= 0.0
            or allowance < 0.0
            or not math.isclose(after, before + allowance, abs_tol=1e-6)
        ):
            raise PairedOutputValidationError(
                "weld allowance plate lengths do not close"
            )
        positive += allowance > 0.0
    return positive


def validate_paired_outputs(
    normal_path: str | Path,
    allowance_path: str | Path,
    allowance_report_path: str | Path,
    *,
    family: str,
) -> dict[str, object]:
    """Prove that one complete normal DXF and its allowance variant form a pair."""

    if family not in {"BH", "BOX", "XBOX"}:
        raise PairedOutputValidationError("paired output family is unsupported")
    normal_path = Path(normal_path)
    allowance_path = Path(allowance_path)
    allowance_report_path = Path(allowance_report_path)
    if normal_path.resolve() == allowance_path.resolve():
        raise PairedOutputValidationError(
            "normal and weld allowance outputs must be different files"
        )
    if not normal_path.is_file() or not allowance_path.is_file():
        raise PairedOutputValidationError("paired DXF output is incomplete")
    report = _load_report(allowance_report_path)
    checks = report.get("checks")
    if (
        report.get("ok") is not True
        or report.get("original_split_result_preserved") is not True
        or not isinstance(checks, dict)
        or not checks
        or any(value is not True for value in checks.values())
    ):
        raise PairedOutputValidationError(
            "weld allowance report does not prove a complete output"
        )
    if (
        report.get("input_split_dxf") != str(normal_path.resolve())
        or report.get("output_dxf") != str(allowance_path.resolve())
    ):
        raise PairedOutputValidationError(
            "weld allowance report is not bound to this output pair"
        )

    normal = _load_dxf(normal_path, "normal")
    allowance = _load_dxf(allowance_path, "weld allowance")
    if normal.dxfversion != allowance.dxfversion or int(
        normal.header.get("$INSUNITS", 0)
    ) != int(allowance.header.get("$INSUNITS", 0)):
        raise PairedOutputValidationError(
            "normal and weld allowance DXF contracts differ"
        )
    if _entity_counts(normal) != _entity_counts(allowance):
        raise PairedOutputValidationError(
            "normal and weld allowance entity sets differ"
        )
    if family == "BH":
        if not _bh_cut_feature_contracts_match(normal, allowance, report):
            raise PairedOutputValidationError(
                "normal and weld allowance hole feature contract differs"
            )
    elif _cut_hole_geometry(normal) != _cut_hole_geometry(allowance):
        raise PairedOutputValidationError(
            "normal and weld allowance hole geometry or color differs"
        )
    positive_extension_count = _positive_extension_count(report, family=family)
    return {
        "ok": True,
        "family": family,
        "normal_dxf": str(normal_path.resolve()),
        "weld_allowance_dxf": str(allowance_path.resolve()),
        "positive_extension_count": positive_extension_count,
        "checks": {
            "normal_dxf_audit_clean": True,
            "weld_allowance_dxf_audit_clean": True,
            "same_dxf_and_unit_contract": True,
            "entity_and_layer_counts_identical": True,
            (
                "cut_hole_feature_contracts_match"
                if family == "BH"
                else "cut_hole_geometry_and_colors_identical"
            ): True,
            "allowance_report_complete": True,
            "allowance_lengths_close": True,
        },
    }
