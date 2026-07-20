"""Public identity boundary for other business modules."""

from app.modules.identity.access import has_any_role, is_admin, require_roles, user_role_codes
from app.modules.identity.dependencies import (
    CurrentUser,
    CurrentUserForSSE,
    get_current_user,
    get_current_user_for_sse,
    get_raw_access_token,
)
from app.modules.identity.models import (
    Permission,
    Role,
    TokenBlacklist,
    User,
    role_permissions,
    user_roles,
)

__all__ = [
    "CurrentUser",
    "CurrentUserForSSE",
    "Permission",
    "Role",
    "TokenBlacklist",
    "User",
    "get_current_user",
    "get_current_user_for_sse",
    "get_raw_access_token",
    "has_any_role",
    "is_admin",
    "require_roles",
    "role_permissions",
    "user_role_codes",
    "user_roles",
]
