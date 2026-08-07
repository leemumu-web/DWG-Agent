from __future__ import annotations

import json
import os
import re
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
from importlib.resources import files
from pathlib import Path, PurePosixPath

from .provenance import (
    BOX_CORE_COMMIT,
    BOX_CORE_TAG,
    BOX_CORE_VERSION,
)

_SCHEMA_VERSION = "BOX-RELEASE-ATTESTATION-2.0"
_HEX_SHA256 = re.compile(r"[0-9a-f]{64}")
_BUILD_CONTRACT_SCHEMA = "BOX-BUILD-CONTRACT-1.0"
_PACKAGED_ATTESTATION = "release_evidence/box_release_attestation.json"
_PROTECTED_RUNTIME_MANIFEST = (
    "release_evidence/box_protected_runtime_manifest.json"
)
_PROTECTED_RUNTIME_SCHEMA = "BOX-PROTECTED-RUNTIME-MANIFEST-1.0"
_IMPLEMENTATION_SCHEMA = "BOX-PRODUCTION-IMPLEMENTATION-2.0"
_PACKAGE_LOGICAL_PREFIX = ("src", "steel_dxf_split")


def _canonical_json(payload: object) -> bytes:
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _file_sha256(path: Path) -> str:
    content = path.read_bytes()
    canonical = content.replace(b"\r\n", b"\n").replace(b"\r", b"\n")
    return sha256(canonical).hexdigest()


def _raw_file_sha256(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def _package_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _source_production_files(package_root: Path) -> tuple[tuple[str, Path], ...]:
    box_package = package_root / "box"
    files = [
        *(
            (
                f"src/steel_dxf_split/box/{path.relative_to(box_package).as_posix()}",
                path,
            )
            for path in box_package.rglob("*.py")
        ),
        ("src/steel_dxf_split/pipeline.py", package_root / "pipeline.py"),
        ("src/steel_dxf_split/cli.py", package_root / "cli.py"),
        (
            "src/steel_dxf_split/paired_output.py",
            package_root / "paired_output.py",
        ),
        (
            "src/steel_dxf_split/hole_color_policy.py",
            package_root / "hole_color_policy.py",
        ),
        (
            "src/steel_dxf_split/part_mark_layout.py",
            package_root / "part_mark_layout.py",
        ),
        (
            "src/steel_dxf_split/preview_fonts.py",
            package_root / "preview_fonts.py",
        ),
        (
            "src/steel_dxf_split/release_evidence/box_build_contract.json",
            package_root / "release_evidence/box_build_contract.json",
        ),
    ]
    missing = [path for _, path in files if not path.is_file()]
    if missing:
        raise ValueError(
            "BOX 生产实现指纹缺少文件："
            + ", ".join(str(path) for path in missing)
        )
    return tuple(sorted(files, key=lambda item: item[0]))


def _load_build_contract_hashes(package_root: Path) -> dict[str, str]:
    contract_path = package_root / "release_evidence/box_build_contract.json"
    payload = json.loads(contract_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or payload.get("schema") != _BUILD_CONTRACT_SCHEMA:
        raise ValueError("BOX 构建合同 schema 无效。")
    hashes = payload.get("files")
    if not isinstance(hashes, dict) or set(hashes) != {"pyproject.toml", "uv.lock"}:
        raise ValueError("BOX 构建合同文件集合无效。")
    if any(
        not isinstance(value, str) or _HEX_SHA256.fullmatch(value) is None
        for value in hashes.values()
    ):
        raise ValueError("BOX 构建合同包含无效 SHA-256。")
    return {str(path): str(digest) for path, digest in hashes.items()}


def _build_contract_entries(package_root: Path) -> list[dict[str, str]]:
    build_contract_hashes = _load_build_contract_hashes(package_root)
    project_root = package_root.parents[1]
    for relative, expected in build_contract_hashes.items():
        source_path = project_root / relative
        if source_path.is_file() and _file_sha256(source_path) != expected:
            raise ValueError(f"BOX 生产实现指纹的 {relative} 已漂移。")
    return [
        {"path": path, "sha256": digest}
        for path, digest in sorted(build_contract_hashes.items())
    ]


def _source_implementation_payload(package_root: Path) -> dict[str, object]:
    file_entries = [
        {"path": logical_path, "sha256": _file_sha256(path)}
        for logical_path, path in _source_production_files(package_root)
    ]
    file_entries.extend(_build_contract_entries(package_root))
    return {
        "schema": _IMPLEMENTATION_SCHEMA,
        "core": {
            "version": BOX_CORE_VERSION,
            "tag": BOX_CORE_TAG,
            "commit": BOX_CORE_COMMIT,
        },
        "files": sorted(file_entries, key=lambda item: item["path"]),
    }


def _package_path_for_logical(package_root: Path, logical_path: str) -> Path:
    relative = PurePosixPath(logical_path)
    if (
        relative.is_absolute()
        or relative.parts[:2] != _PACKAGE_LOGICAL_PREFIX
        or len(relative.parts) <= 2
        or any(part in {"", ".", ".."} for part in relative.parts)
    ):
        raise ValueError(
            f"BOX 受保护运行清单包含越界路径：{logical_path}"
        )
    candidate = package_root.joinpath(*relative.parts[2:]).resolve()
    try:
        candidate.relative_to(package_root.resolve())
    except ValueError as exc:
        raise ValueError(
            f"BOX 受保护运行清单包含越界路径：{logical_path}"
        ) from exc
    return candidate


def _validated_implementation_payload(value: object) -> dict[str, object]:
    if not isinstance(value, dict) or value.get("schema") != _IMPLEMENTATION_SCHEMA:
        raise ValueError("BOX 受保护运行清单的源码实现合同无效。")
    expected_core = {
        "version": BOX_CORE_VERSION,
        "tag": BOX_CORE_TAG,
        "commit": BOX_CORE_COMMIT,
    }
    if value.get("core") != expected_core:
        raise ValueError("BOX 受保护运行清单的核心版本或提交已漂移。")
    entries = value.get("files")
    if not isinstance(entries, list) or not entries:
        raise ValueError("BOX 受保护运行清单没有实现文件。")
    paths: set[str] = set()
    for entry in entries:
        if not isinstance(entry, dict) or set(entry) != {"path", "sha256"}:
            raise ValueError("BOX 受保护运行清单的实现文件字段无效。")
        logical_path = entry.get("path")
        digest = entry.get("sha256")
        if (
            not isinstance(logical_path, str)
            or not logical_path
            or logical_path in paths
            or not isinstance(digest, str)
            or _HEX_SHA256.fullmatch(digest) is None
        ):
            raise ValueError("BOX 受保护运行清单的实现文件值无效。")
        paths.add(logical_path)
    return value


def _load_protected_runtime_payload(package_root: Path) -> dict[str, object]:
    manifest_path = package_root / _PROTECTED_RUNTIME_MANIFEST
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(
            "BOX 加密运行态缺少可验证的字节码清单。"
        ) from exc
    if not isinstance(payload, dict) or set(payload) != {
        "schema_version",
        "source_implementation",
        "compiled_files",
        "payload_digest",
    }:
        raise ValueError("BOX 受保护运行清单顶层字段无效。")
    if payload.get("schema_version") != _PROTECTED_RUNTIME_SCHEMA:
        raise ValueError("BOX 受保护运行清单 schema 无效。")
    content = {
        "schema_version": payload["schema_version"],
        "source_implementation": payload["source_implementation"],
        "compiled_files": payload["compiled_files"],
    }
    digest = payload.get("payload_digest")
    if (
        not isinstance(digest, str)
        or _HEX_SHA256.fullmatch(digest) is None
        or digest != sha256(_canonical_json(content)).hexdigest()
    ):
        raise ValueError("BOX 受保护运行清单摘要已漂移。")

    source_payload = _validated_implementation_payload(
        payload["source_implementation"]
    )
    source_entries = source_payload["files"]
    assert isinstance(source_entries, list)
    source_python_paths = {
        str(entry["path"])
        for entry in source_entries
        if isinstance(entry, dict)
        and isinstance(entry.get("path"), str)
        and str(entry["path"]).endswith(".py")
    }
    expected_compiled_paths = {
        str(PurePosixPath(path).with_suffix(".pyc"))
        for path in source_python_paths
    }
    compiled_entries = payload["compiled_files"]
    if not isinstance(compiled_entries, list):
        raise ValueError("BOX 受保护运行清单的字节码集合无效。")
    actual_compiled_paths: set[str] = set()
    for entry in compiled_entries:
        if not isinstance(entry, dict) or set(entry) != {"path", "sha256"}:
            raise ValueError("BOX 受保护运行清单的字节码字段无效。")
        logical_path = entry.get("path")
        expected_digest = entry.get("sha256")
        if (
            not isinstance(logical_path, str)
            or logical_path in actual_compiled_paths
            or not isinstance(expected_digest, str)
            or _HEX_SHA256.fullmatch(expected_digest) is None
        ):
            raise ValueError("BOX 受保护运行清单的字节码值无效。")
        actual_compiled_paths.add(logical_path)
        compiled_path = _package_path_for_logical(package_root, logical_path)
        if (
            not compiled_path.is_file()
            or _raw_file_sha256(compiled_path) != expected_digest
        ):
            raise ValueError(
                f"BOX 加密运行态字节码缺失或漂移：{logical_path}"
            )
    if actual_compiled_paths != expected_compiled_paths:
        raise ValueError("BOX 受保护运行清单的字节码集合不完整。")

    for source_path in source_python_paths:
        if _package_path_for_logical(package_root, source_path).exists():
            raise ValueError("BOX 加密运行态混入了业务源码。")
    for entry in source_entries:
        if not isinstance(entry, dict):
            continue
        logical_path = entry.get("path")
        expected_digest = entry.get("sha256")
        if (
            not isinstance(logical_path, str)
            or logical_path.endswith(".py")
            or not logical_path.startswith("src/steel_dxf_split/")
        ):
            continue
        resource_path = _package_path_for_logical(package_root, logical_path)
        if (
            not resource_path.is_file()
            or _file_sha256(resource_path) != expected_digest
        ):
            raise ValueError(
                f"BOX 加密运行态资源缺失或漂移：{logical_path}"
            )
    return source_payload


def production_implementation_payload() -> dict[str, object]:
    package_root = _package_root()
    if (package_root / "pipeline.py").is_file():
        return _source_implementation_payload(package_root)
    return _load_protected_runtime_payload(package_root)


def production_implementation_fingerprint() -> str:
    return sha256(_canonical_json(production_implementation_payload())).hexdigest()


def _require_digest(name: str, value: object) -> str:
    if not isinstance(value, str) or _HEX_SHA256.fullmatch(value) is None:
        raise ValueError(f"BOX release attestation 的 {name} 不是 SHA-256。")
    return value


def _require_count(name: str, value: object) -> int:
    if type(value) is not int or value < 0:
        raise ValueError(f"BOX release attestation 的 {name} 数量无效。")
    return value


def _validate_counts(
    *,
    pair_count: int,
    calibration_count: int,
    acceptance_count: int,
    required_pair_count: int,
    minimum_calibration_count: int,
    minimum_acceptance_count: int,
) -> None:
    if (
        pair_count != calibration_count + acceptance_count
        or pair_count < required_pair_count
        or calibration_count < minimum_calibration_count
        or acceptance_count < minimum_acceptance_count
    ):
        raise ValueError("BOX release attestation 的认证数量不完整或不一致。")


def _fsync_directory(path: Path) -> None:
    directory_flag = getattr(os, "O_DIRECTORY", None)
    if directory_flag is None:
        return
    descriptor = os.open(path, os.O_RDONLY | directory_flag)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _write_json_atomic(path: Path, payload: dict[str, object]) -> None:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=destination.parent,
            prefix=f".{destination.name}.",
            suffix=".pending",
            delete=False,
            newline="\n",
        ) as temporary:
            temporary_path = Path(temporary.name)
            json.dump(
                payload,
                temporary,
                ensure_ascii=False,
                indent=2,
                allow_nan=False,
            )
            temporary.write("\n")
            temporary.flush()
            os.fsync(temporary.fileno())
        os.replace(temporary_path, destination)
        _fsync_directory(destination.parent)
        temporary_path = None
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def write_protected_runtime_manifest(path: Path | None = None) -> Path:
    """Bind the certified source payload to the exact legacy bytecode in an image."""
    package_root = _package_root()
    source_payload = _source_implementation_payload(package_root)
    source_entries = source_payload["files"]
    assert isinstance(source_entries, list)
    compiled_entries: list[dict[str, str]] = []
    for entry in source_entries:
        if (
            not isinstance(entry, dict)
            or not isinstance(entry.get("path"), str)
            or not str(entry["path"]).endswith(".py")
        ):
            continue
        compiled_logical = str(
            PurePosixPath(str(entry["path"])).with_suffix(".pyc")
        )
        compiled_path = _package_path_for_logical(
            package_root,
            compiled_logical,
        )
        if not compiled_path.is_file():
            raise ValueError(
                f"BOX 加密运行态缺少已编译字节码：{compiled_logical}"
            )
        compiled_entries.append(
            {
                "path": compiled_logical,
                "sha256": _raw_file_sha256(compiled_path),
            }
        )
    content: dict[str, object] = {
        "schema_version": _PROTECTED_RUNTIME_SCHEMA,
        "source_implementation": source_payload,
        "compiled_files": sorted(
            compiled_entries,
            key=lambda item: item["path"],
        ),
    }
    destination = (
        Path(path)
        if path is not None
        else package_root / _PROTECTED_RUNTIME_MANIFEST
    )
    _write_json_atomic(
        destination,
        {
            **content,
            "payload_digest": sha256(_canonical_json(content)).hexdigest(),
        },
    )
    return destination


def write_box_release_attestation(
    path: Path,
    *,
    pair_count: int,
    calibration_count: int,
    acceptance_count: int,
    manifest_fingerprint: str,
    gate_fingerprint: str,
    required_pair_count: int = 20,
    minimum_calibration_count: int = 10,
    minimum_acceptance_count: int = 10,
) -> None:
    _validate_counts(
        pair_count=pair_count,
        calibration_count=calibration_count,
        acceptance_count=acceptance_count,
        required_pair_count=required_pair_count,
        minimum_calibration_count=minimum_calibration_count,
        minimum_acceptance_count=minimum_acceptance_count,
    )
    certification: dict[str, object] = {
        "passed": True,
        "pair_count": pair_count,
        "calibration_count": calibration_count,
        "acceptance_count": acceptance_count,
        "manifest_fingerprint": _require_digest(
            "manifest_fingerprint",
            manifest_fingerprint,
        ),
        "gate_fingerprint": _require_digest(
            "gate_fingerprint",
            gate_fingerprint,
        ),
        "implementation_fingerprint": production_implementation_fingerprint(),
        "core": {
            "version": BOX_CORE_VERSION,
            "tag": BOX_CORE_TAG,
            "commit": BOX_CORE_COMMIT,
        },
    }
    content: dict[str, object] = {
        "schema_version": _SCHEMA_VERSION,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "certification": certification,
    }
    _write_json_atomic(
        Path(path),
        {
            **content,
            "payload_digest": sha256(_canonical_json(content)).hexdigest(),
        },
    )


@dataclass(frozen=True, slots=True)
class BoxVerifiedReleaseAttestation:
    passed: bool
    pair_count: int
    calibration_count: int
    acceptance_count: int
    manifest_fingerprint: str
    implementation_fingerprint: str
    gate_fingerprint: str
    payload_digest: str
    core_version: str
    core_tag: str
    core_commit: str
    release_path: Path

    def to_dict(self) -> dict[str, object]:
        return {
            "passed": self.passed,
            "pair_count": self.pair_count,
            "calibration_count": self.calibration_count,
            "acceptance_count": self.acceptance_count,
            "manifest_fingerprint": self.manifest_fingerprint,
            "implementation_fingerprint": self.implementation_fingerprint,
            "gate_fingerprint": self.gate_fingerprint,
            "payload_digest": self.payload_digest,
            "core": {
                "version": self.core_version,
                "tag": self.core_tag,
                "commit": self.core_commit,
            },
            "release_path": str(self.release_path),
        }


def load_verified_box_release_attestation(
    path: Path | None = None,
    *,
    required_pair_count: int = 20,
    minimum_calibration_count: int = 10,
    minimum_acceptance_count: int = 10,
) -> BoxVerifiedReleaseAttestation:
    if path is None:
        resource = files("steel_dxf_split").joinpath(_PACKAGED_ATTESTATION)
        release_path = Path(str(resource)).resolve()
        try:
            serialized = resource.read_text(encoding="utf-8")
        except (FileNotFoundError, OSError) as exc:
            raise ValueError(
                "BOX 内置 release attestation 缺失或不可读。"
            ) from exc
    else:
        release_path = Path(path).resolve()
        serialized = release_path.read_text(encoding="utf-8")
    payload = json.loads(serialized)
    if not isinstance(payload, dict):
        raise ValueError("BOX release attestation 顶层必须是 JSON object。")
    if payload.get("schema_version") != _SCHEMA_VERSION:
        raise ValueError("不支持的 BOX release attestation schema。")
    if set(payload) != {
        "schema_version",
        "created_at",
        "certification",
        "payload_digest",
    }:
        raise ValueError("BOX release attestation 顶层字段不符合合同。")
    certification = payload.get("certification")
    if not isinstance(certification, dict):
        raise ValueError("BOX release attestation 缺少认证摘要。")
    if set(certification) != {
        "passed",
        "pair_count",
        "calibration_count",
        "acceptance_count",
        "manifest_fingerprint",
        "gate_fingerprint",
        "implementation_fingerprint",
        "core",
    }:
        raise ValueError("BOX release attestation 认证字段不符合合同。")
    content = {
        "schema_version": payload["schema_version"],
        "created_at": payload.get("created_at"),
        "certification": certification,
    }
    payload_digest = _require_digest("payload_digest", payload.get("payload_digest"))
    if payload_digest != sha256(_canonical_json(content)).hexdigest():
        raise ValueError("BOX release attestation 的认证摘要已漂移。")
    if certification.get("passed") is not True:
        raise ValueError("BOX release attestation 未记录通过状态。")

    pair_count = _require_count("pair_count", certification.get("pair_count"))
    calibration_count = _require_count(
        "calibration_count",
        certification.get("calibration_count"),
    )
    acceptance_count = _require_count(
        "acceptance_count",
        certification.get("acceptance_count"),
    )
    _validate_counts(
        pair_count=pair_count,
        calibration_count=calibration_count,
        acceptance_count=acceptance_count,
        required_pair_count=required_pair_count,
        minimum_calibration_count=minimum_calibration_count,
        minimum_acceptance_count=minimum_acceptance_count,
    )

    core = certification.get("core")
    expected_core = {
        "version": BOX_CORE_VERSION,
        "tag": BOX_CORE_TAG,
        "commit": BOX_CORE_COMMIT,
    }
    if core != expected_core:
        raise ValueError("BOX release attestation 的核心版本或提交已漂移。")
    recorded_implementation = _require_digest(
        "implementation_fingerprint",
        certification.get("implementation_fingerprint"),
    )
    current_implementation = production_implementation_fingerprint()
    if recorded_implementation != current_implementation:
        raise ValueError("BOX release attestation 检测到实现代码漂移。")

    return BoxVerifiedReleaseAttestation(
        passed=True,
        pair_count=pair_count,
        calibration_count=calibration_count,
        acceptance_count=acceptance_count,
        manifest_fingerprint=_require_digest(
            "manifest_fingerprint",
            certification.get("manifest_fingerprint"),
        ),
        implementation_fingerprint=current_implementation,
        gate_fingerprint=_require_digest(
            "gate_fingerprint",
            certification.get("gate_fingerprint"),
        ),
        payload_digest=payload_digest,
        core_version=BOX_CORE_VERSION,
        core_tag=BOX_CORE_TAG,
        core_commit=BOX_CORE_COMMIT,
        release_path=release_path,
    )
