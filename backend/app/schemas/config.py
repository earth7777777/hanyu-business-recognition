from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class ConfigPayload(BaseModel):
    value: dict[str, Any] = Field(default_factory=dict)
