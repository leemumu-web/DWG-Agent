from pathlib import Path

from steel_dxf_split.bh_trace import STAGE_REGISTRY
from steel_dxf_split.layered_site import build_site, validate_site_links


def test_site_has_corpus_matrix_all_candidates_and_no_remote_assets(
    tmp_path: Path,
) -> None:
    artifact_paths = [
        "dxf/intermediate/beam/05_candidate_lowering/assembly-01/0001-web_faces.dxf",
        "svg/intermediate/beam/05_candidate_lowering/assembly-01/0001-web_faces.svg",
        "json/beam/05_candidate_lowering/assembly-01/0001-web_faces.json",
    ]
    for relative in artifact_paths:
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("artifact", encoding="utf-8")
    sample_manifest = {
        "samples": [
            {
                "sample_id": "beam",
                "stage_status": {
                    item.stage_id: "observed"
                    for item in STAGE_REGISTRY
                        if not item.stage_id.startswith("13_")
                },
                "candidates": ["assembly-01", "assembly-02"],
                "artifacts": artifact_paths,
            }
        ]
    }
    build_site(sample_manifest, tmp_path / "site")
    index = (tmp_path / "site/index.html").read_text(encoding="utf-8")
    sample = (tmp_path / "site/samples/beam/index.html").read_text(
        encoding="utf-8"
    )
    assert "00 输入与来源" in index
    assert "13 语料总览" in index
    assert "assembly-01" in sample
    assert "assembly-02" in sample
    assert "STEEL DXF SPLIT v1.5.2" in index
    assert "http://" not in index + sample
    assert "https://" not in index + sample
    assert validate_site_links(tmp_path / "site", tmp_path).ok


def test_link_validator_rejects_missing_external_and_escape_links(
    tmp_path: Path,
) -> None:
    site = tmp_path / "site"
    site.mkdir()
    (site / "index.html").write_text(
        '<a href="missing.html">missing</a>'
        '<img src="https://example.com/x.svg">'
        '<a href="../../outside.txt">escape</a>',
        encoding="utf-8",
    )
    report = validate_site_links(site, tmp_path)
    assert not report.ok
    assert report.missing
    assert report.external
    assert report.escaping
