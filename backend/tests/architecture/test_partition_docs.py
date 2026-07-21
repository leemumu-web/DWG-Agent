from __future__ import annotations

import sys

from tests.support.paths import REPO_ROOT

sys.path.insert(0, str(REPO_ROOT))

from scripts.architecture.check_partition_docs import (  # noqa: E402
    PARTITIONS,
    ROOT,
    _direct_source_files,
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


def test_partition_readmes_name_every_directly_owned_source_file() -> None:
    missing: dict[str, list[str]] = {}
    for relative in PARTITIONS:
        directory = ROOT / relative
        readme = directory / "README.md"
        if not readme.is_file():
            continue
        content = readme.read_text(encoding="utf-8")
        absent = [
            source.name
            for source in _direct_source_files(directory)
            if source.name not in content
        ]
        if absent:
            missing[relative] = absent

    assert missing == {}
