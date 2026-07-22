from __future__ import annotations

from sqlalchemy import JSON, BigInteger

from app.modules.excel_processing.models import (
    ExcelFinalBatch,
    ExcelFinalComponent,
    ExcelFinalPart,
)


def test_excel_final_identifiers_use_platform_bigint_type():
    for column in (
        ExcelFinalBatch.__table__.c.id,
        ExcelFinalBatch.__table__.c.job_id,
        ExcelFinalBatch.__table__.c.file_id,
        ExcelFinalPart.__table__.c.id,
        ExcelFinalPart.__table__.c.batch_id,
        ExcelFinalComponent.__table__.c.id,
        ExcelFinalComponent.__table__.c.batch_id,
    ):
        assert isinstance(column.type, BigInteger), column


def test_excel_final_batch_has_database_ownership_relations():
    batch_table = ExcelFinalBatch.__table__
    foreign_keys = {
        (fk.parent.name, fk.column.table.name, fk.ondelete) for fk in batch_table.foreign_keys
    }

    assert ("job_id", "jobs", "CASCADE") in foreign_keys
    assert ("file_id", "files", "SET NULL") in foreign_keys
    assert any(
        constraint.name == "uq_excel_final_batches_job_id" for constraint in batch_table.constraints
    )


def test_excel_final_identifiers_accept_real_world_string_lengths():
    assert ExcelFinalPart.__table__.c.component_no.type.length == 512
    assert ExcelFinalPart.__table__.c.part_type.type.length == 128
    assert ExcelFinalPart.__table__.c.part_no.type.length == 255
    assert ExcelFinalPart.__table__.c.profile_spec.type.length == 255
    assert ExcelFinalPart.__table__.c.spec.type.length == 128
    assert ExcelFinalComponent.__table__.c.component_no.type.length == 512


def test_excel_final_part_persists_canonical_identity_and_quality_fields():
    table = ExcelFinalPart.__table__.c

    assert table.import_component_no.type.length == 512
    assert table.import_part_no.type.length == 255
    assert table.source_batch.type.length == 255
    assert table.team.type.length == 128
    assert table.original_qty.nullable is True
    assert table.density_source.type.length == 255
    assert table.material_utilization.nullable is True
    assert table.weight_validation.type.length == 32


def test_excel_final_batch_persists_bounded_quality_summary_defaults():
    table = ExcelFinalBatch.__table__.c

    assert table.quality_status.type.length == 32
    assert table.quality_status.default.arg == "ok"
    assert table.warning_count.default.arg == 0
    assert table.severe_warning_count.default.arg == 0
    assert isinstance(table.report_summary.type, JSON)
