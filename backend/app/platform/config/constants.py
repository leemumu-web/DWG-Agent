"""Cross-cutting status, role, pipeline and task constants."""

from __future__ import annotations

ACTIVE = "active"
DISABLED = "disabled"
DELETED = "deleted"

ROLE_SUPER_ADMIN = "super_admin"
ROLE_ADMIN = "admin"
ROLE_OPERATOR = "operator"
ROLE_VIEWER = "viewer"

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
PIPELINE_REMNANT_CONVERT = "remnant_convert"
PIPELINE_REMNANT_PARSE = "remnant_parse"
TASK_REMNANT_CONVERT = "convert_remnant_dwg"
TASK_REMNANT_PARSE = "parse_remnant_drawing"

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

# Steel DXF classification pipeline
PIPELINE_STEEL_DXF_CLASSIFIER = "steel_dxf_classifier"
TASK_STEEL_DXF_CLASSIFICATION = "classify_steel_dxf"
STEP_STAGE_CLASSIFIER_INPUT = "stage_classifier_input"
STEP_RUN_STEEL_DXF_CLASSIFIER = "run_steel_dxf_classifier"
STEP_PERSIST_CLASSIFICATION = "persist_classification_outputs"

# Steel DXF split pipeline
PIPELINE_STEEL_DXF_SPLIT = "steel_dxf_split"
TASK_STEEL_DXF_SPLIT = "split_steel_dxf"
STEP_RUN_STEEL_DXF_SPLIT = "run_steel_dxf_split"
STEP_VALIDATE_STEEL_DXF_SPLIT = "validate_steel_dxf_split"
STEP_PERSIST_STEEL_DXF_SPLIT = "persist_steel_dxf_split"

# Excel→final part-list pipeline (excel_final)
PIPELINE_EXCEL_FINAL = "excel_final"
TASK_EXCEL_FINAL = "process_excel_final"
STEP_DOWNLOAD_EXCEL_SOURCE = "download_excel_source"
STEP_RUN_EXCEL_FINAL = "run_excel_final_pipeline"
STEP_IMPORT_PARTS_DB = "import_parts_to_db"
STEP_PERSIST_EXCEL_FINAL = "persist_excel_final_result"

# Short-lived HttpOnly access-token cookie used only by the EventSource endpoint.
JOB_EVENTS_COOKIE_NAME = "dwg_sse_token"

EXCEL_FILE_EXTENSIONS = frozenset({".xls", ".xlsx", ".xlsm"})
ALLOWED_UPLOAD_EXTENSIONS = {".dwg", ".dxf", ".zip", *EXCEL_FILE_EXTENSIONS}
