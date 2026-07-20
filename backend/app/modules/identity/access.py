"""Global role-based access decisions."""

from app.modules.identity.dependencies import CurrentUser
from app.modules.identity.models.user import User
from app.platform.config.constants import ROLE_ADMIN, ROLE_SUPER_ADMIN
from app.platform.http.exceptions import forbidden


def user_role_codes(user: User) -> set[str]:
    return {role.code for role in user.roles}


def has_any_role(user: User, allowed_roles: set[str]) -> bool:
    return bool(allowed_roles.intersection(user_role_codes(user)))


def is_admin(user: User) -> bool:
    return has_any_role(user, {ROLE_SUPER_ADMIN, ROLE_ADMIN})


def require_roles(*allowed_roles: str):
    def dependency(current_user: CurrentUser) -> User:
        if has_any_role(current_user, {ROLE_SUPER_ADMIN, *allowed_roles}):
            return current_user
        raise forbidden()

    return dependency


__all__ = ["has_any_role", "is_admin", "require_roles", "user_role_codes"]
