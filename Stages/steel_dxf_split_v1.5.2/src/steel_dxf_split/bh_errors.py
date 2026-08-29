from __future__ import annotations


class BHDomainError(ValueError):
    """An expected BH source, interpretation, proof, or manufacturing failure."""

    diagnostic_code = "BH-DOMAIN-ERROR"


class BHCandidateLoweringError(BHDomainError):
    """One complete view-pair hypothesis could not become a physical assembly."""


class BHNoValidHypothesis(BHDomainError):
    """A complete search found no physically admissible BH interpretation."""


class BHInsufficientViewError(BHDomainError):
    """A BH drawing does not contain the two Part projection views required
    for plate reconstruction.

    A complete BH single-part drawing carries both an elevation view and a
    flange-plane view.  When only one Part projection block is present (for
    example a Tekla export that omitted the top view), no manufacturing
    assembly can be lowered.  This is an expected source incompleteness, not a
    geometry defect, so it is routed to an auditable rejection instead of
    failing the job.
    """

    diagnostic_code = "BH-INSUFFICIENT-PART-VIEWS"
