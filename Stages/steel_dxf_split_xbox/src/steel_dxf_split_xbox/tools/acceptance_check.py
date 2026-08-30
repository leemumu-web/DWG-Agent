"""Run the 20-pair XBOX release acceptance gate against the packaged CLI.

Usage (from any cwd, with this package importable):
    python -m steel_dxf_split_xbox.tools.acceptance_check \
        --corpus-root "F:/CAD_Agent/5、XBOX拆分图/XBOX图纸" \
        --work-root "<scratch dir>"

The gate re-pins xbox_release_attestation.json only after every assertion
passes on the full 20 pairs; sample DXFs never enter Git.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

from ..compiler import XBOX_REPORT_SCHEMA
from ..contracts import member_name
from ..pairing import allowance_increment
from ..release import write_xbox_release_attestation

GATE_PATH = Path(__file__).resolve().parent / "acceptance_gate.json"
PACKAGE_ROOT = Path(__file__).resolve().parents[1]
STAGE_ROOT = PACKAGE_ROOT.parents[1]
WING = "p={member}\\U+7FFC"
WEB = "p={member}\\U+8179"


def _gate_fingerprint() -> str:
    payload = json.loads(GATE_PATH.read_text(encoding="utf-8"))
    encoded = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _plates(path: Path) -> list[tuple[float, float]]:
    """Sorted (length, width) bounding boxes of closed material outlines.

    Lengths are bucketed to 0.1mm for sort stability so sub-tolerance
    rounding noise (e.g. 11520.720 vs 11520.721) cannot flip the order.
    """

    import ezdxf

    document = ezdxf.readfile(path)
    plates: list[tuple[float, float]] = []
    for entity in document.modelspace():
        if entity.dxftype() != "LWPOLYLINE":
            continue
        points = list(entity.get_points("xy"))
        xs = [float(point[0]) for point in points]
        ys = [float(point[1]) for point in points]
        plates.append((max(xs) - min(xs), max(ys) - min(ys)))
    return sorted(plates, key=lambda plate: (round(plate[0], 1), plate[1]))


def _labels(path: Path) -> list[str]:
    import ezdxf

    document = ezdxf.readfile(path)
    return sorted(
        entity.dxf.text
        for entity in document.modelspace()
        if entity.dxftype() == "TEXT"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus-root", required=True, type=Path)
    parser.add_argument("--work-root", required=True, type=Path)
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="只跑前 N 组（冒烟用）；正式签发必须跑全 20 组。",
    )
    args = parser.parse_args(argv)

    corpus_root = args.corpus_root
    manifest_path = corpus_root / "XBOX配对清单.json"
    manifest_bytes = manifest_path.read_bytes()
    manifest_sha256 = hashlib.sha256(manifest_bytes).hexdigest()
    manifest = json.loads(manifest_bytes.decode("utf-8"))

    pairs = list(manifest["pairs"])
    if args.limit is not None:
        pairs = pairs[: args.limit]
    work_root = args.work_root
    input_dir = work_root / "input"
    output_dir = work_root / "output"
    for directory in (input_dir, output_dir):
        if directory.exists():
            shutil.rmtree(directory)
    input_dir.mkdir(parents=True)

    for pair in pairs:
        shutil.copyfile(
            corpus_root / pair["before"],
            input_dir / Path(pair["before"]).name,
        )

    environment = dict(os.environ)
    source_root = str(STAGE_ROOT / "src")
    environment["PYTHONPATH"] = (
        source_root + os.pathsep + environment["PYTHONPATH"]
        if environment.get("PYTHONPATH")
        else source_root
    )
    # Bootstrap attestation: the packaged CLI refuses to run without a
    # verified release; the gate itself is the authority here, so pin the
    # current implementation into a scratch attestation for the CLI run and
    # only write the real one into the package after every assertion passes.
    bootstrap = work_root / "bootstrap_attestation.json"
    write_xbox_release_attestation(
        bootstrap,
        manifest_sha256=manifest_sha256,
        gate_fingerprint=_gate_fingerprint(),
    )
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "steel_dxf_split_xbox.cli",
            str(input_dir),
            "--output-dir",
            str(output_dir),
            "--authorize-project-tekla-xbox-dxf-v1",
            "--xbox-release-attestation",
            str(bootstrap),
        ],
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        print(f"FATAL: CLI exit {completed.returncode}\n{completed.stdout[-2000:]}")
        return 1
    payload = json.loads(completed.stdout)
    if payload.get("schema") != XBOX_REPORT_SCHEMA:
        print(f"FATAL: unexpected batch schema {payload.get('schema')!r}")
        return 1
    if payload.get("success_count") != len(pairs) or payload.get("rejected_count") != 0:
        print(
            "FATAL: counts mismatch: "
            f"success={payload.get('success_count')} rejected={payload.get('rejected_count')}"
        )
        return 1

    failures: list[str] = []
    gate = json.loads(GATE_PATH.read_text(encoding="utf-8"))
    tolerance = float(gate["geometry_assertions"]["tolerance_mm"])
    for pair in pairs:
        member = member_name(Path(pair["before"]))
        answer = corpus_root / pair["after"]
        task_dir = output_dir / "auto_accepted" / "xbox" / member
        normal = task_dir / f"{member}_正常拆板.dxf"
        weld = task_dir / f"{member}_余量增长.dxf"
        report = task_dir / f"{member}_report.json"
        missing = [
            artifact.name
            for artifact in (normal, weld, report)
            if not artifact.is_file()
        ]
        if missing:
            failures.append(f"{member}: missing artifacts {missing}")
            continue

        expected_plates = _plates(answer)
        actual_plates = _plates(normal)
        if len(expected_plates) != len(actual_plates):
            failures.append(
                f"{member}: plate count {len(actual_plates)} != {len(expected_plates)}"
            )
            continue
        for index, (expected, actual) in enumerate(
            zip(expected_plates, actual_plates)
        ):
            if any(abs(e - a) > tolerance for e, a in zip(expected, actual)):
                failures.append(
                    f"{member}: plate {index} {actual} != {expected}"
                )

        expected_labels = sorted(
            [
                WING.format(member=member),
                WEB.format(member=member),
            ]
        )
        actual_labels = _labels(normal)
        if actual_labels != expected_labels:
            failures.append(
                f"{member}: labels {actual_labels!r} != {expected_labels!r}"
            )

        weld_plates = _plates(weld)
        if len(weld_plates) != len(actual_plates):
            failures.append(
                f"{member}: weld plate count {len(weld_plates)} != "
                f"{len(actual_plates)}"
            )
            continue
        for index, (normal_plate, weld_plate) in enumerate(
            zip(actual_plates, weld_plates)
        ):
            length, width = normal_plate
            weld_length, weld_width = weld_plate
            if abs(weld_width - width) > tolerance:
                failures.append(
                    f"{member}: weld plate {index} width {weld_width} != {width}"
                )
                continue
            expected_delta = allowance_increment(length)
            actual_delta = round(weld_length - length, 3)
            if abs(actual_delta - expected_delta) > tolerance:
                failures.append(
                    f"{member}: weld plate {index} length {length} extension "
                    f"{actual_delta} != {expected_delta}"
                )

    summary = {
        "schema": "XBOX-ACCEPTANCE-RUN-1.0",
        "manifest_sha256": manifest_sha256,
        "gate_fingerprint": _gate_fingerprint(),
        "pair_count": len(pairs),
        "full_gate": args.limit is None,
        "failure_count": len(failures),
        "failures": failures,
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if failures:
        return 1

    if args.limit is None:
        attestation = PACKAGE_ROOT / "release_evidence" / "xbox_release_attestation.json"
        write_xbox_release_attestation(
            attestation,
            manifest_sha256=manifest_sha256,
            gate_fingerprint=summary["gate_fingerprint"],
        )
        print(f"attestation written: {attestation}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
