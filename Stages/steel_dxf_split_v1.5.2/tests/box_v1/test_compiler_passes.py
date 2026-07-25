from __future__ import annotations

from pathlib import Path

import pytest

from steel_dxf_split.box import compiler, validation
from steel_dxf_split.box.compiler import compile_box_core
from steel_dxf_split.box.contracts import BoxSourceContract
from tests.box_v1.paths import INPUTS

SAMPLE = INPUTS / "2b1-cb-56_拆板前.dxf"


def test_compile_box_core_preserves_v1_mir_proof_and_pass_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    for name in (
        "run_frontend",
        "run_analysis",
        "run_solve",
        "freeze_manufacturing",
        "run_validation",
    ):
        original = getattr(compiler, name)

        def record(
            *args: object,
            _name: str = name,
            _original=original,
            **kwargs: object,
        ):
            events.append(_name)
            return _original(*args, **kwargs)

        monkeypatch.setattr(compiler, name, record)

    result = compile_box_core(SAMPLE, BoxSourceContract())

    assert events == [
        "run_frontend",
        "run_analysis",
        "run_solve",
        "freeze_manufacturing",
        "run_validation",
    ]
    assert result.source.path == SAMPLE.resolve()
    assert result.search.best.mir is result.manufacturing
    assert result.proof_report is result.search.best.proof_report
    assert result.validation["ok"] is True
    assert result.manufacturing.fingerprint == result.fingerprint
    assert result.proof_report.disposition.value == "auto_accept"


def test_source_contract_fails_before_frontend_reads_the_dxf(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    called = False

    def forbidden(_path: Path) -> object:
        nonlocal called
        called = True
        raise AssertionError("frontend must not run")

    monkeypatch.setattr(compiler, "run_frontend", forbidden)

    with pytest.raises(ValueError, match="source contract violation"):
        compile_box_core(
            SAMPLE,
            BoxSourceContract(export_profile="not-authorized"),
        )

    assert called is False


def test_validation_pass_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        validation,
        "validate_manufacturing_ir",
        lambda _manufacturing: {
            "ok": False,
            "checks": {"four_physical_roles": False},
        },
    )

    with pytest.raises(
        ValueError,
        match="four_physical_roles",
    ):
        validation.run_validation(object())
