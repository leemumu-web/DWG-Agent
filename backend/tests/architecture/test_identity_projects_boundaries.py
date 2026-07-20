from __future__ import annotations

import ast
import importlib
from pathlib import Path

import pytest
from sqlalchemy.orm import Session

from app.platform.http.exceptions import AppHTTPException

REPO_ROOT = Path(__file__).resolve().parents[3]
APP_ROOT = REPO_ROOT / "backend" / "app"

IDENTITY_TABLES = {
    "sys_permissions",
    "sys_role_permissions",
    "sys_roles",
    "sys_user_roles",
    "sys_users",
    "token_blacklist",
}
PROJECT_TABLES = {"drawing_versions", "drawings", "project_members", "projects"}


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names


def test_identity_interface_owns_exact_rbac_and_token_tables() -> None:
    identity = importlib.import_module("app.modules.identity.interface")

    owned = {
        identity.User.__table__.name,
        identity.Role.__table__.name,
        identity.Permission.__table__.name,
        identity.TokenBlacklist.__table__.name,
        identity.user_roles.name,
        identity.role_permissions.name,
    }

    assert owned == IDENTITY_TABLES


def test_projects_interface_owns_exact_catalog_tables() -> None:
    projects = importlib.import_module("app.modules.projects.interface")

    owned = {
        projects.Project.__table__.name,
        projects.ProjectMember.__table__.name,
        projects.Drawing.__table__.name,
        projects.DrawingVersion.__table__.name,
    }

    assert owned == PROJECT_TABLES


def test_public_interfaces_enforce_global_and_project_roles(db: Session) -> None:
    identity = importlib.import_module("app.modules.identity.interface")
    projects = importlib.import_module("app.modules.projects.interface")

    admin = identity.User(
        username="boundary-admin",
        real_name="Boundary Admin",
        password_hash="unused",
        status="active",
    )
    admin.roles = [identity.Role(code="admin", name="Admin", is_system=True)]
    member = identity.User(
        username="boundary-member",
        real_name="Boundary Member",
        password_hash="unused",
        status="active",
    )
    outsider = identity.User(
        username="boundary-outsider",
        real_name="Boundary Outsider",
        password_hash="unused",
        status="active",
    )
    project = projects.Project(code="BOUNDARY", name="Boundary project", status="active")
    db.add_all([admin, member, outsider, project])
    db.flush()
    membership = projects.ProjectMember(
        project_id=project.id,
        user_id=member.id,
        project_role="editor",
    )
    db.add(membership)
    db.flush()

    assert identity.require_roles("admin")(admin) is admin
    assert projects.has_global_project_access(admin) is True
    assert projects.require_project_member(db, admin, project.id) is None
    assert projects.require_project_role(db, member, project.id, {"editor"}) is membership
    with pytest.raises(AppHTTPException) as denied:
        projects.require_project_member(db, outsider, project.id)
    assert denied.value.status_code == 403


def test_other_modules_use_only_identity_and_projects_interfaces() -> None:
    violations: list[str] = []
    for path in sorted(APP_ROOT.rglob("*.py")):
        relative = path.relative_to(APP_ROOT)
        if relative.parts[:2] in {
            ("modules", "identity"),
            ("modules", "projects"),
        } or relative.parts[:1] == ("bootstrap",):
            continue
        for imported in _imports(path):
            for module in ("identity", "projects"):
                prefix = f"app.modules.{module}"
                if imported.startswith(prefix) and not imported.startswith(f"{prefix}.interface"):
                    violations.append(f"{relative} -> {imported}")

    assert violations == []


def test_business_interfaces_do_not_compose_http_routes() -> None:
    for module in ("identity", "projects"):
        path = APP_ROOT / "modules" / module / "interface.py"
        assert not any(
            imported.startswith(f"app.modules.{module}.routes")
            for imported in _imports(path)
        ), module


def test_legacy_identity_and_project_files_are_retired() -> None:
    retired = (
        "api/deps.py",
        "api/v1/auth_api.py",
        "api/v1/users_api.py",
        "api/v1/roles_api.py",
        "api/v1/projects_api.py",
        "api/v1/drawings_api.py",
        "models/user.py",
        "models/role.py",
        "models/token_blacklist.py",
        "models/project.py",
        "models/drawing.py",
        "schemas/auth_schema.py",
        "schemas/user_schema.py",
        "schemas/project_schema.py",
        "schemas/drawing_schema.py",
        "services/auth_service.py",
        "services/user_service.py",
        "services/project_service.py",
        "services/drawing_service.py",
    )

    assert [path for path in retired if (APP_ROOT / path).exists()] == []
