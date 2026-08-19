from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from .bh_dialect import BHDialectProfile, DEFAULT_TEKLA_DIALECT


@dataclass(frozen=True, slots=True)
class BHSourceContract:
    """Authoritative workflow provenance for accepted BH source drawings.

    This is an input contract supplied by the production workflow, not a
    classifier result inferred from drawing contents.  Individual DXF facts
    still require normal geometric and semantic proof before manufacturing
    output can be authorized.
    """

    source_system: str = "tekla_structures"
    drawing_kind: str = "single_part_drawing"
    member_family: str = "welded_bh"
    export_profile: str = "project_tekla_bh_dxf_v1"

    def validate(self, dialect: BHDialectProfile) -> None:
        """Fail closed when a caller leaves the verified source domain.

        The workflow supplies this provenance; the compiler deliberately does
        not try to infer Tekla authorship from layer names or geometry.  The
        check therefore validates caller authority and the configured dialect
        binding before any DXF fact is parsed.
        """

        expected = {
            "source_system": "tekla_structures",
            "drawing_kind": "single_part_drawing",
            "member_family": "welded_bh",
        }
        violations = [
            f"{name}={getattr(self, name)!r}, expected {value!r}"
            for name, value in expected.items()
            if getattr(self, name) != value
        ]
        if not self.export_profile.strip():
            violations.append("export_profile must be non-empty")
        elif self.export_profile != dialect.profile_id:
            violations.append(
                f"export_profile={self.export_profile!r}, but dialect.profile_id="
                f"{dialect.profile_id!r}"
            )
        if violations:
            raise ValueError(
                "BH source contract violation: " + "; ".join(violations)
            )


DEFAULT_TEKLA_BH_SOURCE_CONTRACT = BHSourceContract()


@dataclass(frozen=True, slots=True)
class BHScoreWeights:
    """Weights used by the complete-component hypothesis solver.

    All terms are dimensionless costs.  Keeping these values in a data object
    makes the engineering policy inspectable and replaceable; the solver does
    not hide business knowledge in a sequence of magic constants.
    """

    view_prior: float = 2.0
    annotation_consistency: float = 0.75
    annotation_coverage: float = 0.20
    projection_fit: float = 1.25
    repair_complexity: float = 0.80
    longitudinal_plausibility: float = 0.45
    evidence_traceability: float = 0.30


@dataclass(frozen=True, slots=True)
class BHFlangeDevelopmentPolicy:
    """Approved fabrication-length policy for one verified Tekla profile.

    The policy cannot authorize geometry by itself.  It only defines how a
    source-backed development observation is converted to a manufacturing
    length after the geometry and candidate-uniqueness checks have passed.
    """

    enabled: bool = True
    profile_id: str = "project_tekla_bh_dxf_v1"
    derived_length_quantum_mm: float = 1.0
    derived_length_rounding: str = "ceil"
    preserve_direct_projection: bool = True
    require_unique_cranked_candidate: bool = True

    def authorizes_profile(self, profile_id: str) -> bool:
        return self.enabled and self.profile_id == profile_id


@dataclass(frozen=True, slots=True)
class BHUniformScalePolicy:
    """Evidence policy for candidate-local non-1:1 view normalization."""

    enabled: bool = True
    activation_relative_delta: float = 0.05
    consensus_relative_tolerance: float = 0.01
    minimum_factor: float = 0.25
    maximum_factor: float = 4.0


@dataclass(frozen=True, slots=True)
class BHKnowledgeBase:
    """Explicit engineering contract for welded BH-member plate splitting.

    The contract describes physical composition, admissible manufacturing
    geometry, semantic source roles and automation thresholds.  It is kept
    independent from the DXF parser and geometry implementation so future
    profile families can reuse the compiler infrastructure with a different
    knowledge base.
    """

    ontology_version: str = "BH-MFG-3.1"

    # The production workflow guarantees this source family.  It narrows the
    # grammar but does not bypass per-feature manufacturing proof obligations.
    source_contract: BHSourceContract = DEFAULT_TEKLA_BH_SOURCE_CONTRACT

    # The exporter dialect supplies evidence hints, not manufacturing truth.
    dialect: BHDialectProfile = DEFAULT_TEKLA_DIALECT

    # Physical ontology.
    physical_web_count: int = 1
    physical_flange_count: int = 2
    permitted_flange_geometry_count: tuple[int, ...] = (1, 2)

    # Drawing semantics.
    visible_part_layers: tuple[str, ...] = ("Part",)
    physical_cut_layers: tuple[str, ...] = ("Bolt",)
    physical_circle_type: str = "CIRCLE"
    helper_entity_types: tuple[str, ...] = ("LINE", "XLINE", "RAY")
    hidden_linetype: str = "XKITLINE04"
    horizontal_axis_fact: bool = True
    horizontal_axis_tolerance_degrees: float = 2.0

    # Numeric contracts.
    endpoint_snap_mm: float = 0.01
    manufacturing_tolerance_mm: float = 0.15
    source_arc_fit_mm: float = 0.10
    minimum_plate_area_mm2: float = 1.0
    flange_development_policy: BHFlangeDevelopmentPolicy = (
        BHFlangeDevelopmentPolicy()
    )
    uniform_scale_policy: BHUniformScalePolicy = BHUniformScalePolicy()

    # Search policy.
    max_solver_expansions: int = 256
    max_solver_seconds: float | None = None
    minimum_hypothesis_margin: float = 0.02
    # Broad exclusion envelope used only to detect unresolved geometry that
    # could be another member projection.  Crossing it can only downgrade to
    # review; it never authorizes a candidate.
    candidate_universe_residual_limit: float = 0.50

    # Critical projection-correspondence proof limits.  These residuals are
    # dimensionless fractions of the expected member/profile dimensions.
    automatic_main_projection_residual: float = 0.01
    automatic_flange_projection_residual: float = 0.01
    automatic_pair_projection_residual: float = 0.02

    # Legacy confidence telemetry.  These no longer authorize production;
    # explicit proof obligations own the automation disposition.
    minimum_auto_confidence: float = 0.60
    review_confidence: float = 0.80
    score_weights: BHScoreWeights = field(default_factory=BHScoreWeights)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


DEFAULT_BH_KNOWLEDGE = BHKnowledgeBase()
