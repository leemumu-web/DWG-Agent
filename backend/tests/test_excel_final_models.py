from __future__ import annotations

from sqlalchemy import BigInteger

from app.models.excel_final import ExcelFinalBatch, ExcelFinalComponent, ExcelFinalPart


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
