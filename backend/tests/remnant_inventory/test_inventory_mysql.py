from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from threading import Barrier
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, delete, func, select
from sqlalchemy.orm import sessionmaker

from app.modules.files.interface import StoredFile
from app.modules.identity.interface import Role, User, user_roles
from app.modules.operations.audit.models import AuditLog
from app.modules.remnant_inventory.inventory import (
    build_original_download,
    mark_remnant_used,
    preview_file_id,
    release_remnant,
    reserve_remnant,
    search_remnants,
    update_remnant,
)
from app.modules.remnant_inventory.materials import resolve_or_create_material
from app.modules.remnant_inventory.models import (
    Remnant,
    RemnantImportBatch,
    RemnantImportItem,
    RemnantMaterial,
)
from app.platform.http.exceptions import AppHTTPException

MYSQL_URL = os.getenv("MYSQL_INTEGRATION_DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not MYSQL_URL,
    reason="Set MYSQL_INTEGRATION_DATABASE_URL to run live MySQL remnant concurrency tests.",
)


def test_two_workers_resolve_one_material_row() -> None:
    assert MYSQL_URL is not None
    engine = create_engine(MYSQL_URL, pool_pre_ping=True)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    code = f"RACE-{uuid4().hex[:12]}".upper()
    barrier = Barrier(2)

    def create_from_worker(_worker_slot: int) -> tuple[int, bool]:
        with factory() as session:
            barrier.wait(timeout=10)
            material, created = resolve_or_create_material(
                session, code=code, actor_id=None
            )
            session.commit()
            return material.id, created

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(create_from_worker, (1, 2)))
        assert len({material_id for material_id, _created in results}) == 1
        assert sum(created for _material_id, created in results) == 1
        with factory() as check:
            count = check.scalar(
                select(func.count()).select_from(RemnantMaterial).where(
                    RemnantMaterial.code == code
                )
            )
            assert count == 1
    finally:
        with factory() as cleanup:
            cleanup.execute(delete(RemnantMaterial).where(RemnantMaterial.code == code))
            cleanup.commit()
        engine.dispose()


def test_two_workers_get_one_reservation_and_lifecycle_remains_consistent() -> None:
    assert MYSQL_URL is not None
    engine = create_engine(MYSQL_URL, pool_pre_ping=True)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    token = uuid4().hex
    created: dict[str, list[int] | int] = {"user_ids": [], "file_ids": []}

    try:
        with factory() as setup:
            role = setup.scalar(select(Role).where(Role.code == "remnant_worker"))
            assert role is not None
            workers = [
                User(
                    username=f"remnant-mysql-{index}-{token}",
                    real_name=f"MySQL Worker {index}",
                    password_hash="acceptance-only",
                    roles=[role],
                )
                for index in range(2)
            ]
            setup.add_all(workers)
            setup.flush()
            material = RemnantMaterial(
                code=f"MYSQL-{token[:12]}", family_code=f"MYSQL-{token[:6]}", enabled=True
            )
            source = StoredFile(
                bucket="dwg-original",
                storage_key=f"acceptance/{token}.dwg",
                original_name=f"acceptance-{token}.dwg",
                file_ext=".dwg",
                size_bytes=1024,
                sha256=token.ljust(64, "0"),
                status="available",
                uploaded_by=workers[0].id,
            )
            preview = StoredFile(
                bucket="dxf-derived",
                storage_key=f"acceptance/{token}.dxf",
                original_name=f"acceptance-{token}.dxf",
                file_ext=".dxf",
                size_bytes=1024,
                sha256=token.ljust(64, "1"),
                status="available",
            )
            setup.add_all([material, source, preview])
            setup.flush()
            batch = RemnantImportBatch(created_by=workers[0].id, total_count=1, confirmed_count=1)
            setup.add(batch)
            setup.flush()
            item = RemnantImportItem(
                batch_id=batch.id,
                source_file_id=source.id,
                dxf_file_id=preview.id,
                source_sha256=source.sha256,
                source_ext=".dwg",
                status="confirmed",
            )
            setup.add(item)
            setup.flush()
            remnant = Remnant(
                import_item_id=item.id,
                source_file_id=source.id,
                dxf_file_id=preview.id,
                source_sha256=source.sha256,
                thickness_mm="12.000",
                material_id=material.id,
                project_no=f"MYSQL-{token[:8]}",
                status="available",
                imported_by=workers[0].id,
                confirmed_by=workers[0].id,
                confirmed_at=datetime.now(UTC),
            )
            setup.add(remnant)
            setup.commit()
            created.update(
                user_ids=[worker.id for worker in workers],
                file_ids=[source.id, preview.id],
                material_id=material.id,
                batch_id=batch.id,
                item_id=item.id,
                remnant_id=remnant.id,
            )

        with factory() as lookup:
            exact = search_remnants(
                lookup,
                material_id=int(created["material_id"]),
                thickness_mm="12",
                include_family=False,
            )
            assert [row.id for row in exact.items] == [created["remnant_id"]]
            actor = lookup.get(User, int(created["user_ids"][1]))
            assert actor is not None
            assert preview_file_id(lookup, int(created["remnant_id"]), actor=actor) == created[
                "file_ids"
            ][1]

        gate = Barrier(2)

        def compete(actor_id: int) -> tuple[int, str, int | None]:
            with factory() as session:
                actor = session.get(User, actor_id)
                assert actor is not None
                gate.wait()
                try:
                    row = reserve_remnant(
                        session, int(created["remnant_id"]), actor=actor, expected_version=1
                    )
                    session.commit()
                    return actor_id, "reserved", row.reserved_by
                except AppHTTPException as exc:
                    session.rollback()
                    return actor_id, exc.detail["code"], exc.detail.get("details", {}).get(
                        "reserved_by"
                    )

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(compete, created["user_ids"]))
        assert sorted(result[1] for result in results) == [
            "REMNANT_ALREADY_RESERVED",
            "reserved",
        ]
        winner = next(actor_id for actor_id, status, _ in results if status == "reserved")
        loser = next(actor_id for actor_id, status, _ in results if status != "reserved")
        assert next(occupant for _, status, occupant in results if status != "reserved") == winner

        with factory() as lifecycle:
            winner_user = lifecycle.get(User, winner)
            loser_user = lifecycle.get(User, loser)
            assert winner_user is not None and loser_user is not None
            original = build_original_download(
                lifecycle, int(created["remnant_id"]), actor=winner_user
            )
            assert original.file_id == created["file_ids"][0]
            assert original.file_ext == ".dwg"
            with pytest.raises(AppHTTPException):
                build_original_download(lifecycle, int(created["remnant_id"]), actor=loser_user)
            release_remnant(lifecycle, int(created["remnant_id"]), actor=winner_user)
            lifecycle.commit()

        mutation_gate = Barrier(2)
        editor_id = int(created["user_ids"][0])
        reserver_id = int(created["user_ids"][1])

        def reserve_again() -> str:
            with factory() as session:
                actor = session.get(User, reserver_id)
                assert actor is not None
                mutation_gate.wait()
                try:
                    reserve_remnant(
                        session, int(created["remnant_id"]), actor=actor, expected_version=3
                    )
                    session.commit()
                    return "reserved"
                except AppHTTPException as exc:
                    session.rollback()
                    return exc.detail["code"]

        def edit_available() -> str:
            with factory() as session:
                actor = session.get(User, editor_id)
                assert actor is not None
                mutation_gate.wait()
                try:
                    update_remnant(
                        session,
                        int(created["remnant_id"]),
                        actor=actor,
                        project_no="race-edit",
                    )
                    session.commit()
                    return "edited"
                except AppHTTPException as exc:
                    session.rollback()
                    return exc.detail["code"]

        with ThreadPoolExecutor(max_workers=2) as pool:
            reserve_future = pool.submit(reserve_again)
            edit_future = pool.submit(edit_available)
            mutation_results = {reserve_future.result(), edit_future.result()}
        assert mutation_results in (
            {"reserved", "REMNANT_LOCKED"},
            {"edited", "REMNANT_STATE_CONFLICT"},
        )

        with factory() as lifecycle:
            winner_user = lifecycle.get(User, editor_id)
            loser_user = lifecycle.get(User, reserver_id)
            row = lifecycle.get(Remnant, int(created["remnant_id"]))
            assert winner_user is not None and loser_user is not None and row is not None
            if row.status == "available":
                assert row.project_no == "race-edit"
                reserve_remnant(
                    lifecycle, row.id, actor=loser_user, expected_version=row.version
                )
            mark_remnant_used(lifecycle, int(created["remnant_id"]), actor=loser_user)
            lifecycle.commit()
            with pytest.raises(AppHTTPException) as locked:
                update_remnant(
                    lifecycle,
                    int(created["remnant_id"]),
                    actor=winner_user,
                    project_no="must-not-change",
                )
            assert locked.value.detail["code"] == "REMNANT_LOCKED"
    finally:
        if created.get("remnant_id"):
            with engine.begin() as cleanup:
                user_ids = list(created["user_ids"])
                cleanup.execute(delete(AuditLog).where(AuditLog.actor_user_id.in_(user_ids)))
                cleanup.execute(delete(Remnant).where(Remnant.id == created["remnant_id"]))
                cleanup.execute(
                    delete(RemnantImportItem).where(RemnantImportItem.id == created["item_id"])
                )
                cleanup.execute(
                    delete(RemnantImportBatch).where(RemnantImportBatch.id == created["batch_id"])
                )
                cleanup.execute(
                    delete(RemnantMaterial).where(RemnantMaterial.id == created["material_id"])
                )
                cleanup.execute(delete(StoredFile).where(StoredFile.id.in_(created["file_ids"])))
                cleanup.execute(delete(user_roles).where(user_roles.c.user_id.in_(user_ids)))
                cleanup.execute(delete(User).where(User.id.in_(user_ids)))
        engine.dispose()
