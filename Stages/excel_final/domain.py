"""规范化 Excel Final 管线共享的规范记录。

跨模块输入/证据契约（reader → canonical_pipeline → writer_parts →
stage2）。这些 frozen 记录必须保持稳定：任何字段改动都会波及整条管线
与 stage2 交接。

字段约定：

- ``component_qty`` 是源行的构件数，``original_qty`` 是数量列；两者含义
  不同且都随校验贯穿全程。
- ``invalid_fields`` 列出缺失/无效的源列名；非空元组表示该行不完全可信。
  消费方必须把 None 的重量/长度字段视为「源值缺失」，绝不能当作零。
- ``classification`` 是路由后的类别，行未分类前为 None；不要假设它总是
  有值。
- 重量刻意保留未舍入的 Decimal：舍入只发生在 writer/报告边界（见
  weights.py 的容差）。
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from pathlib import Path
from types import MappingProxyType
from typing import Mapping


@dataclass(frozen=True, slots=True)
class SourcePart:
    """一条规范化源零件行（构件行/零件行输入证据）。

    ``component_qty`` 是构件数，``original_qty`` 是数量；``invalid_fields``
    列出缺失的源列（见模块 docstring）。Frozen：零件在管线中传递不改动。
    """

    source_sheet: str
    source_row: int
    source_seq: str | int | None
    batch: str | None
    component_no: str
    component_qty: Decimal
    part_no: str
    original_spec: str
    material: str
    length: Decimal
    original_qty: Decimal
    source_unit_net: Decimal | None
    source_total_net: Decimal | None
    source_unit_gross: Decimal | None
    source_total_gross: Decimal | None
    source_unit_area: Decimal | None
    source_total_area: Decimal | None
    classification: str | None
    invalid_fields: tuple[str, ...] = ()


class ComponentRowKind(StrEnum):
    START = "start"
    SUBTOTAL = "subtotal"
    SUMMARY = "summary"


@dataclass(frozen=True, slots=True)
class ComponentSourceRow:
    """一条规范化构件作用域源行（构件行证据）。

    ``kind`` 区分构件块的 start/subtotal/summary 行；非数据行的数量与
    重量字段为 None。
    """

    source_sheet: str
    source_row: int
    kind: ComponentRowKind
    batch: str | None
    component_no: str
    component_qty: Decimal | None
    original_spec: str | None
    material: str | None
    source_unit_net: Decimal | None
    source_total_net: Decimal | None
    source_unit_gross: Decimal | None
    source_total_gross: Decimal | None
    source_unit_area: Decimal | None
    source_total_area: Decimal | None
    component_length: Decimal | None
    component_width: Decimal | None
    component_height: Decimal | None
    subtotal_source_row: int | None = None


@dataclass(frozen=True, slots=True)
class ParentPartEvidence:
    """拆板前一条父零件的证据链。

    携带规范化规格、密度来源、未舍入的理论重量与重量校验结果；
    ``weight_validation_status`` 是质量等级之一，驱动下游的隔离决定。
    """

    source: SourcePart
    normalized_type: str
    normalized_spec: str
    normalized_width: Decimal | None
    density_value: Decimal | None
    density_source: str
    theoretical_unit_weight_unrounded: Decimal | None
    theoretical_total_weight_unrounded: Decimal | None
    material_utilization: Decimal | None
    weight_validation_status: str
    weight_validation_details: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class SplitPart:
    """父零件的一个拆板子件。

    ``is_main`` 标记主子件；未舍入的理论贡献与其他子件之和必须与父件
    理论重量精确相等（splitter.py 的守恒校验）。
    """

    parent: ParentPartEvidence
    part_type: str
    import_component_no: str
    import_part_no: str
    spec: Decimal
    width: Decimal | None
    quantity: Decimal
    is_main: bool
    theoretical_unit_weight_unrounded: Decimal
    theoretical_contribution_unrounded: Decimal


@dataclass(frozen=True, slots=True)
class PipelineOutcome:
    """一次规范管线的运行结果：输出路径 + 质量汇总。

    ``output_path`` 为解析后的绝对路径；``report_summary`` 不可变
    （MappingProxyType），可跨线程安全读取。
    """

    output_path: Path
    quality_status: str
    warning_count: int
    severe_warning_count: int
    report_summary: Mapping[str, object]

    def __post_init__(self) -> None:
        object.__setattr__(self, "output_path", self.output_path.resolve())
        summary = dict(self.report_summary)
        category_counts = summary.get("category_counts")
        if isinstance(category_counts, Mapping):
            summary["category_counts"] = MappingProxyType(dict(category_counts))
        object.__setattr__(self, "report_summary", MappingProxyType(summary))

    def __fspath__(self) -> str:
        return str(self.output_path)
