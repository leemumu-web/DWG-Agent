from __future__ import annotations

import sys

from tests.support.paths import REPO_ROOT

sys.path.insert(0, str(REPO_ROOT))

from scripts.architecture.check_partition_docs import (  # noqa: E402
    PARTITIONS,
    _source_owned_partitions,
    validation_errors,
)


def test_every_maintained_partition_has_a_local_introduction() -> None:
    assert len(PARTITIONS) == len(set(PARTITIONS))
    assert "backend/app/modules/workflows/intake" in PARTITIONS
    assert "backend/app/modules/files/routes" in PARTITIONS
    assert "frontend/src/features/operations/components/data-console" in PARTITIONS
    assert "frontend/tests/e2e/operations" in PARTITIONS
    assert "Stages/steel_dxf_classifier_v1.1.0" in PARTITIONS
    assert "windows/node-agent" in PARTITIONS
    assert "agents/cad-agent" in PARTITIONS
    assert "backend/app/repositories" not in PARTITIONS
    assert set(_source_owned_partitions()).issubset(PARTITIONS)
    assert len(PARTITIONS) >= 134
    assert validation_errors() == []
