from __future__ import annotations

from hashlib import sha256
from pathlib import Path

ADAPTED_FILES = {
    "__init__.py": {
        "upstream_sha256": "6ee5fcba60ea2f6909e24ecccbab4d9bc62b392c883bb1935302d5ab05f3ce7e",
        "integrated_sha256": "a7e6fbd4c78cc6055484b2cdceec3b5676bddc3caa9c9ff815cf9e9060d2423c",
        "reason": "publish the unified BH/BOX worker API and version metadata",
    },
    "artifact_io.py": {
        "upstream_sha256": "e750f53aaeec75dad084afc52a1d07db8c4213d251edc35cbc07b5024b93f660",
        "integrated_sha256": "d78dc218f7883adf8e5e506af1de890bc2a40fc71c90003e35f842f0e3eaf49e",
        "reason": "retain Linux directory fsync while allowing Windows development tests",
    },
    "cli.py": {
        "upstream_sha256": "d7c043ff6236ad38c304a0428a0848a5a810e6f152029d8d7420f8e4c1aa8c72",
        "integrated_sha256": "ff180f408aa7f4007d1e8ae07079718022d2979bbff93da699c3bcb453ec0bda",
        "reason": "single public input-directory CLI for paired BH/BOX task publication",
    },
    "pipeline.py": {
        "upstream_sha256": "394d7ee2c877283c77f877165e174e47e7a0363921cd1e5f30b4fc2d749d2c65",
        "integrated_sha256": "dc7852b72e54c3ed7a6a3294378eb245bcc8fe86050c4dfb66cc66ec8387aadf",
        "reason": "single detection and split with atomic normal plus weld-allowance task publication",
    },
}
INTEGRATION_FILES = {
    "hole_color_policy.py",
    "part_mark_layout.py",
    "paired_output.py",
    "profile_detection.py",
    "release_evidence/box_build_contract.json",
    "release_evidence/box_release_attestation.json",
}
INTEGRATION_DIRECTORIES = {"box", "bh"}
RETIRED_FILES = {
    "batch_cli.py": {
        "upstream_sha256": "5ff3260649c27a010ee40d1ac41442c1ed19ff161a63b9019552ea83553641a9",
        "reason": "the unified directory CLI owns all mixed BH/BOX orchestration",
    },
    "weld_allowance_cli.py": {
        "upstream_sha256": "ce0040f0c9df6f5b9e7f2500e2b7a1ce7c85d828bc060b8abc1627bd4b6c440b",
        "reason": "allowance processing is an in-memory continuation of the same split task",
    },
    "weld_allowance_release.py": {
        "upstream_sha256": "4cef19afd13374dbf55a4250964c198d8430a2c7067659d1a5d2a0ff87efce82",
        "reason": "paired task validation replaces the retired independent batch release chain",
    },
}
PATCHSET_ID = (
    "bh-paired-output-hole-color-unified-part-mark-compact-label-and-"
    "uniform-scale-2026-07-24"
)
PATCHED_FILES = {
    "bh_validator.py": {
        "upstream_sha256": "85202d4fe68136fa0be8abb6f43df2e9aa020692be7ee0347896d3193b424275",
        "integrated_sha256": "1c0cbfcf75efed07bdaa75dd61702853338c8eca8f95d83b85bc8186111f3026",
        "reason": "prove compact labels, shared material-safe mark placement, and cut colors",
    },
    "bh_writer.py": {
        "upstream_sha256": "f7d602571dee1c1937e07a2dc98eb3cf382a68b7b69de8891e2b1d5fd036704d",
        "integrated_sha256": "a348a193070f8abf35e7b6dc07ccd3c0d002ae866003827f00f671ed91770b8d",
        "reason": "apply shared material-safe mark layout and symmetric-hole color policy",
    },
    "bh_release_evidence.py": {
        "upstream_sha256": "75b335c6a8f562e5ee7ddfd5094511767acd002959137db1bcf606e902000dd9",
        "integrated_sha256": "f1587fc5b0a32407343ff21ed4f545db143be83c40c1645cc81eda2f3f2ae0c7",
        "reason": "pin the regenerated 20-of-20 BH-MFG-3.1 release evidence",
    },
    "bh_constraints.py": {
        "upstream_sha256": "43085e980a1b88eae03840dc38cf532975694d4ffa2cf084bab0c761e7e00a07",
        "integrated_sha256": "c7b0a0e1228908141bdff432f1157555959a4ccb1613baf1679db81e7c98b627",
        "reason": "critical proof rejects unsupported or conflicting metric-scale recovery",
    },
    "bh_development.py": {
        "upstream_sha256": "4eb62f5bea374de7ed1113c7af7d1e683812322081050d01005456efdb697fc7",
        "integrated_sha256": "1621e4ef6c1bcfade15dd5ebd0fd2057a9d2b82b843acd0b5f2e07546461a656",
        "reason": "pure development quantization and unique cranked-candidate authorization",
    },
    "bh_extractor.py": {
        "upstream_sha256": "df9a11746027e609f3772849f9a38980b73e83a5406c376e480027f3e73837cd",
        "integrated_sha256": "484ea6f11d5baf2770b1f90e9798a4c5b0a835c614046946cbfda833173a2db1",
        "reason": "propagate profile policy, source IDs and development certificates",
    },
    "bh_geometry.py": {
        "upstream_sha256": "762ebb07d5cbb44a67ac6c52945e62eb410095da7ff6201df65ad87ca34f56f0",
        "integrated_sha256": "1515ddc4e2db56959e55fa72e9502945c2ccda6ed0e3eb3a3105437b86058ac2",
        "reason": "construct rigid and cranked source-topology development certificates",
    },
    "bh_knowledge.py": {
        "upstream_sha256": "e33994709b3e1e86bf4a74d87f5472325b90743956c592844e8fc3c8358d2023",
        "integrated_sha256": "c9830957b8c3db6b36cc563f97dc52654e88c80f499d7e7c6dbb4dbbef4e52b1",
        "reason": "declare the evidence-gated uniform metric-scale policy",
    },
    "bh_solver.py": {
        "upstream_sha256": "61cb01e1624d2d9f80c64b9d262e86891874a2a8e2aa70049e76d6336838eb70",
        "integrated_sha256": "6f615c29bfd1338ce4316e24da6b8ce09b67980572fbbeaa8b91a1d8ce3605fe",
        "reason": "normalize candidate-local geometry only after metric evidence consensus",
    },
    "bh_text.py": {
        "upstream_sha256": "db24b0c7acd34d553bef7ae2f2d2c4ed53d95fb30730b3a76a929301bba7bcf0",
        "integrated_sha256": "59cef2025157bf14a2f0f8f068ca7e9bcbf87f7116196330002790a05d4ff3fd",
        "reason": "use compact visible web, merged-flange, upper-flange and lower-flange labels",
    },
    "dxf_preview.py": {
        "upstream_sha256": "561477e41268e1672db3ccf2bacb5a3358921ac6df1e679d44e16d722751ba55",
        "integrated_sha256": "73648434c655446df14d71958dc0c9050fe158a488fc2e29f11a0f93a820884c",
        "reason": "allow the fused Windows worker to select installed CJK preview fonts",
    },
    "release_evidence/project_tekla_bh_dxf_v1.json": {
        "upstream_sha256": "6358c421fb0014482b7f75ca6bf7ecc417ecc61466887ea66582c16dd0302c55",
        "integrated_sha256": "243fa7d095cf9c402ffcb62ad03634b0e25b895c2fa3ea6af6004b1d5fdc2e34",
        "reason": "replace the obsolete 18-of-20 profile with current 20-of-20 evidence",
    },
    "weld_allowance.py": {
        "upstream_sha256": "c4804f1346bb9323ac41e9d668044113a55c5b1f34b98d0bcf7eb5e423dcbae0",
        "integrated_sha256": "f3caa461b7d5c2539fedc7348d25e2ca222bbc33cf792e1911ab8ef28dbf0fce",
        "reason": "bind preserved hole colors into the allowance invariance proof",
    },
    "bh_hypothesis.py": {
        "upstream_sha256": "5d430d08dd6cf74c7af3242ed426b43a44d0ef73be0ee5b71e7731118e46c14e",
        "integrated_sha256": "c66be31339589a91b8112ebc3c52a29e9e5bb59ea9eb0884621a71129d33ab01",
        "reason": "carry candidate-local metric-scale evidence into hypothesis search",
    },
    "bh_provenance.py": {
        "upstream_sha256": "fd02655f0936a3dcd7fb47efca4ae394fd1cf950f3ab245aa56b42aea6ace646",
        "integrated_sha256": "03b10cf78f66d0d87b01f2a081b25902439d09bbac4262e76778a0edb9ca39eb",
        "reason": "transform source provenance with the accepted uniform scale",
    },
}

ADDED_PATCH_FILES = {
    "bh_metric_scale.py": {
        "integrated_sha256": "8214cc90d83ac0c79800b89765db88218d7cab96a4cabba78c08d03bae11929b",
        "reason": "recover a uniform metric scale only from bound cross-view evidence",
    },
    "bh_project_ledger.py": {
        "integrated_sha256": "eac40f11ea4cada975a11a5a7d77d9a253a4edef1e0f1379e530fd438edb6608",
        "reason": "publish the project-level BH Excel ledger consumed by the second processing phase",
    },
}


def _canonical_bytes(path: Path) -> bytes:
    return path.read_bytes().replace(b"\r\n", b"\n").replace(b"\r", b"\n")


def _sha256(path: Path) -> str:
    return sha256(_canonical_bytes(path)).hexdigest()


def _files(root: Path, *, integrated: bool) -> dict[str, Path]:
    result: dict[str, Path] = {}
    for path in root.rglob("*"):
        if not path.is_file() or "__pycache__" in path.parts or path.suffix == ".pyc":
            continue
        relative = path.relative_to(root)
        if integrated and (
            relative.parts[0] in INTEGRATION_DIRECTORIES
            or relative.as_posix() in INTEGRATION_FILES
        ):
            continue
        result[relative.as_posix()] = path
    return result


def verify_bh_source(authority: Path, integrated: Path) -> dict[str, object]:
    source = _files(authority / "src/steel_dxf_split", integrated=False)
    target = _files(integrated / "src/steel_dxf_split", integrated=True)
    missing = sorted(set(source) - set(target) - set(RETIRED_FILES))
    unexpected = sorted(set(target) - set(source) - set(ADDED_PATCH_FILES))
    exact = 0
    adapted = 0
    patched = 0
    invalid: list[str] = []
    for relative in sorted(set(source) & set(target)):
        if relative in ADAPTED_FILES:
            contract = ADAPTED_FILES[relative]
            if (
                _sha256(source[relative]) == contract["upstream_sha256"]
                and _sha256(target[relative]) == contract["integrated_sha256"]
            ):
                adapted += 1
            else:
                invalid.append(relative)
        elif relative in PATCHED_FILES:
            contract = PATCHED_FILES[relative]
            if (
                _sha256(source[relative]) == contract["upstream_sha256"]
                and _sha256(target[relative]) == contract["integrated_sha256"]
            ):
                patched += 1
            else:
                invalid.append(relative)
        elif _canonical_bytes(target[relative]) == _canonical_bytes(source[relative]):
            exact += 1
        else:
            invalid.append(relative)
    added: list[str] = []
    for relative, contract in ADDED_PATCH_FILES.items():
        target_path = target.get(relative)
        if (
            relative not in source
            and target_path is not None
            and _sha256(target_path) == contract["integrated_sha256"]
        ):
            added.append(relative)
        else:
            invalid.append(relative)
    retired: list[str] = []
    for relative, contract in RETIRED_FILES.items():
        source_path = source.get(relative)
        if (
            source_path is not None
            and _sha256(source_path) == contract["upstream_sha256"]
            and relative not in target
        ):
            retired.append(relative)
        else:
            invalid.append(relative)
    return {
        "exact": exact,
        "adapted": adapted,
        "declared_adapted_files": sorted(ADAPTED_FILES),
        "patched": patched,
        "patchset_id": PATCHSET_ID,
        "declared_patch_files": sorted(PATCHED_FILES),
        "added": len(added),
        "declared_added_files": sorted(ADDED_PATCH_FILES),
        "retired": len(retired),
        "declared_retired_files": sorted(RETIRED_FILES),
        "missing": len(missing),
        "unexpected": len(unexpected),
        "unexpected_files": unexpected,
        "invalid_adaptations": invalid,
    }
