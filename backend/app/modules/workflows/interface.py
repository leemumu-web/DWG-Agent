"""Public workflow boundary for other business modules.

Calling contract (CONTEXT.md: Interface must document invariants, error
modes, ordering and configuration):

- ``bind_stage_job`` attaches a Job to a stage and moves the run to
  ``running``; call it only after the input batch has been frozen and only
  for a stage declared by the run's template. It rejects binding on a
  terminal workflow (409) and unknown stages (422).
- ``sync_workflow_from_jobs`` is a read-only projection replay: call
  ``workflow_needs_sync`` first and only sync when drift is detected. It
  skips stages whose bound ``job.attempt`` no longer matches the stage
  generation, and only accepts succeeded Results whose
  ``result_json["job_attempt"]`` equals the current attempt — stale
  generations never enter the projection.
- ``find_frozen_input_reference`` / ``read_verified_input_object`` /
  ``production_file_reference_exists`` back the files deletion guard: they
  expose only frozen-manifest identifiers, so a file still referenced by a
  frozen batch cannot be physically removed.
- ``attach_artifact`` validates ``artifact_type`` against the stage
  capability whitelist (422 ``WORKFLOW_ARTIFACT_TYPE_INVALID``) and enforces
  the artifact reference rules before registering the artifact.
- ``create_workflow``/``start_workflow``/``cancel_workflow``/
  ``complete_manual_stage``/``recompute_workflow`` are lifecycle entry
  points; ``recompute_workflow`` never commits — callers own the transaction
  boundary.
"""

from app.modules.workflows.access import (
    find_production_file_workflow_id,
    production_file_reference_exists,
)
from app.modules.workflows.artifacts import attach_artifact
from app.modules.workflows.intake.registration import (
    FrozenInputReference,
    find_frozen_input_reference,
    read_verified_input_object,
)
from app.modules.workflows.job_sync import bind_stage_job, sync_workflow_from_jobs
from app.modules.workflows.lifecycle import (
    cancel_workflow,
    complete_manual_stage,
    create_workflow,
    get_workflow_or_404,
    recompute_workflow,
    start_workflow,
)
from app.modules.workflows.models import (
    WorkflowArtifact,
    WorkflowBatchExport,
    WorkflowInputBatch,
    WorkflowInputItem,
    WorkflowRetentionExport,
    WorkflowRun,
    WorkflowStageRun,
)
from app.modules.workflows.templates import list_workflow_templates

__all__ = [
    "FrozenInputReference",
    "WorkflowArtifact",
    "WorkflowBatchExport",
    "WorkflowInputBatch",
    "WorkflowInputItem",
    "WorkflowRetentionExport",
    "WorkflowRun",
    "WorkflowStageRun",
    "attach_artifact",
    "bind_stage_job",
    "cancel_workflow",
    "complete_manual_stage",
    "create_workflow",
    "find_frozen_input_reference",
    "find_production_file_workflow_id",
    "production_file_reference_exists",
    "get_workflow_or_404",
    "list_workflow_templates",
    "read_verified_input_object",
    "recompute_workflow",
    "start_workflow",
    "sync_workflow_from_jobs",
]
