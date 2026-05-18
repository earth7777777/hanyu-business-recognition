from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class ExternalOrchestratorAdapter(ABC):
    provider: str

    @abstractmethod
    def submit(self, envelope: dict[str, Any], profile: dict[str, Any]) -> dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    def poll(
        self,
        external_task_id: str,
        profile: dict[str, Any],
        submit_result: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        raise NotImplementedError

    def parse_callback(
        self,
        payload: dict[str, Any],
        headers: dict[str, str],
        profile: dict[str, Any],
    ) -> dict[str, Any]:
        raise NotImplementedError("Callback parsing is not enabled in this version.")
