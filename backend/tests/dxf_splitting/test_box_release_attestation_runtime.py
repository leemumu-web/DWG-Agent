from __future__ import annotations

from steel_dxf_split.box.release import (
    load_verified_box_release_attestation,
    production_implementation_fingerprint,
)


def test_packaged_box_release_attestation_matches_current_runtime() -> None:
    verified = load_verified_box_release_attestation()

    assert verified.passed is True
    assert verified.pair_count == 20
    assert verified.calibration_count == 10
    assert verified.acceptance_count == 10
    assert (
        verified.implementation_fingerprint
        == production_implementation_fingerprint()
    )
