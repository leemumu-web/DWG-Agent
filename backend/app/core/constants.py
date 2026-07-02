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

ALLOWED_UPLOAD_EXTENSIONS = {".dwg"}
