from __future__ import annotations


class BHDomainError(ValueError):
    """An expected BH source, interpretation, proof, or manufacturing failure."""

    diagnostic_code = "BH-DOMAIN-ERROR"


class BHCandidateLoweringError(BHDomainError):
    """One complete view-pair hypothesis could not become a physical assembly."""


class BHNoValidHypothesis(BHDomainError):
    """A complete search found no physically admissible BH interpretation."""
