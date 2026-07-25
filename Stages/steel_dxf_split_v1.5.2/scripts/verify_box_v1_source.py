from __future__ import annotations

import argparse
from hashlib import sha256
import json
from pathlib import Path
import subprocess
from typing import Any


BOX_CORE_TAG = "v1.0.0"
BOX_CORE_COMMIT = "5a2be1a82eb7235bcff62d97a13d2937f9ad026b"
BOX_CORE_PATCHSET_ID = "box-view-preprocessing-hole-color-unified-part-mark-and-role-marks-2026-07-24"

FROZEN_SOURCE_FILES = (
    "artifact_io.py",
    "assembly.py",
    "box_region.py",
    "course_graph.py",
    "dxf_artifact_io.py",
    "dxf_io.py",
    "equivalence.py",
    "flange_solver.py",
    "inspect_cli.py",
    "manufacturing_ir.py",
    "metadata.py",
    "openings.py",
    "preview.py",
    "process_control.py",
    "projection_geometry.py",
    "projection_lowering.py",
    "proofs.py",
    "source_ir.py",
    "validator.py",
    "view_frame.py",
    "view_solver.py",
    "web_solver.py",
    "weld_allowance.py",
    "writer.py",
)

ADAPTED_SOURCE_FILES: dict[str, dict[str, str]] = {
    "__init__.py": {
        "upstream_sha256": "35efddd48e2c79a58f74588eccb440b09b62c4e74be65307eccfb57a816398b2",
        "internal_sha256": "e8ead91dd1bf41dde2885d86aab9ccdd60b8444c06257b2de3dc9e3737462584",
        "reason": "retain BOX version metadata while removing its second public split API",
    },
}

RETIRED_SOURCE_FILES: dict[str, dict[str, str]] = {
    "batch_cli.py": {
        "upstream_sha256": "dbe0b00868e2271fd278bb7b1838f2e3c54c919d107f5a7d7372983928248dd1",
        "reason": "the unified worker owns isolated batch orchestration",
    },
    "cli.py": {
        "upstream_sha256": "df55688ee5cca4aa72fbf4b5ac6d826fbea757c27e6a701f55763f990cfbd910",
        "reason": "the unified worker is the only public single-file entrypoint",
    },
    "pipeline.py": {
        "upstream_sha256": "c84054951e497460236bac10b30dd7ccb1643e6124c0cd76a443e6a60f37559b",
        "reason": "Project2 orchestration was superseded by the unified worker and BOX compiler",
    },
    "weld_allowance_cli.py": {
        "upstream_sha256": "951ac3ee9874ed598c78678b9074e75a519e4172b361baaac126358499e4aeb6",
        "reason": "allowance processing is an in-memory continuation of the same split task",
    },
    "weld_allowance_release.py": {
        "upstream_sha256": "0c59d4446a18b41e2bf6bdecc5cb9fda5415378706a381b9f17c93f77a7f04c9",
        "reason": "paired task validation replaces the retired independent batch release chain",
    },
}

PATCHED_SOURCE_FILES: dict[str, dict[str, str]] = {
    "artifact_io.py": {
        "upstream_sha256": "92168d7398397af18b6c4826df01e78f710fbb52339b5013b5b449ecf0559117",
        "internal_sha256": "0620547eda6044ccc0a396228e3cd5c82115a061cd2a86b0370fef5db2b27a21",
        "reason": "preserve directory durability where Windows exposes no O_DIRECTORY flag",
    },
    "assembly.py": {
        "upstream_sha256": "89d3fcd4f3af682839c0dc941a015cea7bb60d8a337559ec98e5c26829562751",
        "internal_sha256": "50e00999f66d43c13404944b91dc86f86f5f2c89a5d566fe1d23d067b11b1a93",
        "reason": "apply evidence-gated view preprocessing and unified material-safe part-mark layout",
    },
    "course_graph.py": {
        "upstream_sha256": "52dc39b3e392b29156baf706079b2fb16da83dadf9403d3fee5872c013357715",
        "internal_sha256": "fafcaa78b142d0f5ad3a4f6b45302ab1e537a31e7202b25a7df819d047ce2b36",
        "reason": "shared Tekla hidden-projection dialect normalization",
    },
    "flange_solver.py": {
        "upstream_sha256": "487826703f7b024799d4aae7f9d4f3902688447e344519b7a9018fcbdff95468",
        "internal_sha256": "cd4ba1a866ab71cbdc8a30461ae5183e64a2adc46701569bc48f99599364e7c0",
        "reason": "source-face notch recovery and thickness-bounded cap boundary proof",
    },
    "projection_geometry.py": {
        "upstream_sha256": "2355f211acc4cd7cd815b3cc7cd76b0f2c2d92d107fd520af01110bfa01ae02d",
        "internal_sha256": "c6c954d94293404b8ed85ca9c0983a7b8cf12ab6e0635045d08a232749ebb4ad",
        "reason": "maximal source-face lane and explicit bounded-search completeness",
    },
    "source_ir.py": {
        "upstream_sha256": "d2f5e73e2e561349b64bb922315e441c89e69f866c6c196eb2dabb536df99e9a",
        "internal_sha256": "92ae5ecf809c3b86d72da49f2f9f266640ae8202b5faa4669c955d340300a852",
        "reason": "central XKITLINE04 and DOT2 hidden-projection dialect predicate",
    },
    "validator.py": {
        "upstream_sha256": "23946c91af1793c093292b887e3c5fc7e84181f13edea9ff6dbe882c709a27b8",
        "internal_sha256": "e33598d011832bd3d155915ca2c8ff3c1fa6b2ef6b82d23c04e61b547f077c2a",
        "reason": "prove symmetric circular-hole ACI colors and white noncircular cuts",
    },
    "view_solver.py": {
        "upstream_sha256": "6ebc28ec9aaad89bc5a295628ebb228b86435ff7ffb01e53606bd14a5ffbe325",
        "internal_sha256": "051a92d2fccefbf31341e126a9b3a5dd7f64fdb78c0ba364159c2a7ef4ca4577",
        "reason": "resolve role-specific axes while retaining complete BOX proof resolution",
    },
    "web_solver.py": {
        "upstream_sha256": "f087105930ce4809ac887dc82e0cf27f900d6ae1147946b60be5c19fa6be9841",
        "internal_sha256": "7702231f5c0572a2ce90ca8b1a669d00e50761bff5d36c8e3427cef955e9238c",
        "reason": "propagate direct-face and connected-course search completeness",
    },
    "weld_allowance.py": {
        "upstream_sha256": "b8e9db4118de528fa8c6afe08eab56633b66060f6a9255424b480615c4ac467c",
        "internal_sha256": "f46937237206429c907b54ec7fc7ea49d981dbb163eea533f8e6e5df6de1238c",
        "reason": "bind preserved hole colors and current compilation schema into allowance validation",
    },
    "writer.py": {
        "upstream_sha256": "aa295327e9ff9cf88d8c663225b9393c1cf38bdd2a9fe288e25690ec76a02964",
        "internal_sha256": "633a5fac2a74def6839f9c54e294e4231dff4f9e282a4477f52b04899fef67aa",
        "reason": "apply symmetric-hole policy, unified material-safe marks, and confirmed plate-role marks",
    },
}

ALLOWED_INTEGRATION_FILES = {
    "analysis.py",
    "compiler.py",
    "contracts.py",
    "delivery.py",
    "frontend.py",
    "manufacturing.py",
    "part_mark_layout.py",
    "provenance.py",
    "release.py",
    "solve.py",
    "validation.py",
    "view_preprocessing.py",
}


def _sha256(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def _normalized_source(path: Path) -> bytes:
    return path.read_bytes().replace(b"\r\n", b"\n")


def _matches_declared_source_digest(path: Path, expected: str) -> bool:
    raw = path.read_bytes()
    variants = {
        raw,
        raw.replace(b"\r\n", b"\n"),
        raw.replace(b"\r\n", b"\n").replace(b"\n", b"\r\n"),
    }
    return any(sha256(source).hexdigest() == expected for source in variants)


def _git(root: Path, *arguments: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(root), *arguments],
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip()


def verify_source_import(
    *,
    upstream_root: Path,
    project_root: Path,
) -> dict[str, Any]:
    upstream_root = upstream_root.resolve()
    project_root = project_root.resolve()
    upstream_package = upstream_root / "src" / "box_dxf_split"
    internal_package = project_root / "src" / "steel_dxf_split" / "box"
    upstream_commit = _git(upstream_root, "rev-parse", "HEAD")
    upstream_tag = _git(
        upstream_root,
        "describe",
        "--tags",
        "--exact-match",
        "HEAD",
    )
    missing: list[str] = []
    changed: list[dict[str, str]] = []
    patched: list[dict[str, str]] = []
    adapted: list[dict[str, str]] = []
    retired: list[dict[str, str]] = []
    matched = 0
    for name in FROZEN_SOURCE_FILES:
        upstream = upstream_package / name
        internal = internal_package / name
        if not upstream.is_file() or not internal.is_file():
            missing.append(name)
            continue
        upstream_digest = _sha256(upstream)
        internal_digest = _sha256(internal)
        declared_patch = PATCHED_SOURCE_FILES.get(name)
        if declared_patch is not None:
            if (
                _matches_declared_source_digest(
                    upstream, declared_patch["upstream_sha256"]
                )
                and _matches_declared_source_digest(
                    internal, declared_patch["internal_sha256"]
                )
            ):
                patched.append(
                    {
                        "path": name,
                        "upstream_sha256": upstream_digest,
                        "internal_sha256": internal_digest,
                        "reason": declared_patch["reason"],
                    }
                )
            else:
                changed.append(
                    {
                        "path": name,
                        "upstream_sha256": upstream_digest,
                        "internal_sha256": internal_digest,
                        "expected_upstream_sha256": declared_patch["upstream_sha256"],
                        "expected_internal_sha256": declared_patch["internal_sha256"],
                    }
                )
            continue
        if _normalized_source(upstream) != _normalized_source(internal):
            changed.append(
                {
                    "path": name,
                    "upstream_sha256": upstream_digest,
                    "internal_sha256": internal_digest,
                }
            )
            continue
        matched += 1
    for name, declared_adaptation in ADAPTED_SOURCE_FILES.items():
        upstream = upstream_package / name
        internal = internal_package / name
        if not upstream.is_file() or not internal.is_file():
            missing.append(name)
            continue
        upstream_digest = _sha256(upstream)
        internal_digest = _sha256(internal)
        if (
            _matches_declared_source_digest(
                upstream, declared_adaptation["upstream_sha256"]
            )
            and _matches_declared_source_digest(
                internal, declared_adaptation["internal_sha256"]
            )
        ):
            adapted.append(
                {
                    "path": name,
                    "upstream_sha256": upstream_digest,
                    "internal_sha256": internal_digest,
                    "reason": declared_adaptation["reason"],
                }
            )
        else:
            changed.append(
                {
                    "path": name,
                    "upstream_sha256": upstream_digest,
                    "internal_sha256": internal_digest,
                    "expected_upstream_sha256": declared_adaptation[
                        "upstream_sha256"
                    ],
                    "expected_internal_sha256": declared_adaptation[
                        "internal_sha256"
                    ],
                }
            )
    for name, declared_retirement in RETIRED_SOURCE_FILES.items():
        upstream = upstream_package / name
        internal = internal_package / name
        if not upstream.is_file():
            missing.append(name)
            continue
        upstream_digest = _sha256(upstream)
        if (
            _matches_declared_source_digest(
                upstream, declared_retirement["upstream_sha256"]
            )
            and not internal.exists()
        ):
            retired.append(
                {
                    "path": name,
                    "upstream_sha256": upstream_digest,
                    "reason": declared_retirement["reason"],
                }
            )
        else:
            changed.append(
                {
                    "path": name,
                    "upstream_sha256": upstream_digest,
                    "internal_state": "present" if internal.exists() else "absent",
                    "expected_upstream_sha256": declared_retirement[
                        "upstream_sha256"
                    ],
                    "expected_internal_state": "absent",
                }
            )
    integration_files = sorted(
        path.name
        for path in internal_package.glob("*.py")
        if path.name not in FROZEN_SOURCE_FILES
        and path.name not in ADAPTED_SOURCE_FILES
    )
    unexpected = sorted(
        name for name in integration_files if name not in ALLOWED_INTEGRATION_FILES
    )
    release_matches = (
        upstream_tag == BOX_CORE_TAG and upstream_commit == BOX_CORE_COMMIT
    )
    invalid_patch_manifest = sorted(
        set(PATCHED_SOURCE_FILES).difference(FROZEN_SOURCE_FILES)
    )
    invalid_source_manifest = sorted(
        set(FROZEN_SOURCE_FILES).intersection(ADAPTED_SOURCE_FILES)
        | set(FROZEN_SOURCE_FILES).intersection(RETIRED_SOURCE_FILES)
        | set(ADAPTED_SOURCE_FILES).intersection(RETIRED_SOURCE_FILES)
    )
    return {
        "schema": "BOX-V1-SOURCE-IMPORT-1.2",
        "tag": upstream_tag,
        "commit": upstream_commit,
        "expected_tag": BOX_CORE_TAG,
        "expected_commit": BOX_CORE_COMMIT,
        "release_matches": release_matches,
        "patchset_id": BOX_CORE_PATCHSET_ID,
        "matched": matched,
        "patched": patched,
        "adapted": adapted,
        "retired": retired,
        "missing": missing,
        "changed": changed,
        "invalid_patch_manifest": invalid_patch_manifest,
        "invalid_source_manifest": invalid_source_manifest,
        "integration_files": integration_files,
        "unexpected": unexpected,
        "ok": (
            release_matches
            and matched == len(FROZEN_SOURCE_FILES) - len(PATCHED_SOURCE_FILES)
            and len(patched) == len(PATCHED_SOURCE_FILES)
            and len(adapted) == len(ADAPTED_SOURCE_FILES)
            and len(retired) == len(RETIRED_SOURCE_FILES)
            and not missing
            and not changed
            and not invalid_patch_manifest
            and not invalid_source_manifest
            and not unexpected
        ),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Verify the internal BOX core against v1.0.0 source."
    )
    parser.add_argument("--upstream", required=True, type=Path)
    parser.add_argument(
        "--project-root",
        type=Path,
        default=Path(__file__).resolve().parents[1],
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    report = verify_source_import(
        upstream_root=args.upstream,
        project_root=args.project_root,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
