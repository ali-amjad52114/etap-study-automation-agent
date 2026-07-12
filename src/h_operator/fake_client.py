"""Deterministic in-memory H client for offline orchestrator tests."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping
from uuid import uuid4


# Structurally valid PNG framing sufficient for the adapter's dependency-free
# header validation. Tests that decode pixels can inject their own resource.
FAKE_PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
)


@dataclass(frozen=True)
class FakeSessionScenario:
    statuses: tuple[str, ...] = ("pending", "running", "completed")
    answer: Mapping[str, Any] | None = field(default_factory=lambda: {
        "step": "OPEN_PROJECT",
        "status": "completed",
        "screenshot_key": "open-project.png",
        "error": None,
    })
    resources: Mapping[tuple[str, str], bytes] = field(
        default_factory=lambda: {("screenshots", "open-project.png"): FAKE_PNG}
    )
    error: str | None = None
    error_code: str | None = None
    outcome: str | None = "success"


class FakeHClient:
    """Implements ``HClientProtocol`` and records observable interactions."""

    def __init__(self, scenario: FakeSessionScenario | None = None):
        self.scenario = scenario or FakeSessionScenario()
        self.created: list[dict[str, Any]] = []
        self.cancelled: list[str] = []
        self._positions: dict[str, int] = {}

    def create_session(self, *, agent: Mapping[str, Any], messages: str) -> str:
        session_id = str(uuid4())
        self.created.append({"id": session_id, "agent": agent, "messages": messages})
        self._positions[session_id] = 0
        return session_id

    def get_status(self, session_id: str) -> Mapping[str, Any]:
        position = self._positions[session_id]
        statuses = self.scenario.statuses
        status = statuses[min(position, len(statuses) - 1)]
        self._positions[session_id] = position + 1
        return {
            "status": status,
            "error": self.scenario.error,
            "error_code": self.scenario.error_code,
            "outcome": self.scenario.outcome,
        }

    def get_answer(self, session_id: str) -> Mapping[str, Any] | None:
        self._positions[session_id]
        return self.scenario.answer

    def get_resource(self, session_id: str, bucket: str, key: str) -> bytes:
        self._positions[session_id]
        return self.scenario.resources[(bucket, key)]

    def cancel(self, session_id: str) -> None:
        self._positions[session_id]
        self.cancelled.append(session_id)
