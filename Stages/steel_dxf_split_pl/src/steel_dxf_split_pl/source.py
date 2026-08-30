from __future__ import annotations

import re
from pathlib import Path

from ezdxf.entities import DXFEntity, MText, Text

from .dxf_io import (
    DXFLoadError,
    iter_modelspace_entities,
    load_document,
    normalize_text,
    recursive_virtual_entities,
)

from .contracts import PLMetadata, PLSourceContext, PLSplitError

_PART_NUMBER = re.compile(r"^(?:p=)?([A-Za-z0-9][A-Za-z0-9._-]*)$", re.IGNORECASE)
_PL_SPEC = re.compile(
    r"^PL\s*(\d+(?:\.\d+)?)\s*[*X×]\s*(\d+(?:\.\d+)?)$",
    re.IGNORECASE,
)
_NUMBER = re.compile(r"^\d+(?:\.\d+)?$")


def _resolved(path: str | Path) -> Path:
    return Path(path).expanduser().resolve(strict=False)


def _contains(parent: Path, child: Path) -> bool:
    try:
        child.relative_to(parent)
    except ValueError:
        return False
    return True


def discover_input_files(
    input_path: str | Path,
    output_dir: str | Path,
) -> tuple[Path, ...]:
    source = _resolved(input_path)
    output = _resolved(output_dir)
    if source.is_dir() and (_contains(source, output) or _contains(output, source)):
        raise PLSplitError(
            "INPUT_OUTPUT_OVERLAP",
            "输入目录与输出目录不能相同或互相嵌套。",
        )
    if source.is_file():
        if source.suffix.casefold() != ".dxf":
            code = "DWG_NOT_SUPPORTED" if source.suffix.casefold() == ".dwg" else "INPUT_FORMAT"
            raise PLSplitError(code, "PL 拆板只读取 DXF；DWG 请先转换为 DXF。")
        return (source,)
    if not source.exists():
        raise PLSplitError("INPUT_NOT_FOUND", f"输入路径不存在：{source}")
    if not source.is_dir():
        raise PLSplitError("INPUT_FORMAT", "输入必须是一张 DXF 或包含 DXF 的目录。")
    files = tuple(
        sorted(
            (
                path.resolve()
                for path in source.iterdir()
                if path.is_file() and path.suffix.casefold() == ".dxf"
            ),
            key=lambda path: (path.name.casefold(), path.name),
        )
    )
    if not files:
        raise PLSplitError("NO_DXF_INPUT", "输入目录第一层没有 DXF 文件。")
    return files


def _text_value(entity: DXFEntity) -> str | None:
    if isinstance(entity, Text):
        return normalize_text(entity.dxf.text)
    if isinstance(entity, MText):
        return normalize_text(entity.text)
    return None


def _looks_like_sheet(entities: tuple[DXFEntity, ...]) -> bool:
    has_part_mark = False
    has_pl_spec = False
    for entity in entities:
        value = _text_value(entity)
        if not value:
            continue
        layer = entity.dxf.layer.casefold()
        if layer == "partmark" and _PART_NUMBER.fullmatch(value):
            has_part_mark = True
        if _PL_SPEC.fullmatch(value):
            has_pl_spec = True
    return has_part_mark and has_pl_spec


def load_source_contexts(source_path: str | Path) -> tuple[PLSourceContext, ...]:
    source = _resolved(source_path)
    if source.suffix.casefold() == ".dwg":
        raise PLSplitError(
            "DWG_NOT_SUPPORTED",
            "PL 拆板只读取 DXF；请先用仓库 DWG→DXF Stage 转换。",
        )
    if source.suffix.casefold() != ".dxf":
        raise PLSplitError("INPUT_FORMAT", "PL 拆板输入必须是 DXF。")
    try:
        document = load_document(source)
    except (DXFLoadError, OSError) as error:
        raise PLSplitError("DXF_LOAD_FAILED", f"DXF 无法审计读取：{error}") from error

    sheet_contexts: list[PLSourceContext] = []
    for insert in document.modelspace().query("INSERT"):
        entities = tuple(recursive_virtual_entities(insert))
        if not _looks_like_sheet(entities):
            continue
        sheet_contexts.append(
            PLSourceContext(
                source_path=source,
                context_id=str(insert.dxf.name),
                container_handle=insert.dxf.get("handle"),
                entities=entities,
            )
        )
    if sheet_contexts:
        return tuple(
            sorted(sheet_contexts, key=lambda context: context.context_id.casefold())
        )

    entities = tuple(iter_modelspace_entities(document))
    if not entities:
        raise PLSplitError("EMPTY_CONTEXT", "DXF 模型空间没有可解释实体。")
    return (
        PLSourceContext(
            source_path=source,
            context_id=source.stem,
            container_handle=None,
            entities=entities,
        ),
    )


def canonical_part_number(value: str) -> str:
    normalized = normalize_text(value)
    match = _PART_NUMBER.fullmatch(normalized)
    if match is None:
        raise PLSplitError("PART_NUMBER_INVALID", f"零件号格式无效：{normalized}")
    return match.group(1)


def _text_position(entity: DXFEntity) -> tuple[float, float, float]:
    if isinstance(entity, Text):
        point = entity.dxf.insert
        height = float(entity.dxf.get("height", 1.0))
    elif isinstance(entity, MText):
        point = entity.dxf.insert
        height = float(entity.dxf.get("char_height", 1.0))
    else:
        raise TypeError(entity.dxftype())
    return float(point.x), float(point.y), max(height, 0.4)


def _unique_part_number(context: PLSourceContext) -> str:
    part_mark_texts = [
        value
        for entity in context.entities
        if entity.dxf.layer.casefold() == "partmark"
        if (value := _text_value(entity))
    ]
    values = [
        canonical_part_number(value)
        for value in part_mark_texts
        if _PART_NUMBER.fullmatch(value)
    ]
    unique: dict[str, str] = {}
    for value in values:
        unique.setdefault(value.casefold(), value)
    if len(unique) == 1:
        return next(iter(unique.values()))
    if not part_mark_texts:
        rows = _spec_rows(context)
        if len(rows) == 1:
            specification = rows[0][0]
            spec_x, spec_y, spec_height = _text_position(specification)
            row_tolerance = max(0.1, spec_height * 0.25)
            for entity in context.entities:
                if entity.dxf.layer.casefold() != "otherobjecttype":
                    continue
                value = _text_value(entity)
                if not value or _PART_NUMBER.fullmatch(value) is None:
                    continue
                candidate = canonical_part_number(value)
                if (
                    "-" not in candidate
                    or not candidate[-1].isalnum()
                    or not any(character.isalpha() for character in candidate)
                ):
                    continue
                x, y, _ = _text_position(entity)
                if x >= spec_x or abs(y - spec_y) > row_tolerance:
                    continue
                unique.setdefault(candidate.casefold(), candidate)
            if len(unique) == 1:
                return next(iter(unique.values()))
    raise PLSplitError(
        "PART_NUMBER_AMBIGUOUS",
        f"单件必须有唯一零件号，当前识别到：{sorted(unique.values())}",
    )


def _spec_rows(context: PLSourceContext) -> list[tuple[DXFEntity, float, float]]:
    rows: list[tuple[DXFEntity, float, float]] = []
    for entity in context.entities:
        value = _text_value(entity)
        match = _PL_SPEC.fullmatch(value or "")
        if match is not None:
            rows.append((entity, float(match.group(1)), float(match.group(2))))
    return rows


def _row_bom_length(context: PLSourceContext, specification: DXFEntity) -> float:
    spec_x, spec_y, spec_height = _text_position(specification)
    row_tolerance = max(0.1, spec_height * 0.25)
    candidates: list[tuple[float, float]] = []
    for entity in context.entities:
        if entity.dxf.layer.casefold() != "otherobjecttype":
            continue
        value = _text_value(entity)
        if not value or _NUMBER.fullmatch(value) is None:
            continue
        x, y, _ = _text_position(entity)
        if x <= spec_x or abs(y - spec_y) > row_tolerance:
            continue
        candidates.append((x - spec_x, float(value)))
    if not candidates:
        raise PLSplitError("BOM_LENGTH_MISSING", "PL 材料表行没有可绑定的长度。")
    nearest = min(distance for distance, _ in candidates)
    values = {
        value
        for distance, value in candidates
        if abs(distance - nearest) <= 0.000001
    }
    if len(values) != 1:
        raise PLSplitError("BOM_LENGTH_AMBIGUOUS", "PL 材料表行存在多个同等长度候选。")
    result = next(iter(values))
    if result <= 0.0:
        raise PLSplitError("BOM_LENGTH_INVALID", "PL 材料表长度必须大于 0。")
    return result


def extract_metadata(context: PLSourceContext) -> PLMetadata:
    part_number = _unique_part_number(context)
    rows = _spec_rows(context)
    unique_specs = {(thickness, width) for _, thickness, width in rows}
    if len(unique_specs) != 1:
        raise PLSplitError(
            "PL_SPEC_AMBIGUOUS",
            f"单件必须有唯一 PL 厚度和宽度，当前识别到：{sorted(unique_specs)}",
        )
    thickness, width = next(iter(unique_specs))
    lengths = {_row_bom_length(context, entity) for entity, _, _ in rows}
    if len(lengths) != 1:
        raise PLSplitError(
            "BOM_LENGTH_AMBIGUOUS",
            f"单件材料表长度不唯一：{sorted(lengths)}",
        )
    return PLMetadata(
        part_number=part_number,
        thickness_mm=thickness,
        width_mm=width,
        bom_length_mm=next(iter(lengths)),
    )
