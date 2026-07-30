from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, Literal, Mapping

from .analyzer import BHAnalyzer
from .dxf_ascii import read_ascii_dxf
from .dxf_ezdxf import read_ezdxf
from .model import DrawingData, DrawingResult, PlateMeasurement


@dataclass(frozen=True, slots=True)
class BhInputEntry:
    path: Path
    file_name: str


@dataclass(frozen=True, slots=True)
class BhProgress:
    processed: int
    total: int
    file_name: str
    status: str


@dataclass(frozen=True, slots=True)
class BhMeasurement:
    role: str
    left_raw: float
    right_raw: float
    left_safe: int
    right_safe: int
    confidence: float
    evidence: str


@dataclass(frozen=True, slots=True)
class BhBatchItem:
    file_name: str
    part_number: str
    specification: str
    status: str
    confidence: float
    measurements: tuple[BhMeasurement, ...]
    warnings: tuple[str, ...]
    diagnostic_row: tuple[object, ...]


@dataclass(frozen=True, slots=True)
class BhBatchOutcome:
    items: tuple[BhBatchItem, ...]

    @property
    def processed_count(self) -> int:
        return len(self.items)

    @property
    def ok_count(self) -> int:
        return sum(item.status == "OK" for item in self.items)

    @property
    def failure_count(self) -> int:
        return self.processed_count - self.ok_count

    @property
    def measurement_count(self) -> int:
        return sum(len(item.measurements) for item in self.items)

    def iter_result_rows(
        self,
        visualization_map: Mapping[str, str] | None = None,
    ) -> Iterable[list[object]]:
        visuals = visualization_map or {}
        for item in self.items:
            warning_text = " | ".join(item.warnings)
            if item.measurements:
                for measurement in item.measurements:
                    yield [
                        item.file_name,
                        item.part_number + measurement.role,
                        item.specification,
                        measurement.left_safe,
                        measurement.right_safe,
                        round(measurement.left_raw, 3),
                        round(measurement.right_raw, 3),
                        item.status,
                        round(measurement.confidence, 3),
                        measurement.evidence + (
                            " | " + warning_text if warning_text else ""
                        ),
                        visuals.get(item.file_name, ""),
                    ]
                continue
            yield [
                item.file_name,
                item.part_number + "（未输出）",
                item.specification,
                None,
                None,
                None,
                None,
                item.status,
                round(item.confidence, 3),
                warning_text,
                visuals.get(item.file_name, ""),
            ]

    def iter_diagnostic_rows(
        self,
        visualization_map: Mapping[str, str] | None = None,
    ) -> Iterable[list[object]]:
        visuals = visualization_map or {}
        for item in self.items:
            yield [*item.diagnostic_row, visuals.get(item.file_name, "")]


def _read(path: Path, backend: str) -> DrawingData:
    if backend == "ascii":
        return read_ascii_dxf(path)
    if backend == "ezdxf":
        return read_ezdxf(path)
    try:
        return read_ezdxf(path)
    except Exception as exc:
        drawing = read_ascii_dxf(path)
        drawing.audit_messages.insert(
            0,
            f"ezdxf backend unavailable, used ASCII fallback: {exc}",
        )
        return drawing


def _measurement(value: PlateMeasurement) -> BhMeasurement:
    return BhMeasurement(
        role=value.role,
        left_raw=value.left_raw,
        right_raw=value.right_raw,
        left_safe=value.left_safe,
        right_safe=value.right_safe,
        confidence=value.confidence,
        evidence=value.evidence,
    )


def _compact_result(result: DrawingResult, *, file_name: str) -> BhBatchItem:
    return BhBatchItem(
        file_name=file_name,
        part_number=result.part_number,
        specification=result.specification,
        status=result.status,
        confidence=result.confidence,
        measurements=tuple(_measurement(value) for value in result.measurements),
        warnings=tuple(result.warnings),
        diagnostic_row=_diagnostic_row(result, file_name=file_name),
    )


def _rounded_diagnostic(
    values: Mapping[str, object],
    key: str,
    digits: int,
) -> float | None:
    if not values:
        return None
    return round(float(values.get(key, 0.0)), digits)


def _diagnostic_row(
    result: DrawingResult,
    *,
    file_name: str,
) -> tuple[object, ...]:
    front = result.diagnostics.get("front_view") or {}
    units = result.diagnostics.get("units") or {}
    plates = result.diagnostics.get("plate_identification") or {}
    web = plates.get("web") or {}
    return (
        file_name,
        result.part_number,
        result.status,
        result.diagnostics.get("measurement_rule", ""),
        result.diagnostics.get("output_unit", "mm"),
        units.get("header_insunits_code"),
        units.get("header_insunits_name", ""),
        units.get("title_drawing_scale") or "",
        "否" if units else "",
        _rounded_diagnostic(units, "coordinate_unit_to_mm", 6),
        units.get("status", ""),
        units.get("verification_mode", ""),
        front.get("id", ""),
        _rounded_diagnostic(front, "left_x_dxf", 6),
        _rounded_diagnostic(front, "right_x_dxf", 6),
        _rounded_diagnostic(front, "length_mm", 3),
        _rounded_diagnostic(front, "height_mm", 3),
        plates.get("upper_flange_count"),
        plates.get("lower_flange_count"),
        _rounded_diagnostic(web, "left_offset_mm", 3),
        _rounded_diagnostic(web, "right_offset_mm", 3),
        " | ".join(result.warnings),
    )


def analyze_manifest(
    entries: Iterable[BhInputEntry],
    *,
    backend: Literal["ascii", "ezdxf", "auto"],
    on_progress: Callable[[BhProgress], None],
    on_analyzed: Callable[
        [BhInputEntry, DrawingData | None, DrawingResult],
        None,
    ]
    | None = None,
    analyzer: BHAnalyzer | None = None,
) -> BhBatchOutcome:
    """Analyze an ordered manifest without retaining drawing geometry."""
    manifest = tuple(entries)
    active_analyzer = analyzer or BHAnalyzer()
    items: list[BhBatchItem] = []
    total = len(manifest)
    for processed, entry in enumerate(manifest, start=1):
        drawing: DrawingData | None = None
        try:
            drawing = _read(entry.path, backend)
            result = active_analyzer.analyze(drawing)
        except Exception as exc:
            result = DrawingResult(
                file_name=entry.file_name,
                part_number=Path(entry.file_name).stem,
                specification="",
                status="ERROR_UNHANDLED",
                confidence=0.0,
                measurements=[],
                warnings=[repr(exc)],
            )
        result.file_name = entry.file_name
        if on_analyzed is not None:
            on_analyzed(entry, drawing, result)
        item = _compact_result(result, file_name=entry.file_name)
        items.append(item)
        on_progress(BhProgress(
            processed=processed,
            total=total,
            file_name=entry.file_name,
            status=item.status,
        ))
    return BhBatchOutcome(tuple(items))
