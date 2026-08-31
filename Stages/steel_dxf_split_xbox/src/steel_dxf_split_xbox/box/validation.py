from __future__ import annotations

from collections.abc import Mapping

from .manufacturing_ir import BoxManufacturingIR
from .validator import validate_manufacturing_ir


def _failed_checks(report: Mapping[str, object]) -> tuple[str, ...]:
    checks = report.get("checks")
    if not isinstance(checks, Mapping):
        return ()
    return tuple(sorted(str(name) for name, passed in checks.items() if passed is not True))


def run_validation(manufacturing: BoxManufacturingIR) -> dict[str, object]:
    """Enforce Project 2's manufacturing contract and fail closed."""

    report = validate_manufacturing_ir(manufacturing)
    if report.get("ok") is not True:
        failed = _failed_checks(report)
        detail = ", ".join(failed) if failed else "unknown validation failure"
        raise ValueError(f"BOX manufacturing IR validation failed: {detail}")
    return report
