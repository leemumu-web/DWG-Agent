import importlib.util
import json
import subprocess
import sys
from pathlib import Path

from steel_dxf_split.process_control import IsolatedProcessResult

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "run_atomic_test_matrix", ROOT / "scripts" / "bh" / "run_atomic_test_matrix.py"
)
assert SPEC is not None and SPEC.loader is not None
ATOMIC_MATRIX = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(ATOMIC_MATRIX)


def test_atomic_matrix_default_modules_cover_every_test_module() -> None:
    discovered = sorted(
        str(path.relative_to(ROOT)) for path in (ROOT / "tests").glob("test_*.py")
    )
    assert ATOMIC_MATRIX.DEFAULT_TEST_MODULES == discovered


def test_atomic_worker_environment_can_import_project_scripts() -> None:
    pythonpath = ATOMIC_MATRIX.build_test_environment()["PYTHONPATH"].split(
        ATOMIC_MATRIX.os.pathsep
    )
    assert str(ROOT) in pythonpath
    assert str(ROOT / "src") in pythonpath


def test_atomic_node_delegates_to_platform_process_control(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def fake_run(command, timeout_seconds, *, cwd, env):
        captured.update(
            command=command,
            timeout=timeout_seconds,
            cwd=cwd,
            env=env,
        )
        return IsolatedProcessResult(
            124,
            "timed out",
            0.5,
            True,
            active_supervision_seconds=0.4,
            unbudgeted_wall_seconds=0.1,
        )

    monkeypatch.setattr(ATOMIC_MATRIX, "run_isolated_process", fake_run)
    environment = {"PYTHONUTF8": "1"}

    result = ATOMIC_MATRIX.run_node(
        "tests/test_example.py::test_example",
        environment,
        7,
    )

    assert result == (124, "timeout", "timed out", 0.5, 0.4, 0.1)
    assert captured["timeout"] == 7
    assert captured["cwd"] == ROOT
    assert captured["env"] == environment


def test_atomic_matrix_report_uses_current_release_schema(tmp_path: Path) -> None:
    fingerprint = "a" * 64
    report = ATOMIC_MATRIX.write_report(
        tmp_path / "report.json",
        ["tests/test_example.py::test_example"],
        {
            "tests/test_example.py::test_example": {
                "node": "tests/test_example.py::test_example",
                "status": "passed",
            }
        },
        workspace_fingerprint=fingerprint,
    )

    assert report["version"] == "1.5.2"
    assert report["schema"] == "BH-ATOMIC-TEST-MATRIX-1.3"
    assert report["workspace_fingerprint"] == fingerprint
    assert not (tmp_path / ".report.json.tmp").exists()


def test_atomic_workspace_fingerprint_tracks_release_inputs_only(
    tmp_path: Path,
) -> None:
    source = tmp_path / "src" / "steel_dxf_split" / "module.py"
    source.parent.mkdir(parents=True)
    source.write_text("VALUE = 1\n", encoding="utf-8")

    first = ATOMIC_MATRIX.workspace_fingerprint(tmp_path)
    output = tmp_path / "output" / "ignored.json"
    output.parent.mkdir()
    output.write_text("generated", encoding="utf-8")
    assert ATOMIC_MATRIX.workspace_fingerprint(tmp_path) == first

    source.write_text("VALUE = 2\n", encoding="utf-8")
    assert ATOMIC_MATRIX.workspace_fingerprint(tmp_path) != first


def test_atomic_resume_reuses_only_the_same_workspace_fingerprint(
    tmp_path: Path,
) -> None:
    node = "tests/test_example.py::test_example"
    report_path = tmp_path / "report.json"
    result = {"node": node, "status": "passed"}
    ATOMIC_MATRIX.write_report(
        report_path,
        [node],
        {node: result},
        workspace_fingerprint="a" * 64,
    )

    assert ATOMIC_MATRIX.load_resumable_results(
        report_path,
        [node],
        workspace_fingerprint="a" * 64,
    ) == {node: result}
    assert ATOMIC_MATRIX.load_resumable_results(
        report_path,
        [node],
        workspace_fingerprint="b" * 64,
    ) == {}

    payload = json.loads(report_path.read_text(encoding="utf-8"))
    payload["schema"] = "BH-ATOMIC-TEST-MATRIX-1.2"
    report_path.write_text(json.dumps(payload), encoding="utf-8")
    assert ATOMIC_MATRIX.load_resumable_results(
        report_path,
        [node],
        workspace_fingerprint="a" * 64,
    ) == {}


def test_node_collector_can_write_machine_readable_node_list(tmp_path: Path) -> None:
    output = tmp_path / "nodes.json"
    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "bh" / "collect_pytest_nodes.py"),
            "--output",
            str(output),
            "tests/bh_v152/test_version_contract.py",
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload == [
        "tests/bh_v152/test_version_contract.py::test_version_sources_are_identical",
        "tests/bh_v152/test_version_contract.py::test_release_evidence_binds_current_compiler_and_twenty_sources",
        "tests/bh_v152/test_version_contract.py::test_candidate_bootstrap_allows_only_the_declared_prior_ontology",
        "tests/bh_v152/test_version_contract.py::test_production_resolver_never_bootstraps_from_prior_ontology",
        "tests/bh_v152/test_version_contract.py::test_dialect_fingerprint_binds_hidden_projection_semantics",
        "tests/bh_v152/test_version_contract.py::test_release_payload_is_derived_from_a_complete_candidate_summary",
        "tests/bh_v152/test_version_contract.py::test_release_payload_rejects_an_incomplete_candidate_gate",
        "tests/bh_v152/test_version_contract.py::test_release_payload_recomputes_the_required_mutation_gate",
    ]


def test_node_collector_can_collect_tests_that_import_project_scripts(
    tmp_path: Path,
) -> None:
    output = tmp_path / "nodes.json"
    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "bh" / "collect_pytest_nodes.py"),
            "--output",
            str(output),
            "tests/bh_v152/test_layered_release_verifier.py",
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert len(json.loads(output.read_text(encoding="utf-8"))) == 4


def test_node_collector_does_not_publish_partial_list_on_collection_error(
    tmp_path: Path,
) -> None:
    output = tmp_path / "nodes.json"
    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "bh" / "collect_pytest_nodes.py"),
            "--output",
            str(output),
            "tests/does_not_exist.py",
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode != 0
    assert not output.exists()
