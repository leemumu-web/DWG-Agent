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

# DXF 转换任务类型（spec §14, Stage 3）。task_type pattern: ^[a-z][a-z0-9_]+$
TASK_DWG_TO_DXF = "convert_dwg_to_dxf"

# DXF 转换 job_steps 名称（spec §13.4：每步写 job_steps）
STEP_DOWNLOAD_SOURCE = "download_source_dwg"
STEP_RUN_ODA_CONVERT = "run_oda_convert"
STEP_PERSIST_DXF = "persist_dxf_result"

ALLOWED_UPLOAD_EXTENSIONS = {".dwg"}
