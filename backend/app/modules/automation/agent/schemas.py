from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class AgentRunCreate(BaseModel):
    session_id: str
    task: str
    file_id: int | None = None
    context: dict[str, Any] = Field(default_factory=dict)


class AgentRunRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    session_id: str
    user_id: int | None = None
    project_id: int | None = None
    drawing_id: int | None = None
    file_id: int | None = None
    task: str
    status: str
    answer: str | None = None
    output_file_id: int | None = None
    history_count: int
    created_at: datetime
    updated_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None


class AgentRunStepRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    agent_run_id: int
    step_type: str
    title: str | None = None
    tool_name: str | None = None
    arguments_json: dict[str, Any] | None = None
    content: str | None = None
    status: str
