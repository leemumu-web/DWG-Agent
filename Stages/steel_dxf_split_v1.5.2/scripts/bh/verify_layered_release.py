#!/usr/bin/env python3
"""Independently verify a complete layered DXF/SVG research release."""
from __future__ import annotations

import argparse
from hashlib import sha256
from html.parser import HTMLParser
import json
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import unquote, urlsplit
from xml.etree import ElementTree

import ezdxf


SAMPLE_STAGE_IDS = (
    "00_input_provenance",
    "01_frontend_fact_ir",
    "02_annotation_facts",
    "03_metadata_semantics",
    "04_view_hypothesis_frontier",
    "05_candidate_lowering",
    "06_constraints_and_selection",
    "07_assembly_validation",
    "08_manufacturing_ir",
    "09_quality_route",
    "10_codegen_layout",
    "11_saved_output_validation",
    "12_manual_supervision",
)
SAMPLE_CATEGORIES = frozenset(
    {"intermediate", "final", "reference", "comparison"}
)
ALL_CATEGORIES = SAMPLE_CATEGORIES | {"corpus"}


class _ReferenceParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.references: list[str] = []

    def handle_starttag(
        self, tag: str, attrs: list[tuple[str, str | None]]
    ) -> None:
        del tag
        for key, value in attrs:
            if key in {"href", "src", "data"} and value:
                self.references.append(value)


def _error(errors: list[dict[str, Any]], code: str, **context: Any) -> None:
    errors.append({"code": code, **context})


def _sha256(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_file(run_dir: Path, relative: Any) -> tuple[Path | None, str]:
    text = str(relative or "")
    path = Path(text)
    if not text or path.is_absolute() or ".." in path.parts:
        return None, text
    candidate = (run_dir / path).resolve()
    if not candidate.is_relative_to(run_dir.resolve()):
        return None, text
    return candidate, text


def _artifact_records(manifest: dict[str, Any]) -> Iterable[tuple[str, dict[str, Any]]]:
    for sample in manifest.get("samples", []):
        sample_id = str(sample.get("sample_id", ""))
        for artifact in sample.get("artifacts", []):
            if isinstance(artifact, dict):
                yield sample_id, artifact
    for artifact in manifest.get("corpus_artifacts", []):
        if isinstance(artifact, dict):
            yield "_corpus", artifact


def _validate_artifact(
    sample_id: str,
    artifact: dict[str, Any],
    run_dir: Path,
    errors: list[dict[str, Any]],
    declared: set[Path],
    parsed: set[Path],
) -> None:
    category = str(artifact.get("category", ""))
    artifact_id = str(artifact.get("artifact_id", ""))
    dxf_relative = artifact.get("dxf_path")
    svg_relative = artifact.get("svg_path")
    if not dxf_relative or not svg_relative:
        _error(
            errors,
            "MISSING_MIRROR",
            sample_id=sample_id,
            artifact_id=artifact_id,
        )
        return

    dxf_path = Path(str(dxf_relative))
    svg_path = Path(str(svg_relative))
    dxf_parts = dxf_path.with_suffix("").parts
    svg_parts = svg_path.with_suffix("").parts
    valid_mirror = (
        category in ALL_CATEGORIES
        and len(dxf_parts) > 2
        and len(svg_parts) > 2
        and dxf_parts[0] == "dxf"
        and svg_parts[0] == "svg"
        and dxf_parts[1] == svg_parts[1] == category
        and dxf_parts[1:] == svg_parts[1:]
    )
    if not valid_mirror:
        _error(
            errors,
            "MISSING_MIRROR",
            sample_id=sample_id,
            artifact_id=artifact_id,
            dxf_path=str(dxf_relative),
            svg_path=str(svg_relative),
        )

    for kind, relative, digest_key, parse_code in (
        ("DXF", dxf_relative, "dxf_sha256", "DXF_PARSE_FAILED"),
        ("SVG", svg_relative, "svg_sha256", "SVG_PARSE_FAILED"),
    ):
        absolute, text = _safe_file(run_dir, relative)
        if absolute is None or not absolute.is_file():
            _error(
                errors,
                "MISSING_MIRROR",
                sample_id=sample_id,
                artifact_id=artifact_id,
                path=text,
            )
            continue
        relative_path = absolute.relative_to(run_dir.resolve())
        declared.add(relative_path)
        expected_digest = artifact.get(digest_key)
        if expected_digest and _sha256(absolute) != expected_digest:
            _error(errors, "HASH_MISMATCH", sample_id=sample_id, path=text)
        if relative_path in parsed:
            continue
        parsed.add(relative_path)
        try:
            if kind == "DXF":
                audit = ezdxf.readfile(absolute).audit()
                if audit.errors:
                    raise ValueError(f"DXF audit reported {len(audit.errors)} errors")
            else:
                root = ElementTree.parse(absolute).getroot()
                if not root.tag.endswith("svg"):
                    raise ValueError(f"unexpected SVG root: {root.tag}")
        except Exception as exc:
            _error(errors, parse_code, sample_id=sample_id, path=text, detail=str(exc))

    json_relative = artifact.get("json_path")
    if json_relative:
        absolute, text = _safe_file(run_dir, json_relative)
        if absolute is None or not absolute.is_file():
            _error(errors, "MISSING_JSON", sample_id=sample_id, path=text)
        else:
            declared.add(absolute.relative_to(run_dir.resolve()))
            expected_digest = artifact.get("json_sha256")
            if expected_digest and _sha256(absolute) != expected_digest:
                _error(errors, "HASH_MISMATCH", sample_id=sample_id, path=text)
            try:
                json.loads(absolute.read_text(encoding="utf-8"))
            except Exception as exc:
                _error(
                    errors,
                    "JSON_PARSE_FAILED",
                    sample_id=sample_id,
                    path=text,
                    detail=str(exc),
                )


def _validate_site(
    run_dir: Path, expected: list[str], errors: list[dict[str, Any]]
) -> None:
    site_root = run_dir / "site"
    required = [site_root / "index.html"] + [
        site_root / "samples" / sample_id / "index.html" for sample_id in expected
    ]
    for path in required:
        if not path.is_file():
            _error(
                errors,
                "BROKEN_SITE_LINK",
                source=path.relative_to(run_dir).as_posix(),
                target="missing_required_page",
            )
    if not site_root.exists():
        return
    run_resolved = run_dir.resolve()
    for page in sorted(site_root.rglob("*.html")):
        parser = _ReferenceParser()
        try:
            parser.feed(page.read_text(encoding="utf-8"))
        except Exception as exc:
            _error(
                errors,
                "BROKEN_SITE_LINK",
                source=page.relative_to(run_dir).as_posix(),
                target=str(exc),
            )
            continue
        for reference in parser.references:
            split = urlsplit(reference)
            if split.scheme or split.netloc:
                _error(
                    errors,
                    "BROKEN_SITE_LINK",
                    source=page.relative_to(run_dir).as_posix(),
                    target=reference,
                )
                continue
            if not split.path:
                continue
            target = (page.parent / unquote(split.path)).resolve()
            if not target.is_relative_to(run_resolved) or not target.exists():
                _error(
                    errors,
                    "BROKEN_SITE_LINK",
                    source=page.relative_to(run_dir).as_posix(),
                    target=reference,
                )


def verify_layered_release(source_dir: Path, run_dir: Path) -> dict[str, object]:
    source_dir = Path(source_dir)
    run_dir = Path(run_dir)
    expected = sorted(
        path.name.removesuffix("_拆板前.dxf")
        for path in source_dir.glob("*_拆板前.dxf")
    )
    errors: list[dict[str, Any]] = []
    if len(expected) != 20:
        _error(errors, "EXPECTED_SAMPLE_COUNT", expected=20, discovered=len(expected))

    manifest_path = run_dir / "manifest.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except Exception as exc:
        _error(errors, "MANIFEST_INVALID", detail=str(exc))
        manifest = {}

    sample_rows = [
        item for item in manifest.get("samples", []) if isinstance(item, dict)
    ]
    samples: dict[str, dict[str, Any]] = {}
    for sample in sample_rows:
        sample_id = str(sample.get("sample_id", ""))
        if sample_id in samples:
            _error(errors, "DUPLICATE_SAMPLE", sample_id=sample_id)
        samples[sample_id] = sample
    for sample_id in sorted(set(samples) - set(expected)):
        _error(errors, "UNEXPECTED_SAMPLE", sample_id=sample_id)

    dispositions: list[str] = []
    for sample_id in expected:
        sample = samples.get(sample_id)
        if sample is None:
            _error(errors, "MISSING_SAMPLE", sample_id=sample_id)
            continue
        stage_status = sample.get("stage_status", {})
        for stage_id in SAMPLE_STAGE_IDS:
            if stage_id not in stage_status:
                _error(
                    errors,
                    "MISSING_STAGE",
                    sample_id=sample_id,
                    stage_id=stage_id,
                )
            elif stage_status[stage_id] == "failed":
                _error(
                    errors,
                    "STAGE_FAILED",
                    sample_id=sample_id,
                    stage_id=stage_id,
                )
        disposition = str(sample.get("proof_disposition", ""))
        purpose = str(sample.get("output_purpose", ""))
        gate_applicable = sample.get("supervision_gate_applicable")
        gate_passed = sample.get("supervision_gate_passed")
        supervision_ok = sample.get("supervision", {}).get("ok") is True
        dispositions.append(disposition)
        if disposition == "auto_accept":
            if (
                purpose != "production"
                or gate_applicable is not True
                or gate_passed is not supervision_ok
            ):
                _error(errors, "OUTPUT_ROUTE_INVALID", sample_id=sample_id)
            if not supervision_ok or gate_passed is not True:
                _error(errors, "SUPERVISION_FAILED", sample_id=sample_id)
        elif disposition == "review_required":
            if (
                purpose != "review"
                or gate_applicable is not False
                or gate_passed is not None
            ):
                _error(errors, "OUTPUT_ROUTE_INVALID", sample_id=sample_id)
        else:
            _error(
                errors,
                "DISPOSITION_INVALID",
                sample_id=sample_id,
                disposition=disposition,
            )
        final_name = Path(str(sample.get("final_dxf_path", ""))).name
        if (
            disposition == "auto_accept"
            and ("清洁" not in final_name or "复核候选" in final_name)
        ) or (
            disposition == "review_required"
            and ("复核候选" not in final_name or "清洁" in final_name)
        ):
            _error(errors, "OUTPUT_ROUTE_INVALID", sample_id=sample_id)
        saved = sample.get("saved_validation", {})
        if saved.get("ok") is not True:
            _error(errors, "FINAL_VALIDATION_FAILED", sample_id=sample_id)
        if (
            saved.get("generated_line_count", 0) != 0
            or saved.get("checks", {}).get("no_cross_or_helper_lines") is False
        ):
            _error(
                errors,
                "FINAL_HELPER_LINE",
                sample_id=sample_id,
                count=saved.get("generated_line_count"),
            )
        categories = {
            str(item.get("category", ""))
            for item in sample.get("artifacts", [])
            if isinstance(item, dict)
        }
        for category in sorted(SAMPLE_CATEGORIES - categories):
            _error(
                errors,
                "MISSING_MIRROR",
                sample_id=sample_id,
                category=category,
            )

    corpus = [
        item
        for item in manifest.get("corpus_artifacts", [])
        if isinstance(item, dict)
        and item.get("category") == "corpus"
        and item.get("stage_id") == "13_corpus_summary"
    ]
    if not corpus:
        _error(errors, "MISSING_CORPUS_STAGE", stage_id="13_corpus_summary")

    declared: set[Path] = set()
    parsed: set[Path] = set()
    for sample_id, artifact in _artifact_records(manifest):
        _validate_artifact(sample_id, artifact, run_dir, errors, declared, parsed)

    actual: set[Path] = set()
    for directory in ("dxf", "svg", "json"):
        root = run_dir / directory
        if root.exists():
            actual.update(
                path.relative_to(run_dir.resolve())
                for path in root.resolve().rglob("*")
                if path.is_file()
            )
    for path in sorted(actual - declared, key=lambda item: item.as_posix()):
        _error(errors, "ORPHAN_FILE", path=path.as_posix())

    _validate_site(run_dir, expected, errors)
    codes = {str(item["code"]) for item in errors}
    sample_ids_complete = set(samples) == set(expected) and len(sample_rows) == len(expected)
    disposition_counts = {
        "auto_accept": dispositions.count("auto_accept"),
        "review_required": dispositions.count("review_required"),
        "rejected_or_invalid": sum(
            item not in {"auto_accept", "review_required"}
            for item in dispositions
        ),
    }
    return {
        "schema": "STEEL-DXF-LAYERED-RELEASE-VERIFY-1.1",
        "expected_sample_count": len(expected),
        "sample_count": len(samples),
        "all_stages_complete": not ({"MISSING_STAGE", "STAGE_FAILED"} & codes),
        "all_dxf_parseable": "DXF_PARSE_FAILED" not in codes,
        "all_svg_parseable": "SVG_PARSE_FAILED" not in codes,
        "all_mirrors_complete": "MISSING_MIRROR" not in codes,
        "all_site_links_valid": "BROKEN_SITE_LINK" not in codes,
        "all_supervised_comparisons_pass": all(
            sample.get("supervision", {}).get("ok") is True
            for sample in samples.values()
        ),
        "all_applicable_supervision_gates_pass": (
            "SUPERVISION_FAILED" not in codes
        ),
        "all_output_routes_safe": not (
            {"DISPOSITION_INVALID", "OUTPUT_ROUTE_INVALID"} & codes
        ),
        "disposition_counts": disposition_counts,
        "all_final_outputs_safe": not (
            {"FINAL_VALIDATION_FAILED", "FINAL_HELPER_LINE"} & codes
        ),
        "errors": errors,
        "release_ready": not errors
        and sample_ids_complete
        and len(expected) == len(samples) == 20,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-dir", required=True, type=Path)
    parser.add_argument("--run-dir", required=True, type=Path)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    report = verify_layered_release(args.source_dir, args.run_dir)
    payload = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.report is not None:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(payload, encoding="utf-8")
    print(payload, end="")
    return 0 if report["release_ready"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
