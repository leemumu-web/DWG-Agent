"""Application settings and cross-cutting configuration constants."""

# Do not export the settings object under the submodule name ``settings``.
# Keeping that name bound to the module makes dotted imports and monkeypatch
# targets deterministic (app.platform.config.settings.settings).
from app.platform.config.settings import Settings

__all__ = ["Settings"]
