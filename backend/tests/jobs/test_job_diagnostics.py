import json

from app.modules.jobs.diagnostics import build_job_diagnostics
from app.modules.jobs.models import Job, JobStep


def test_job_diagnostics_whitelists_business_facts_and_hides_backend_details(db):
    job = Job(
        task_type="convert_dwg_to_dxf",
        precision_level="normal",
        pipeline="dxf_open_source",
        status="running",
        attempt=2,
        progress=70,
        params_json={"file_id": 81},
        progress_data={
            "phase": "/tmp/private/oda_result_ready",
            "phase_label": "ODA 已返回",
            "message": "Traceback in /tmp/private/worker.py",
            "progress_basis": "Traceback:/app/private",
        },
    )
    db.add(job)
    db.flush()
    db.add(
        JobStep(
            job_id=job.id,
            attempt=2,
            step_name="run_oda_convert",
            worker_name="private-worker-01",
            status="failed",
            input_json={
                "file_id": 81,
                "version": "ACAD2013",
                "source": "/tmp/private/source.dwg",
            },
            output_json={
                "duration_seconds": 1.25,
                "target": "/tmp/private/result.dxf",
                "stdout": "secret",
                "entity_counts": {
                    "LINE": 12,
                    "private_path": "/tmp/private/result.dxf",
                    "nested": {"stderr": "secret"},
                },
            },
            error_message="Traceback: SQLAlchemy Exception at /app/private/worker.py",
        )
    )
    db.commit()

    diagnostics = build_job_diagnostics(db, job)

    assert diagnostics["current_phase"]["label"] == "ODA 已返回"
    assert diagnostics["current_phase"]["message"] == "ODA 已返回"
    assert diagnostics["current_phase"]["code"] == "running"
    assert diagnostics["current_phase"]["basis"] == "confirmed_state"
    assert diagnostics["logs"][0]["details"] == [
        {"key": "file_id", "label": "源文件编号", "value": 81},
        {"key": "version", "label": "目标版本", "value": "ACAD2013"},
        {"key": "duration_seconds", "label": "耗时秒数", "value": 1.25},
        {"key": "entity_counts", "label": "DXF 实体分类", "value": {"LINE": 12}},
    ]
    assert diagnostics["logs"][0]["message"] == (
        "CAD 格式转换未完成，请核对源文件后重试。"
    )
    serialized = json.dumps(diagnostics, ensure_ascii=False, default=str)
    for forbidden in (
        "/tmp/",
        "/app/",
        "Traceback",
        "SQLAlchemy",
        "private-worker",
        "stdout",
        "source.dwg",
        "result.dxf",
    ):
        assert forbidden not in serialized
