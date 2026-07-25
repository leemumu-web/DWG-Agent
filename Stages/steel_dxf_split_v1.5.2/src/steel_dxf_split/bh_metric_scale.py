from __future__ import annotations

from dataclasses import asdict, dataclass
from statistics import median
from typing import Iterable

from ezdxf.entities import DXFEntity
from ezdxf.math import Matrix44

from .bh_annotations import AnnotationModel
from .bh_extractor import BHBlockInstance
from .bh_geometry import PartBlock, entities_bbox
from .bh_knowledge import BHUniformScalePolicy
from .bh_models import BHMetadata


@dataclass(frozen=True, slots=True)
class MetricScaleEvidence:
    channel: str
    observed: float
    expected: float
    factor: float
    source_ids: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class ViewMetricScaleResolution:
    mode: str
    factor: float
    evidence: tuple[MetricScaleEvidence, ...]
    proposed_factor: float
    maximum_relative_deviation: float
    reason: str

    @property
    def authorized(self) -> bool:
        return self.mode in {"identity", "normalized"}

    def to_dict(self) -> dict[str, object]:
        return {
            "mode": self.mode,
            "factor": self.factor,
            "proposed_factor": self.proposed_factor,
            "maximum_relative_deviation": self.maximum_relative_deviation,
            "reason": self.reason,
            "evidence": [item.to_dict() for item in self.evidence],
        }


def _block_source_ids(block: PartBlock) -> tuple[str, ...]:
    if block.source_view is not None:
        return block.source_view.source_ids
    return block.entity_source_ids


def _relative_deviation(values: Iterable[float], center: float) -> float:
    return max(
        (abs(value / center - 1.0) for value in values),
        default=0.0,
    )


def _view_evidence(
    block: PartBlock,
    *,
    longitudinal: float,
    transverse: float,
    role: str,
) -> tuple[MetricScaleEvidence, MetricScaleEvidence]:
    direct = (
        longitudinal / max(block.bbox.width, 1e-12),
        transverse / max(block.bbox.height, 1e-12),
        block.bbox.width,
        block.bbox.height,
    )
    rotated = (
        longitudinal / max(block.bbox.height, 1e-12),
        transverse / max(block.bbox.width, 1e-12),
        block.bbox.height,
        block.bbox.width,
    )
    selected = min(
        (direct, rotated),
        key=lambda item: _relative_deviation(item[:2], median(item[:2])),
    )
    long_factor, transverse_factor, observed_long, observed_transverse = selected
    source_ids = _block_source_ids(block)
    return (
        MetricScaleEvidence(
            channel=f"{role}_longitudinal",
            observed=observed_long,
            expected=longitudinal,
            factor=long_factor,
            source_ids=source_ids,
        ),
        MetricScaleEvidence(
            channel=f"{role}_transverse",
            observed=observed_transverse,
            expected=transverse,
            factor=transverse_factor,
            source_ids=source_ids,
        ),
    )


def _circle_diameters(
    instances: list[BHBlockInstance],
) -> dict[str, float]:
    result: dict[str, float] = {}
    for instance in instances:
        for index, entity in enumerate(instance.entities):
            if (
                entity.dxftype() != "CIRCLE"
                or str(getattr(entity.dxf, "layer", "")).casefold() != "bolt"
                or index >= len(instance.entity_source_ids)
            ):
                continue
            result[instance.entity_source_ids[index]] = (
                2.0 * float(entity.dxf.radius)
            )
    return result


def _bolt_evidence(
    main: PartBlock,
    flange: PartBlock,
    annotations: AnnotationModel,
    instances: list[BHBlockInstance],
) -> tuple[MetricScaleEvidence, ...]:
    selected_regions = {
        region
        for region in (main.region_id, flange.region_id)
        if region is not None
    }
    if not selected_regions:
        return ()
    diameters = _circle_diameters(instances)
    evidence: list[MetricScaleEvidence] = []
    for mark in annotations.bolt_marks:
        if (
            not selected_regions.intersection(mark.target_region_ids)
        ):
            continue
        for source_id in mark.target_source_ids:
            observed = diameters.get(source_id)
            if observed is None or observed <= 1e-12:
                continue
            evidence.append(
                MetricScaleEvidence(
                    channel="bolt_diameter",
                    observed=observed,
                    expected=mark.diameter,
                    factor=mark.diameter / observed,
                    source_ids=tuple(
                        sorted({source_id, *mark.source_ids})
                    ),
                )
            )
    return tuple(evidence)


def resolve_view_metric_scale(
    main: PartBlock,
    flange: PartBlock,
    metadata: BHMetadata,
    annotations: AnnotationModel,
    instances: list[BHBlockInstance],
    policy: BHUniformScalePolicy,
) -> ViewMetricScaleResolution:
    geometry = (
        *_view_evidence(
            main,
            longitudinal=metadata.nominal_length,
            transverse=metadata.profile.max_height,
            role="main",
        ),
        *_view_evidence(
            flange,
            longitudinal=metadata.nominal_length,
            transverse=metadata.profile.flange_width,
            role="flange",
        ),
    )
    geometry_factors = tuple(item.factor for item in geometry)
    proposed = median(geometry_factors)
    geometry_deviation = _relative_deviation(geometry_factors, proposed)
    # A cranked or stepped web can legitimately exceed nominal H.  Preserve
    # the established 1:1 route from the stable axes; web height remains a
    # mandatory consensus channel only when non-identity recovery is proposed.
    identity_factors = tuple(
        item.factor
        for item in geometry
        if item.channel
        in {
            "main_longitudinal",
            "flange_longitudinal",
            "flange_transverse",
        }
    )
    identity_deviation = max(
        (abs(factor - 1.0) for factor in identity_factors),
        default=0.0,
    )
    if (
        not policy.enabled
        or identity_deviation <= policy.activation_relative_delta
    ):
        return ViewMetricScaleResolution(
            mode="identity",
            factor=1.0,
            evidence=geometry,
            proposed_factor=proposed,
            maximum_relative_deviation=geometry_deviation,
            reason=(
                "policy_disabled"
                if not policy.enabled
                else "stable_identity_axes_within_activation_threshold"
            ),
        )

    bolts = _bolt_evidence(main, flange, annotations, instances)
    evidence = (*geometry, *bolts)
    consensus = (
        median(tuple(item.factor for item in bolts))
        if bolts
        else proposed
    )
    all_factors = tuple(item.factor for item in evidence)
    all_deviation = _relative_deviation(all_factors, consensus)
    within_bounds = (
        policy.minimum_factor <= consensus <= policy.maximum_factor
    )
    authorized = bool(
        bolts
        and within_bounds
        and geometry_deviation <= policy.consensus_relative_tolerance
        and all_deviation <= policy.consensus_relative_tolerance
    )
    if not authorized:
        if not bolts:
            reason = "missing_bound_bolt_diameter_evidence"
        elif not within_bounds:
            reason = "proposed_factor_out_of_bounds"
        else:
            reason = "uniform_scale_evidence_conflict"
        return ViewMetricScaleResolution(
            mode="blocked",
            factor=1.0,
            evidence=evidence,
            proposed_factor=consensus,
            maximum_relative_deviation=max(
                geometry_deviation,
                all_deviation,
            ),
            reason=reason,
        )
    return ViewMetricScaleResolution(
        mode="normalized",
        factor=consensus,
        evidence=evidence,
        proposed_factor=consensus,
        maximum_relative_deviation=max(
            geometry_deviation,
            all_deviation,
        ),
        reason="independent_uniform_scale_consensus",
    )


def _scaled_entity(entity: DXFEntity, factor: float) -> DXFEntity:
    clone = entity.copy()
    if abs(factor - 1.0) <= 1e-12:
        return clone
    try:
        clone.transform(Matrix44.scale(factor, factor, 1.0))
    except (AttributeError, NotImplementedError) as error:
        raise ValueError(
            f"Cannot scale lowering entity {entity.dxftype()}."
        ) from error
    return clone


def scale_part_block(block: PartBlock, factor: float) -> PartBlock:
    entities = [_scaled_entity(entity, factor) for entity in block.entities]
    return PartBlock(
        insert=block.insert,
        entities=entities,
        bbox=entities_bbox(entities),
        source_view=block.source_view,
        entity_source_ids=block.entity_source_ids,
    )


def scale_runtime_instances(
    instances: list[BHBlockInstance],
    factor: float,
) -> list[BHBlockInstance]:
    return [
        BHBlockInstance(
            insert=instance.insert,
            entities=[
                _scaled_entity(entity, factor)
                for entity in instance.entities
            ],
            layer_counts=instance.layer_counts.copy(),
            texts=list(instance.texts),
            entity_source_ids=instance.entity_source_ids,
        )
        for instance in instances
    ]
