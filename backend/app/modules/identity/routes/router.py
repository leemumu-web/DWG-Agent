"""Identity HTTP route composition."""

from fastapi import APIRouter

from app.modules.identity.routes import roles, sessions, users

sessions_router = sessions.router
users_router = users.router
roles_router = roles.router

router = APIRouter()
router.include_router(sessions_router, prefix="/auth", tags=["auth"])
router.include_router(users_router, prefix="/users", tags=["users"])
router.include_router(roles_router, tags=["roles"])

__all__ = ["roles_router", "router", "sessions_router", "users_router"]
