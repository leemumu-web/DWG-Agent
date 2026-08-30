from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

XBOX_RELEASE_SCHEMA = "XBOX-RELEASE-ATTESTATION-1.0"
XBOX_RELEASE_FILENAME = "xbox_release_attestation.json"
XBOX_PROTECTED_MANIFEST_SCHEMA = "XBOX-PROTECTED-RUNTIME-MANIFEST-1.0"
XBOX_PROTECTED_MANIFEST_FILENAME = "xbox_protected_runtime_manifest.json"
XBOX_COMPILER_VERSION = "0.1.0"

# Implementation files bound by the release fingerprint: this package's own
# layer plus the vendored closed-box core. Byte changes anywhere in this set
# invalidate the attestation until a fresh 20/20 acceptance run re-pins it.
_OWN_IMPLEMENTATION_FILES = (
    "__init__.py",
    "cli.py",
    "compiler.py",
    "contracts.py",
    "paired_output.py",
    "pairing.py",
    "release.py",
)
_VENDORED_TOP_LEVEL_FILES = (
    "hole_color_policy.py",
    "part_mark_layout.py",
    "preview_fonts.py",
    "weld_allowance_geometry.py",
)


@dataclass(frozen=True, slots=True)
class XboxVerifiedReleaseAttestation:
    payload: dict[str, object]

    def to_dict(self) -> dict[str, object]:
        return dict(self.payload)


def _package_root() -> Path:
    return Path(__file__).resolve().parent


def _implementation_files() -> list[tuple[str, Path]]:
    package_root = _package_root()
    sources: list[tuple[str, Path]] = []
    for name in _OWN_IMPLEMENTATION_FILES:
        sources.append((f"src/steel_dxf_split_xbox/{name}", package_root / name))
    for name in _VENDORED_TOP_LEVEL_FILES:
        sources.append((f"src/steel_dxf_split_xbox/{name}", package_root / name))
    for subpackage in ("box", "manufacturing_decision"):
        directory = package_root / subpackage
        for path in sorted(directory.rglob("*.py")):
            logical = (
                f"src/steel_dxf_split_xbox/{subpackage}/"
                f"{path.relative_to(directory).as_posix()}"
            )
            sources.append((logical, path))
    return sorted(sources, key=lambda item: item[0])


def _source_implementation_payload() -> dict[str, object]:
    files: list[dict[str, str]] = []
    for logical, path in _implementation_files():
        if not path.is_file():
            raise ValueError(f"XBOX implementation source is missing: {logical}")
        files.append(
            {
                "path": logical,
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            }
        )
    return {"schema": "XBOX-IMPLEMENTATION-1.0", "files": files}


def _protected_manifest_path() -> Path:
    return (
        _package_root()
        / "release_evidence"
        / XBOX_PROTECTED_MANIFEST_FILENAME
    )


def _implementation_payload() -> dict[str, object]:
    if (_package_root() / "compiler.py").is_file():
        return _source_implementation_payload()
    manifest_path = _protected_manifest_path()
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(
            "XBOX implementation sources were removed for the protected image "
            f"and no runtime manifest exists: {manifest_path}"
        ) from exc
    if not isinstance(payload, dict) or payload.get("schema") != (
        XBOX_PROTECTED_MANIFEST_SCHEMA
    ):
        raise ValueError("XBOX protected runtime manifest is invalid")
    implementation = payload.get("implementation")
    if not isinstance(implementation, dict):
        raise ValueError("XBOX protected runtime manifest lacks implementation")
    return implementation


def production_implementation_payload() -> dict[str, object]:
    return _implementation_payload()


def production_implementation_fingerprint() -> str:
    payload = _implementation_payload()
    encoded = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def write_xbox_protected_runtime_manifest(path: str | Path | None = None) -> Path:
    """Freeze the source-state implementation payload before bytecode compilation."""

    target = Path(path) if path is not None else _protected_manifest_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema": XBOX_PROTECTED_MANIFEST_SCHEMA,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "implementation": _source_implementation_payload(),
    }
    target.write_text(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    return target


def _default_attestation_path() -> Path:
    return (
        _package_root()
        / "release_evidence"
        / XBOX_RELEASE_FILENAME
    )


def _digest(payload: dict[str, object]) -> str:
    body = {key: value for key, value in payload.items() if key != "payload_digest"}
    encoded = json.dumps(
        body,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def write_xbox_release_attestation(
    path: str | Path,
    *,
    manifest_sha256: str,
    gate_fingerprint: str,
) -> None:
    if any(
        len(value) != 64 or any(character not in "0123456789abcdef" for character in value)
        for value in (manifest_sha256, gate_fingerprint)
    ):
        raise ValueError("XBOX release fingerprints must be lowercase SHA-256 values")
    payload: dict[str, object] = {
        "schema": XBOX_RELEASE_SCHEMA,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "certification": {
            "passed": True,
            "pair_count": 20,
            "calibration_count": 10,
            "acceptance_count": 10,
            "compiler_version": XBOX_COMPILER_VERSION,
            "manifest_sha256": manifest_sha256,
            "gate_fingerprint": gate_fingerprint,
            "implementation_fingerprint": production_implementation_fingerprint(),
        },
    }
    payload["payload_digest"] = _digest(payload)
    Path(path).write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def load_verified_xbox_release_attestation(
    path: str | Path | None = None,
) -> XboxVerifiedReleaseAttestation:
    source = Path(path).resolve() if path is not None else _default_attestation_path()
    try:
        payload = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"XBOX release attestation cannot be read: {source}") from exc
    if not isinstance(payload, dict):
        raise ValueError("XBOX release attestation must be an object")
    if payload.get("schema") != XBOX_RELEASE_SCHEMA:
        raise ValueError("XBOX release attestation schema is invalid")
    certification = payload.get("certification")
    if not isinstance(certification, dict):
        raise ValueError("XBOX release certification is missing")
    if (
        certification.get("passed") is not True
        or certification.get("pair_count") != 20
        or certification.get("calibration_count") != 10
        or certification.get("acceptance_count") != 10
        or certification.get("compiler_version") != XBOX_COMPILER_VERSION
    ):
        raise ValueError("XBOX release certification did not pass the required gate")
    for name in (
        "manifest_sha256",
        "gate_fingerprint",
        "implementation_fingerprint",
    ):
        value = certification.get(name)
        if (
            not isinstance(value, str)
            or len(value) != 64
            or any(character not in "0123456789abcdef" for character in value)
        ):
            raise ValueError(f"XBOX release {name} is invalid")
    if certification["implementation_fingerprint"] != production_implementation_fingerprint():
        raise ValueError("XBOX release attestation detected implementation drift")
    digest = payload.get("payload_digest")
    if not isinstance(digest, str) or digest != _digest(payload):
        raise ValueError("XBOX release attestation digest is invalid")
    return XboxVerifiedReleaseAttestation(payload=dict(payload))


__all__ = [
    "XBOX_COMPILER_VERSION",
    "XBOX_PROTECTED_MANIFEST_FILENAME",
    "XBOX_PROTECTED_MANIFEST_SCHEMA",
    "XBOX_RELEASE_FILENAME",
    "XBOX_RELEASE_SCHEMA",
    "XboxVerifiedReleaseAttestation",
    "load_verified_xbox_release_attestation",
    "production_implementation_fingerprint",
    "production_implementation_payload",
    "write_xbox_protected_runtime_manifest",
    "write_xbox_release_attestation",
]
