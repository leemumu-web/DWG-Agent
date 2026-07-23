from __future__ import annotations

from app.modules.identity.interface import User, user_role_codes
from app.platform.config.constants import ROLE_ADMIN, ROLE_REMNANT_WORKER, ROLE_SUPER_ADMIN


def can_use_remnants(user: User) -> bool:
    return bool({ROLE_SUPER_ADMIN, ROLE_ADMIN, ROLE_REMNANT_WORKER} & user_role_codes(user))


def can_manage_materials(user: User) -> bool:
    return bool({ROLE_SUPER_ADMIN, ROLE_ADMIN} & user_role_codes(user))
