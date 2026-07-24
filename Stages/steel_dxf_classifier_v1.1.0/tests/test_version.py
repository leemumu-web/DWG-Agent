from pathlib import Path

import steel_dxf_classifier


ROOT = Path(__file__).resolve().parents[1]


def test_release_version_is_synchronized() -> None:
    assert steel_dxf_classifier.__version__ == "1.2.0"
    assert (ROOT / "VERSION").read_text(encoding="utf-8").strip() == "1.2.0"
    assert 'version = "1.2.0"' in (ROOT / "pyproject.toml").read_text(
        encoding="utf-8"
    )
