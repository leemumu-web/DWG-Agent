from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path

from .analyzer import BoxAnalyzer
from .dxf_ezdxf import read_ezdxf
from .model import DrawingResult, PlateMeasurement


@dataclass(frozen=True, slots=True)
class BoxInputEntry:
    path: Path
    file_name: str


@dataclass(frozen=True, slots=True)
class BoxProgress:
    processed: int
    total: int
    file_name: str
    status: str


@dataclass(frozen=True, slots=True)
class BoxMeasurement:
    role: str
    left_safe: int
    right_safe: int
    left_raw: float
    right_raw: float


@dataclass(frozen=True, slots=True)
class BoxBatchItem:
    file_name: str
    part_number: str
    specification: str
    status: str
    confidence: float
    measurements: tuple[BoxMeasurement, ...]
    warnings: tuple[str, ...]
    diagnostics: dict[str, object]


@dataclass(frozen=True, slots=True)
class BoxBatchOutcome:
    items: tuple[BoxBatchItem, ...]

    @property
    def processed_count(self) -> int:
        return len(self.items)

    @property
    def ok_count(self) -> int:
        return sum(1 for item in self.items if item.status == "OK")

    @property
    def failure_count(self) -> int:
        return sum(1 for item in self.items if item.status != "OK")

    @property
    def measurement_count(self) -> int:
        return sum(len(item.measurements) for item in self.items)

    def iter_result_rows(self) -> Iterable[Sequence[object]]:
        for item in self.items:
            for measurement in item.measurements:
                yield (
                    item.file_name,
                    item.part_number,
                    item.specification,
                    measurement.role,
                    measurement.left_safe,
                    measurement.right_safe,
                    round(measurement.left_raw, 3),
                    round(measurement.right_raw, 3),
                    item.status,
                    round(item.confidence, 3),
                    " | ".join(item.warnings[:6]),
                    "",
                )

    def iter_diagnostic_rows(self) -> Iterable[Sequence[object]]:
        for item in self.items:
            diagnostics = item.diagnostics
            front = diagnostics.get("front_view") or {}
            unit = diagnostics.get("unit") or {}

            status_text = item.status
            warning = "；".join(item.warnings[:8])
            plate_cells: list[object] = []
            for measurement in item.measurements[:4]:
                plate_cells.append(
                    f"{measurement.role}:{measurement.left_safe}/{measurement.right_safe}"
                )
            while len(plate_cells) < 4:
                plate_cells.append("")
            yield (
                item.file_name,
                item.part_number,
                status_text,
                "全局水平X三步走",
                "mm",
                unit.get("header_insunits_code"),
                unit.get("header_insunits_name"),
                "",
                False,
                unit.get("coordinate_unit_to_mm"),
                unit.get("status"),
                unit.get("verification_mode", ""),
                front.get("block"),
                front.get("left_x_dxf"),
                front.get("right_x_dxf"),
                front.get("length_mm"),
                front.get("height_mm"),
                *plate_cells,
                warning,
                "",
            )


def _measurement(value: PlateMeasurement) -> BoxMeasurement:
    return BoxMeasurement(
        role=value.role,
        left_safe=value.left_safe,
        right_safe=value.right_safe,
        left_raw=value.left_raw,
        right_raw=value.right_raw,
    )


def _compact_result(result: DrawingResult, *, file_name: str) -> BoxBatchItem:
    return BoxBatchItem(
        file_name=file_name,
        part_number=result.part_number,
        specification=result.specification,
        status=result.status,
        confidence=result.confidence,
        measurements=tuple(_measurement(value) for value in result.measurements),
        warnings=tuple(result.warnings),
        diagnostics=result.diagnostics,
    )


def analyze_manifest(
    entries: Iterable[BoxInputEntry],
    *,
    on_progress: Callable[[BoxProgress], None] | None = None,
    analyzer: BoxAnalyzer | None = None,
) -> BoxBatchOutcome:
    """Analyze an ordered manifest without retaining drawing geometry."""
    manifest = tuple(entries)
    active_analyzer = analyzer or BoxAnalyzer()
    items: list[BoxBatchItem] = []
    total = len(manifest)
    for processed, entry in enumerate(manifest, start=1):
        result: DrawingResult
        try:
            drawing = read_ezdxf(entry.path)
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
        item = _compact_result(result, file_name=entry.file_name)
        items.append(item)
        if on_progress is not None:
            on_progress(BoxProgress(
                processed=processed,
                total=total,
                file_name=entry.file_name,
                status=item.status,
            ))
    return BoxBatchOutcome(tuple(items))
