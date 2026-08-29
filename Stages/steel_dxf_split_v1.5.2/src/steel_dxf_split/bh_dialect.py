from __future__ import annotations

from dataclasses import dataclass, replace

from .bh_ir import SemanticLayer, VisibilityClass


def _name(value: str) -> str:
    return value.strip().casefold()


@dataclass(frozen=True, slots=True)
class RoleHint:
    role: SemanticLayer
    confidence: float
    reason: str


@dataclass(frozen=True, slots=True)
class RoleRule:
    role: SemanticLayer
    layers: tuple[str, ...]
    entity_types: tuple[str, ...] = ()

    def matches_layer(self, layer: str) -> bool:
        normalized = _name(layer)
        return normalized in {_name(item) for item in self.layers}

    def accepts_type(self, entity_type: str) -> bool:
        if not self.entity_types:
            return True
        normalized = _name(entity_type)
        return normalized in {_name(item) for item in self.entity_types}


@dataclass(frozen=True, slots=True)
class BHDialectProfile:
    profile_id: str
    rules: tuple[RoleRule, ...]
    # Tekla exports the same hidden projection role with different DXF
    # linetype spellings across DWG/DXF generations.  Keep those source
    # spellings at the dialect boundary; downstream geometry consumes one
    # canonical hidden-edge semantic instead of exporter-specific names.
    hidden_projection_linetypes: tuple[str, ...] = ()
    # Tekla's extension-line origin offset is a paper-space setting.  In the
    # project corpus the default 1 mm offset and 3 mm text height therefore
    # remain in the same 1:3 ratio after model-space scaling.
    dimension_origin_offset_text_ratio: float = 1.0 / 3.0
    dimension_origin_offset_tolerance_mm: float = 1.0

    def is_hidden_projection_linetype(self, linetype: str) -> bool:
        normalized = _name(linetype)
        return normalized in {
            _name(item) for item in self.hidden_projection_linetypes
        }

    def visibility(
        self,
        role: SemanticLayer,
        linetype: str,
    ) -> VisibilityClass:
        """Classify drawing visibility once at the authorized dialect boundary."""

        if role == SemanticLayer.CUT_HELPER:
            return VisibilityClass.CENTER
        if role in {
            SemanticLayer.PART_MARK,
            SemanticLayer.BOLT_MARK,
            SemanticLayer.DIMENSION,
            SemanticLayer.SECTION,
            SemanticLayer.DRAWING_SHEET,
            SemanticLayer.OTHER,
        }:
            return VisibilityClass.ANNOTATION
        if self.is_hidden_projection_linetype(linetype):
            return VisibilityClass.HIDDEN
        if role in {SemanticLayer.PART_EDGE, SemanticLayer.PHYSICAL_CUT}:
            return VisibilityClass.PHYSICAL
        return VisibilityClass.UNKNOWN

    def hint(self, layer: str, entity_type: str, linetype: str) -> RoleHint:
        del linetype  # Reserved for dialects that distinguish roles by line style.
        layer_rules = tuple(rule for rule in self.rules if rule.matches_layer(layer))
        if not layer_rules:
            return RoleHint(SemanticLayer.UNKNOWN, 0.0, "layer_unmapped")
        for rule in layer_rules:
            if rule.accepts_type(entity_type):
                return RoleHint(rule.role, 1.0, "layer_entity_match")
        return RoleHint(SemanticLayer.UNKNOWN, 0.0, "entity_type_incompatible")

    def with_alias(self, role: SemanticLayer, alias: str) -> "BHDialectProfile":
        normalized_alias = alias.strip()
        if not normalized_alias:
            raise ValueError("A dialect layer alias cannot be empty.")
        matched = False
        updated: list[RoleRule] = []
        for rule in self.rules:
            if rule.role != role:
                updated.append(rule)
                continue
            matched = True
            aliases = tuple(dict.fromkeys((*rule.layers, normalized_alias)))
            updated.append(replace(rule, layers=aliases))
        if not matched:
            raise ValueError(f"No dialect rule exists for semantic role {role.value!r}.")
        return replace(self, rules=tuple(updated))


DEFAULT_TEKLA_DIALECT = BHDialectProfile(
    profile_id="project_tekla_bh_dxf_v1",
    hidden_projection_linetypes=("XKITLINE04", "DOT2", "DASHEDX2"),
    rules=(
        RoleRule(
            SemanticLayer.PART_EDGE,
            ("Part",),
            ("LINE", "ARC", "CIRCLE", "LWPOLYLINE", "POLYLINE"),
        ),
        RoleRule(
            SemanticLayer.PHYSICAL_CUT,
            ("Bolt",),
            ("CIRCLE", "ARC", "LWPOLYLINE", "POLYLINE"),
        ),
        RoleRule(
            SemanticLayer.CUT_HELPER,
            ("Bolt",),
            ("LINE", "XLINE", "RAY", "POINT"),
        ),
        RoleRule(SemanticLayer.PART_MARK, ("PartMark",)),
        RoleRule(SemanticLayer.BOLT_MARK, ("BoltMark",)),
        RoleRule(
            SemanticLayer.DIMENSION,
            ("Z-DIMENSIONS", "Z-DIMENSIONS-LINES"),
        ),
        RoleRule(SemanticLayer.SECTION, ("Section",)),
        RoleRule(SemanticLayer.DRAWING_SHEET, ("DrawingSheet",)),
        RoleRule(SemanticLayer.OTHER, ("OtherObjectType",)),
    ),
)


_CANONICAL_TEKLA_LAYER = {
    SemanticLayer.PART_EDGE: "Part",
    SemanticLayer.PHYSICAL_CUT: "Bolt",
    SemanticLayer.CUT_HELPER: "Bolt",
    SemanticLayer.PART_MARK: "PartMark",
    SemanticLayer.BOLT_MARK: "BoltMark",
    SemanticLayer.DIMENSION: "Z-DIMENSIONS",
    SemanticLayer.SECTION: "Section",
    SemanticLayer.DRAWING_SHEET: "DrawingSheet",
    SemanticLayer.OTHER: "OtherObjectType",
}


def canonical_tekla_layer(role: SemanticLayer) -> str | None:
    """Return the current profile's canonical output spelling for a role."""

    return _CANONICAL_TEKLA_LAYER.get(role)


def canonical_tekla_linetype(
    visibility: VisibilityClass,
    source_linetype: str,
) -> str:
    """Lower source-specific visibility spellings to the geometry dialect."""

    if visibility == VisibilityClass.HIDDEN:
        return "XKITLINE04"
    return source_linetype
