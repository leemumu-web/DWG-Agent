from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
from pathlib import Path
import re
from typing import Any


_MANIFEST_FIELDS = frozenset({"schema_version", "cases"})
_CASE_FIELDS = frozenset(
    {
        "sample_id",
        "source_file",
        "manual_file",
        "source_sha256",
        "manual_sha256",
        "export_profile",
        "profile",
        "material",
        "physical_plates",
        "blocking_proof_ids",
        "disposition",
    }
)
_PLATE_FIELDS = frozenset(
    {
        "role",
        "thickness_mm",
        "bbox_mm",
        "circular_cut_count",
        "inner_contour_count",
    }
)
_PHYSICAL_ROLES = ("web", "upper_flange", "lower_flange")
_DISPOSITIONS = frozenset({"auto_accept", "review_required"})
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_SAMPLE_ID = re.compile(r"^[A-Za-z0-9-]+$")


@dataclass(frozen=True, slots=True)
class CorpusPlateExpectation:
    role: str
    thickness_mm: float
    bbox_mm: tuple[float, float]
    circular_cut_count: int
    inner_contour_count: int


@dataclass(frozen=True, slots=True)
class CorpusCase:
    sample_id: str
    source_file: str
    manual_file: str
    source_sha256: str
    manual_sha256: str
    export_profile: str
    profile: str
    material: str | None
    physical_plates: tuple[CorpusPlateExpectation, ...]
    blocking_proof_ids: tuple[str, ...]
    disposition: str

    def source_path(self, source_dir: Path) -> Path:
        return source_dir / self.source_file

    def manual_path(self, reference_dir: Path) -> Path:
        return reference_dir / self.manual_file


@dataclass(frozen=True, slots=True)
class CorpusManifest:
    schema_version: str
    cases: tuple[CorpusCase, ...]


def _require_fields(
    payload: dict[str, Any],
    expected: frozenset[str],
    *,
    location: str,
) -> None:
    actual = frozenset(payload)
    unknown = sorted(actual - expected)
    missing = sorted(expected - actual)
    if unknown:
        raise ValueError(f"{location} has unknown fields: {', '.join(unknown)}")
    if missing:
        raise ValueError(f"{location} is missing fields: {', '.join(missing)}")


def _finite_positive(value: Any, *, location: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{location} must be a number")
    result = float(value)
    if not 0.0 < result < float("inf"):
        raise ValueError(f"{location} must be finite and positive")
    return result


def _nonnegative_int(value: Any, *, location: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{location} must be a non-negative integer")
    return value


def _plate(payload: Any, *, location: str) -> CorpusPlateExpectation:
    if not isinstance(payload, dict):
        raise ValueError(f"{location} must be an object")
    _require_fields(payload, _PLATE_FIELDS, location=location)
    bbox = payload["bbox_mm"]
    if not isinstance(bbox, list) or len(bbox) != 2:
        raise ValueError(f"{location}.bbox_mm must contain width and height")
    return CorpusPlateExpectation(
        role=str(payload["role"]),
        thickness_mm=_finite_positive(
            payload["thickness_mm"], location=f"{location}.thickness_mm"
        ),
        bbox_mm=(
            _finite_positive(bbox[0], location=f"{location}.bbox_mm[0]"),
            _finite_positive(bbox[1], location=f"{location}.bbox_mm[1]"),
        ),
        circular_cut_count=_nonnegative_int(
            payload["circular_cut_count"],
            location=f"{location}.circular_cut_count",
        ),
        inner_contour_count=_nonnegative_int(
            payload["inner_contour_count"],
            location=f"{location}.inner_contour_count",
        ),
    )


def _case(payload: Any, *, index: int) -> CorpusCase:
    location = f"cases[{index}]"
    if not isinstance(payload, dict):
        raise ValueError(f"{location} must be an object")
    _require_fields(payload, _CASE_FIELDS, location=location)
    sample_id = payload["sample_id"]
    if not isinstance(sample_id, str) or not _SAMPLE_ID.fullmatch(sample_id):
        raise ValueError(f"{location}.sample_id is invalid")
    source_file = payload["source_file"]
    manual_file = payload["manual_file"]
    if source_file != f"{sample_id}_拆板前.dxf":
        raise ValueError(f"{location}.source_file does not match sample_id")
    if manual_file != f"{sample_id}_拆板后.dxf":
        raise ValueError(f"{location}.manual_file does not match sample_id")
    source_digest = payload["source_sha256"]
    manual_digest = payload["manual_sha256"]
    if not isinstance(source_digest, str) or not _SHA256.fullmatch(source_digest):
        raise ValueError(f"{location}.source_sha256 is invalid")
    if not isinstance(manual_digest, str) or not _SHA256.fullmatch(manual_digest):
        raise ValueError(f"{location}.manual_sha256 is invalid")
    plate_payloads = payload["physical_plates"]
    if not isinstance(plate_payloads, list):
        raise ValueError(f"{location}.physical_plates must be a list")
    plates = tuple(
        _plate(item, location=f"{location}.physical_plates[{plate_index}]")
        for plate_index, item in enumerate(plate_payloads)
    )
    if tuple(plate.role for plate in plates) != _PHYSICAL_ROLES:
        raise ValueError(
            f"{location}.physical_plates must be web, upper_flange, lower_flange"
        )
    disposition = payload["disposition"]
    if disposition not in _DISPOSITIONS:
        raise ValueError(f"{location}.disposition is unsupported")
    blocking = payload["blocking_proof_ids"]
    if not isinstance(blocking, list) or not all(
        isinstance(item, str) and item for item in blocking
    ):
        raise ValueError(f"{location}.blocking_proof_ids must be strings")
    if disposition == "auto_accept" and blocking:
        raise ValueError(f"{location} auto_accept cannot have blocking proofs")
    if disposition == "review_required" and not blocking:
        raise ValueError(f"{location} review_required must identify blocking proofs")
    export_profile = payload["export_profile"]
    profile = payload["profile"]
    material = payload["material"]
    if not isinstance(export_profile, str) or not export_profile:
        raise ValueError(f"{location}.export_profile must be a non-empty string")
    if not isinstance(profile, str) or not profile.startswith("BH"):
        raise ValueError(f"{location}.profile must be a BH profile")
    if material is not None and (not isinstance(material, str) or not material):
        raise ValueError(f"{location}.material must be null or a non-empty string")
    return CorpusCase(
        sample_id=sample_id,
        source_file=source_file,
        manual_file=manual_file,
        source_sha256=source_digest,
        manual_sha256=manual_digest,
        export_profile=export_profile,
        profile=profile,
        material=material,
        physical_plates=plates,
        blocking_proof_ids=tuple(blocking),
        disposition=disposition,
    )


def _file_sha256(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def validate_corpus_source_file(case: CorpusCase, source_dir: Path) -> None:
    source = case.source_path(source_dir)
    if not source.is_file():
        raise FileNotFoundError(f"missing source DXF: {source}")
    if _file_sha256(source) != case.source_sha256:
        raise ValueError(f"source hash mismatch: {case.sample_id}")


def validate_corpus_manual_file(case: CorpusCase, reference_dir: Path) -> None:
    manual = case.manual_path(reference_dir)
    if not manual.is_file():
        raise FileNotFoundError(f"missing manual DXF: {manual}")
    if _file_sha256(manual) != case.manual_sha256:
        raise ValueError(f"manual hash mismatch: {case.sample_id}")


def validate_corpus_case_files(
    case: CorpusCase,
    source_dir: Path,
    reference_dir: Path,
) -> None:
    validate_corpus_source_file(case, source_dir)
    validate_corpus_manual_file(case, reference_dir)


def load_corpus_manifest(
    path: Path,
    *,
    source_dir: Path | None = None,
    reference_dir: Path | None = None,
) -> CorpusManifest:
    """Load the offline corpus oracle and optionally bind it to split file roots.

    The caller supplies both directories explicitly. No compiler module
    discovers or imports this fixture, so manual drawings cannot become
    production evidence.
    """

    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("corpus manifest must be an object")
    _require_fields(payload, _MANIFEST_FIELDS, location="manifest")
    if payload["schema_version"] != "BH-CORPUS-1.0":
        raise ValueError("unsupported corpus manifest schema")
    raw_cases = payload["cases"]
    if not isinstance(raw_cases, list):
        raise ValueError("manifest.cases must be a list")
    cases = tuple(_case(item, index=index) for index, item in enumerate(raw_cases))
    ids = [case.sample_id for case in cases]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate sample_id in corpus manifest")

    if (source_dir is None) != (reference_dir is None):
        raise ValueError(
            "source_dir and reference_dir must be provided together"
        )
    if source_dir is not None and reference_dir is not None:
        for case in cases:
            validate_corpus_case_files(case, source_dir, reference_dir)
    return CorpusManifest(schema_version=payload["schema_version"], cases=cases)
