from __future__ import annotations

import re
from dataclasses import dataclass

from .dxf_io import normalize_text
from .source_ir import SourceDocumentIR, SourceEntityIR


class MetadataResolutionError(ValueError):
    """BOX metadata is missing, conflicting, or physically impossible."""


@dataclass(frozen=True, slots=True)
class BoxProfile:
    height: float
    width: float
    web_thickness: float
    flange_thickness: float

    @property
    def web_clear_width(self) -> float:
        return self.height - 2.0 * self.flange_thickness

    @property
    def flange_clear_width(self) -> float:
        return self.width - 2.0 * self.web_thickness

    @property
    def canonical(self) -> str:
        values = (
            self.height,
            self.width,
            self.web_thickness,
            self.flange_thickness,
        )
        return "BOX" + "*".join(_format_dimension(value) for value in values)


@dataclass(frozen=True, slots=True)
class MetadataField[T]:
    value: T
    source_id: str
    raw_text: str
    normalized_text: str


@dataclass(frozen=True, slots=True)
class BoxMetadata:
    member_mark: MetadataField[str]
    profile: MetadataField[BoxProfile]
    nominal_length: MetadataField[float]
    material: MetadataField[str]
    scale_denominator: MetadataField[int]
    title_group_id: str

    @property
    def fields(self) -> tuple[MetadataField[object], ...]:
        return (
            self.member_mark,
            self.profile,
            self.nominal_length,
            self.material,
            self.scale_denominator,
        )


_NUMBER = r"(?:\d+(?:\.\d*)?|\.\d+)"
_PROFILE_RE = re.compile(
    rf"\bBOX\s*({_NUMBER})\s*[*X×]\s*({_NUMBER})"
    rf"\s*[*X×]\s*({_NUMBER})\s*[*X×]\s*({_NUMBER})\b",
    re.IGNORECASE,
)
_MATERIAL_RE = re.compile(r"^Q\d{3,}[A-Z0-9]*(?:-[A-Z0-9]+)*$", re.IGNORECASE)
_MEMBER_RE = re.compile(
    r"^(?=[A-Z0-9-]*[A-Z])(?:[A-Z0-9]+-){2,}[A-Z0-9]+$",
    re.IGNORECASE,
)
_SCALE_RE = re.compile(r"^1\s*:\s*(\d+)$")
_PLAIN_NUMBER_RE = re.compile(rf"^({_NUMBER})$")
_LENGTH_HEADER_RE = re.compile(
    r"^长度(?:\s*[（(]\s*MM\s*[)）])?$",
    re.IGNORECASE,
)
_QUANTITY_HEADER_RE = re.compile(r"^数量$")


def _format_dimension(value: float) -> str:
    if value.is_integer():
        return str(int(value))
    return f"{value:.12g}"


def parse_box_profile(value: str) -> BoxProfile:
    """Parse and validate one canonical Tekla BOX profile string."""

    normalized = normalize_text(value)
    match = _PROFILE_RE.fullmatch(normalized)
    if match is None:
        raise MetadataResolutionError(f"not a BOX H*B*tw*tf profile: {value!r}")
    height, width, web_thickness, flange_thickness = (
        float(part) for part in match.groups()
    )
    if min(height, width, web_thickness, flange_thickness) <= 0:
        raise MetadataResolutionError("BOX dimensions and thicknesses must be positive")
    if 2.0 * flange_thickness >= height:
        raise MetadataResolutionError(
            "BOX flange thickness leaves no positive web clear width"
        )
    if 2.0 * web_thickness >= width:
        raise MetadataResolutionError(
            "BOX web thickness leaves no positive flange clear width"
        )
    return BoxProfile(
        height=height,
        width=width,
        web_thickness=web_thickness,
        flange_thickness=flange_thickness,
    )


def _text_entities(source: SourceDocumentIR) -> tuple[SourceEntityIR, ...]:
    return tuple(
        entity
        for entity in source.entities
        if entity.text_decoded is not None and normalize_text(entity.text_decoded)
    )


def _field[T](entity: SourceEntityIR, value: T) -> MetadataField[T]:
    assert entity.text_decoded is not None
    return MetadataField(
        value=value,
        source_id=entity.source_id,
        raw_text=entity.text_raw or "",
        normalized_text=normalize_text(entity.text_decoded),
    )


def _unique_match(
    entities: tuple[SourceEntityIR, ...],
    *,
    label: str,
    predicate: object,
) -> SourceEntityIR:
    check = predicate
    assert callable(check)
    matches = tuple(
        entity
        for entity in entities
        if entity.text_decoded is not None
        and check(normalize_text(entity.text_decoded))
    )
    if len(matches) != 1:
        raise MetadataResolutionError(
            f"expected exactly one {label} in BOX title group, found {len(matches)}"
        )
    return matches[0]


def _resolve_drawing_scale(
    title_entities: tuple[SourceEntityIR, ...],
    all_text_entities: tuple[SourceEntityIR, ...],
) -> SourceEntityIR:
    """Prefer the component table scale, otherwise use one sheet-wide value."""

    def matches(entities: tuple[SourceEntityIR, ...]) -> tuple[SourceEntityIR, ...]:
        return tuple(
            entity
            for entity in entities
            if entity.text_decoded is not None
            and _SCALE_RE.fullmatch(normalize_text(entity.text_decoded)) is not None
        )

    candidates = matches(title_entities)
    scope = "BOX title group"
    if not candidates:
        candidates = matches(all_text_entities)
        scope = "drawing"
    if not candidates:
        raise MetadataResolutionError("no drawing scale found")
    normalized = {
        normalize_text(entity.text_decoded or "") for entity in candidates
    }
    if len(normalized) != 1:
        raise MetadataResolutionError(
            f"conflicting drawing scales in {scope}: "
            + ", ".join(sorted(normalized))
        )
    return min(candidates, key=lambda entity: entity.source_id)


def _resolve_nominal_length(
    title_entities: tuple[SourceEntityIR, ...],
) -> SourceEntityIR:
    candidates = tuple(
        entity
        for entity in title_entities
        if entity.text_decoded is not None
        and _PLAIN_NUMBER_RE.fullmatch(normalize_text(entity.text_decoded))
        is not None
    )
    if len(candidates) == 1:
        return candidates[0]

    length_headers = tuple(
        entity
        for entity in title_entities
        if entity.text_decoded is not None
        and _LENGTH_HEADER_RE.fullmatch(normalize_text(entity.text_decoded))
        is not None
        and entity.center is not None
    )
    quantity_headers = tuple(
        entity
        for entity in title_entities
        if entity.text_decoded is not None
        and _QUANTITY_HEADER_RE.fullmatch(normalize_text(entity.text_decoded))
        is not None
        and entity.center is not None
    )
    if len(length_headers) == 1 and len(quantity_headers) == 1:
        length_x = length_headers[0].center[0]
        quantity_x = quantity_headers[0].center[0]
        column_matches = tuple(
            entity
            for entity in candidates
            if entity.center is not None
            and abs(entity.center[0] - length_x)
            < abs(entity.center[0] - quantity_x)
        )
        if len(column_matches) == 1:
            return column_matches[0]

    raise MetadataResolutionError(
        "expected exactly one nominal length in BOX title group, "
        f"found {len(candidates)}"
    )


def resolve_box_metadata(source: SourceDocumentIR) -> BoxMetadata:
    """Resolve one complete BOX title record with per-field source evidence."""

    text_entities = _text_entities(source)
    profile_candidates: list[tuple[SourceEntityIR, BoxProfile]] = []
    for entity in text_entities:
        assert entity.text_decoded is not None
        normalized = normalize_text(entity.text_decoded)
        if not normalized.upper().startswith("BOX"):
            continue
        try:
            profile_candidates.append((entity, parse_box_profile(normalized)))
        except MetadataResolutionError:
            continue
    if not profile_candidates:
        raise MetadataResolutionError("no valid BOX profile found")

    canonical_profiles = {profile.canonical for _, profile in profile_candidates}
    if len(canonical_profiles) != 1:
        raise MetadataResolutionError(
            "conflicting BOX profiles: " + ", ".join(sorted(canonical_profiles))
        )
    title_group_ids = {entity.group_id for entity, _ in profile_candidates}
    if len(title_group_ids) != 1:
        raise MetadataResolutionError(
            "equivalent BOX profiles occur in multiple title groups"
        )
    title_group_id = next(iter(title_group_ids))
    title_entities = tuple(
        entity for entity in text_entities if entity.group_id == title_group_id
    )
    profile_entity, profile = profile_candidates[0]

    member_entity = _unique_match(
        title_entities,
        label="member mark",
        predicate=lambda text: _MEMBER_RE.fullmatch(text) is not None,
    )
    material_entity = _unique_match(
        title_entities,
        label="material",
        predicate=lambda text: _MATERIAL_RE.fullmatch(text) is not None,
    )
    scale_entity = _resolve_drawing_scale(title_entities, text_entities)
    length_entity = _resolve_nominal_length(title_entities)

    assert member_entity.text_decoded is not None
    assert material_entity.text_decoded is not None
    assert scale_entity.text_decoded is not None
    assert length_entity.text_decoded is not None
    scale_match = _SCALE_RE.fullmatch(normalize_text(scale_entity.text_decoded))
    length_match = _PLAIN_NUMBER_RE.fullmatch(
        normalize_text(length_entity.text_decoded)
    )
    assert scale_match is not None
    assert length_match is not None

    return BoxMetadata(
        member_mark=_field(member_entity, normalize_text(member_entity.text_decoded)),
        profile=_field(profile_entity, profile),
        nominal_length=_field(length_entity, float(length_match.group(1))),
        material=_field(material_entity, normalize_text(material_entity.text_decoded)),
        scale_denominator=_field(scale_entity, int(scale_match.group(1))),
        title_group_id=title_group_id,
    )
