from __future__ import annotations

import json
from pathlib import Path

import pytest
import steel_dxf_split
from steel_dxf_split import cli as split_cli
from steel_dxf_split import pipeline
from steel_dxf_split.bh_knowledge import BHSourceContract
from steel_dxf_split.box.contracts import BoxSourceContract

from app.modules.dxf_classification.interface import DxfSplitCandidateInput
from app.modules.dxf_splitting import execution as split_execution
from app.modules.dxf_splitting.validation import StagedSplitSource


def _write_manifest(path: Path, items: list[dict[str, str]]) -> Path:
    path.write_text(
        json.dumps(
            {
                "schema": "STEEL-DXF-CLASSIFIED-SPLIT-INPUT-1.0",
                "items": items,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return path


def test_classified_manifest_maps_each_frozen_input_exactly_once(tmp_path: Path) -> None:
    bh = tmp_path / "bh.dxf"
    box = tmp_path / "box.dxf"
    bh.write_bytes(b"bh")
    box.write_bytes(b"box")
    manifest = _write_manifest(
        tmp_path / "classified.json",
        [
            {"file_name": bh.name, "family": "BH"},
            {"file_name": box.name, "family": "BOX"},
        ],
    )

    classified = split_cli._load_classified_inputs(manifest, (bh, box))

    assert classified == {bh: "BH", box: "BOX"}


@pytest.mark.parametrize(
    ("items", "message"),
    [
        ([{"file_name": "bh.dxf", "family": "BH"}], "box.dxf"),
        (
            [
                {"file_name": "bh.dxf", "family": "BH"},
                {"file_name": "box.dxf", "family": "BOX"},
                {"file_name": "extra.dxf", "family": "BH"},
            ],
            "extra.dxf",
        ),
        (
            [
                {"file_name": "bh.dxf", "family": "BH"},
                {"file_name": "bh.dxf", "family": "BH"},
                {"file_name": "box.dxf", "family": "BOX"},
            ],
            "bh.dxf",
        ),
        (
            [
                {"file_name": "../bh.dxf", "family": "BH"},
                {"file_name": "box.dxf", "family": "BOX"},
            ],
            "../bh.dxf",
        ),
        (
            [
                {"file_name": "bh.dxf", "family": "BT"},
                {"file_name": "box.dxf", "family": "BOX"},
            ],
            "BT",
        ),
    ],
)
def test_classified_manifest_rejects_unsafe_or_non_bijective_mapping(
    tmp_path: Path,
    items: list[dict[str, str]],
    message: str,
) -> None:
    bh = tmp_path / "bh.dxf"
    box = tmp_path / "box.dxf"
    bh.write_bytes(b"bh")
    box.write_bytes(b"box")
    manifest = _write_manifest(tmp_path / "classified.json", items)

    with pytest.raises(ValueError, match=message.replace(".", r"\.")):
        split_cli._load_classified_inputs(manifest, (bh, box))


def test_cli_requires_classification_manifest() -> None:
    with pytest.raises(SystemExit):
        split_cli.build_parser().parse_args(
            ["input", "--output-dir", "output"]
        )


def test_package_exports_only_explicit_classified_dispatch() -> None:
    assert steel_dxf_split.split_classified_dxf is pipeline.split_classified_dxf
    assert "split_dxf" not in steel_dxf_split.__all__


def test_worker_manifest_uses_only_frozen_classification_types(
    tmp_path: Path,
) -> None:
    sources = [
        StagedSplitSource(
            semantic=DxfSplitCandidateInput(
                classification_item_id=index,
                drawing_id=None,
                classification_disposition="classified",
                part_type=family,
                profile_normalized=None,
                type_source="catalog",
                source_file_id=index + 100,
                output_file_id=index + 200,
                classifier_version="1.2.0",
            ),
            source_name=file_name,
            staged_path=tmp_path / file_name,
        )
        for index, (file_name, family) in enumerate(
            (("from-classifier-bh.dxf", "BH"), ("from-classifier-box.dxf", "BOX")),
            start=1,
        )
    ]

    path = split_execution._write_classification_manifest(
        tmp_path / "classified-input.json",
        sources,
    )

    assert json.loads(path.read_text(encoding="utf-8")) == {
        "schema": "STEEL-DXF-CLASSIFIED-SPLIT-INPUT-1.0",
        "items": [
            {"file_name": "from-classifier-bh.dxf", "family": "BH"},
            {"file_name": "from-classifier-box.dxf", "family": "BOX"},
        ],
    }


class _BhCalled(RuntimeError):
    pass


class _BoxCalled(RuntimeError):
    pass


def test_explicit_bh_family_calls_only_bh_core(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    def call_bh(*args, **kwargs):
        raise _BhCalled

    def reject_box(*args, **kwargs):
        raise AssertionError("BOX core must not receive a classified BH input")

    monkeypatch.setattr(pipeline, "split_bh_dxf", call_bh)
    monkeypatch.setattr("steel_dxf_split.box.compiler.compile_box", reject_box)

    with pytest.raises(_BhCalled):
        pipeline.split_classified_dxf(
            tmp_path / "classified-bh.dxf",
            tmp_path / "output",
            pipeline.SplitOptions(
                source_contract=BHSourceContract(
                    export_profile="project_tekla_bh_dxf_v1"
                )
            ),
            family="BH",
        )


def test_explicit_box_family_calls_only_box_core(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    def reject_bh(*args, **kwargs):
        raise AssertionError("BH core must not receive a classified BOX input")

    def call_box(*args, **kwargs):
        raise _BoxCalled

    monkeypatch.setattr(pipeline, "split_bh_dxf", reject_bh)
    monkeypatch.setattr("steel_dxf_split.box.compiler.compile_box", call_box)

    with pytest.raises(_BoxCalled):
        pipeline.split_classified_dxf(
            tmp_path / "classified-box.dxf",
            tmp_path / "output",
            pipeline.SplitOptions(
                box_source_contract=BoxSourceContract(
                    export_profile="project_tekla_box_dxf_v1"
                )
            ),
            family="BOX",
        )


def test_unsupported_family_fails_before_any_domain_core(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    def reject(*args, **kwargs):
        raise AssertionError("unsupported family reached a domain core")

    monkeypatch.setattr(pipeline, "split_bh_dxf", reject)
    monkeypatch.setattr("steel_dxf_split.box.compiler.compile_box", reject)

    with pytest.raises(ValueError, match="BH.*BOX"):
        pipeline.split_classified_dxf(
            tmp_path / "classified-bt.dxf",
            tmp_path / "output",
            pipeline.SplitOptions(),
            family="BT",
        )
