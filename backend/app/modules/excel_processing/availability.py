"""Feature-flag gate for the implemented Excel Final pipeline."""

from app.platform.config.settings import settings
from app.platform.http.exceptions import service_unavailable


def ensure_pipeline_enabled() -> None:
    if not settings.excel_final_pipeline_enabled:
        raise service_unavailable(
            "EXCEL_FINAL_PIPELINE_DISABLED",
            "Excel→Final pipeline is disabled. Set EXCEL_FINAL_PIPELINE_ENABLED=true to enable.",
        )


__all__ = ["ensure_pipeline_enabled"]
