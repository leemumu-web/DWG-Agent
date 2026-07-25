from __future__ import annotations

from dataclasses import asdict, dataclass, field, fields, is_dataclass
from enum import Enum
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping, Protocol

from . import __version__


@dataclass(frozen=True, slots=True)
class Evidence:
    code: str
    description: str
    weight: float
    value: Any = None
    source_ids: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class DecisionRecord:
    name: str
    selected: str
    score: float
    confidence: float
    margin: float
    alternatives: list[dict[str, Any]] = field(default_factory=list)
    evidence: list[Evidence] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "selected": self.selected,
            "score": self.score,
            "confidence": self.confidence,
            "margin": self.margin,
            "alternatives": self.alternatives,
            "evidence": [item.to_dict() for item in self.evidence],
            "warnings": list(self.warnings),
        }


@dataclass(slots=True)
class StageRecord:
    name: str
    duration_ms: float
    inputs: dict[str, Any] = field(default_factory=dict)
    outputs: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class BHCompilerTrace:
    version: str = __version__
    stages: list[StageRecord] = field(default_factory=list)
    decisions: list[DecisionRecord] = field(default_factory=list)
    invariants: dict[str, bool] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    def add_stage(self, stage: StageRecord) -> None:
        self.stages.append(stage)

    def add_decision(self, decision: DecisionRecord) -> None:
        self.decisions.append(decision)

    @property
    def minimum_confidence(self) -> float:
        return min(
            (decision.confidence for decision in self.decisions),
            default=1.0,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "minimum_confidence": self.minimum_confidence,
            "stages": [stage.to_dict() for stage in self.stages],
            "decisions": [decision.to_dict() for decision in self.decisions],
            "invariants": dict(self.invariants),
            "warnings": list(self.warnings),
        }


@dataclass(frozen=True, slots=True)
class StageDefinition:
    stage_id: str
    title_zh: str
    description_zh: str


STAGE_REGISTRY = (
    StageDefinition("00_input_provenance", "输入与来源", "记录源图、人工图、哈希和 DXF 审计。"),
    StageDefinition("01_frontend_fact_ir", "事实前端", "保留块、实体、变换、来源和语义分类。"),
    StageDefinition("02_annotation_facts", "标注事实", "提取尺寸、孔标、零件标和剖面。"),
    StageDefinition("03_metadata_semantics", "构件元数据", "解析材料表空间行、BH 截面和名义长度。"),
    StageDefinition("04_view_hypothesis_frontier", "视图假设前沿", "枚举并多样化保留有序视图对。"),
    StageDefinition("05_candidate_lowering", "候选制造降低", "逐候选恢复腹板、翼缘、孔、开口和圆弧。"),
    StageDefinition("06_constraints_and_selection", "约束与选择", "评估硬规则、软代价、排名和失败原因。"),
    StageDefinition("07_assembly_validation", "装配体验证", "验证一腹板、两物理翼缘及板件几何不变量。"),
    StageDefinition("08_manufacturing_ir", "制造 IR 与证明", "冻结逐特征来源、证明闭包、信息账本和指纹。"),
    StageDefinition("09_quality_route", "自动化路由", "仅依关键证明决定生产、复核或拒绝。"),
    StageDefinition("10_codegen_layout", "代码生成与布局", "将已授权制造 IR 布局并写出清洁 DXF。"),
    StageDefinition("11_saved_output_validation", "保存后验证", "重新读取输出并审计实体与制造闭包。"),
    StageDefinition("12_manual_supervision", "人工监督核验", "处置冻结后离线比较自动板件和人工拆板。"),
    StageDefinition("13_corpus_summary", "语料总览", "汇总 20 组阶段完整性、能力和误差。"),
)
STAGE_IDS = frozenset(item.stage_id for item in STAGE_REGISTRY)
TRACE_STATUSES = frozenset(
    {"observed", "selected", "rejected", "failed", "not_applicable"}
)


def _freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({str(key): _freeze(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(item) for item in value)
    if isinstance(value, (set, frozenset)):
        return frozenset(_freeze(item) for item in value)
    return value


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Enum):
        return _json_safe(value.value)
    if isinstance(value, Path):
        return value.as_posix()
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if isinstance(value, (set, frozenset)):
        return [_json_safe(item) for item in sorted(value, key=repr)]
    if is_dataclass(value) and not isinstance(value, type):
        return {item.name: _json_safe(getattr(value, item.name)) for item in fields(value)}
    to_dict = getattr(value, "to_dict", None)
    if callable(to_dict):
        return _json_safe(to_dict())
    raise TypeError(f"Trace value is not JSON serializable: {type(value).__name__}")


@dataclass(frozen=True, slots=True)
class TraceShape:
    shape_id: str
    kind: str
    role: str
    coordinates: tuple[tuple[float, float], ...] = ()
    closed: bool = False
    bulges: tuple[float, ...] = ()
    source_ids: tuple[str, ...] = ()
    properties: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "coordinates", tuple(tuple(point) for point in self.coordinates))
        object.__setattr__(self, "bulges", tuple(self.bulges))
        object.__setattr__(self, "source_ids", tuple(self.source_ids))
        object.__setattr__(self, "properties", _freeze(self.properties))

    def to_dict(self) -> dict[str, Any]:
        return {
            "shape_id": self.shape_id,
            "kind": self.kind,
            "role": self.role,
            "coordinates": _json_safe(self.coordinates),
            "closed": self.closed,
            "bulges": _json_safe(self.bulges),
            "source_ids": list(self.source_ids),
            "properties": _json_safe(self.properties),
        }


@dataclass(frozen=True, slots=True)
class TraceEvent:
    sequence: int
    sample_id: str
    stage_id: str
    artifact_id: str
    status: str
    title_zh: str
    summary_zh: str
    hypothesis_id: str | None
    shapes: tuple[TraceShape, ...]
    payload: Mapping[str, Any]

    def __post_init__(self) -> None:
        object.__setattr__(self, "shapes", tuple(self.shapes))
        object.__setattr__(self, "payload", _freeze(self.payload))

    def to_dict(self) -> dict[str, Any]:
        return {
            "sequence": self.sequence,
            "sample_id": self.sample_id,
            "stage_id": self.stage_id,
            "artifact_id": self.artifact_id,
            "status": self.status,
            "title_zh": self.title_zh,
            "summary_zh": self.summary_zh,
            "hypothesis_id": self.hypothesis_id,
            "shapes": [item.to_dict() for item in self.shapes],
            "payload": _json_safe(self.payload),
        }


class TraceObserver(Protocol):
    def emit(self, **kwargs: Any) -> TraceEvent: ...


class InMemoryTraceObserver:
    def __init__(self, sample_id: str):
        if not sample_id:
            raise ValueError("Trace sample_id must not be empty")
        self.sample_id = sample_id
        self.events: list[TraceEvent] = []

    def emit(
        self,
        *,
        stage_id: str,
        artifact_id: str,
        status: str,
        title_zh: str,
        summary_zh: str,
        payload: Mapping[str, Any],
        shapes: tuple[TraceShape, ...] = (),
        hypothesis_id: str | None = None,
    ) -> TraceEvent:
        if stage_id not in STAGE_IDS:
            raise ValueError(f"Unknown trace stage: {stage_id}")
        if status not in TRACE_STATUSES:
            raise ValueError(f"Unknown trace status: {status}")
        if not artifact_id:
            raise ValueError("Trace artifact_id must not be empty")
        event = TraceEvent(
            sequence=len(self.events) + 1,
            sample_id=self.sample_id,
            stage_id=stage_id,
            artifact_id=artifact_id,
            status=status,
            title_zh=title_zh,
            summary_zh=summary_zh,
            hypothesis_id=hypothesis_id,
            shapes=shapes,
            payload=payload,
        )
        self.events.append(event)
        return event

    def to_dict(self) -> dict[str, Any]:
        return {
            "sample_id": self.sample_id,
            "events": [item.to_dict() for item in self.events],
        }


def emit_trace(observer: TraceObserver | None, **kwargs: Any) -> TraceEvent | None:
    return None if observer is None else observer.emit(**kwargs)
