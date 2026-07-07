from __future__ import annotations

ACTIVE = "active"
DISABLED = "disabled"
DELETED = "deleted"

ROLE_SUPER_ADMIN = "super_admin"
ROLE_ADMIN = "admin"
ROLE_ENGINEER = "engineer"
ROLE_REVIEWER = "reviewer"
ROLE_OPERATOR = "operator"
ROLE_VIEWER = "viewer"
ROLE_AUDITOR = "auditor"

JOB_PENDING = "pending"
JOB_QUEUED = "queued"
JOB_RUNNING = "running"
JOB_WAITING_CAD_WORKER = "waiting_cad_worker"
JOB_VALIDATING = "validating"
JOB_NEED_REVIEW = "need_review"
JOB_SUCCEEDED = "succeeded"
JOB_FAILED = "failed"
JOB_CANCELLED = "cancelled"

PIPELINE_STUB = "local_stub"
PIPELINE_DXF = "dxf_open_source"
PIPELINE_CAD = "zwcad_worker"

# DWG→DXF pipeline
PIPELINE_DXF2DWG = "dxf2dwg_open_source"
TASK_DWG_TO_DXF = "convert_dwg_to_dxf"
TASK_DXF_TO_DWG = "convert_dxf_to_dwg"

# DWG→DXF job_steps
STEP_DOWNLOAD_SOURCE = "download_source_dwg"
STEP_RUN_ODA_CONVERT = "run_oda_convert"
STEP_PERSIST_DXF = "persist_dxf_result"

# DXF→DWG job_steps
STEP_DOWNLOAD_SOURCE_DXF = "download_source_dxf"
STEP_RUN_ODA_CONVERT_DXF = "run_oda_convert_dxf"
STEP_PERSIST_DWG = "persist_dwg_result"

# DXF→Excel pipeline
PIPELINE_DXF2EXCEL = "dxf2excel"
TASK_DXF_TO_EXCEL = "extract_dxf_to_excel"
STEP_DOWNLOAD_DXF_BATCH = "download_dxf_batch"
STEP_RUN_DXF2EXCEL = "run_dxf2excel_pipeline"
STEP_PERSIST_EXCEL = "persist_excel_result"

ALLOWED_UPLOAD_EXTENSIONS = {".dwg", ".dxf", ".zip"}
