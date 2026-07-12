"""Deterministic in-memory H client for offline orchestrator tests."""

from __future__ import annotations

from dataclasses import dataclass, field
import base64
from typing import Any, Mapping
from uuid import uuid4


# Structurally valid PNG framing sufficient for the adapter's dependency-free
# header validation. Tests that decode pixels can inject their own resource.
FAKE_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
)


@dataclass(frozen=True)
class FakeSessionScenario:
    statuses: tuple[str, ...] = ("pending", "running", "completed")
    answer: Mapping[str, Any] | None = field(default_factory=lambda: {
        "step": "OPEN_PROJECT",
        "status": "completed",
        "screenshot_key": "open-project.png",
        "error": None,
        "observed_identity": "EXAMPLE",
        "visible_confirmation": True,
    })
    resources: Mapping[tuple[str, str], bytes] = field(
        default_factory=lambda: {("screenshots", "open-project.png"): FAKE_PNG}
    )
    # Explicitly models the resource derived from the latest observation event.
    # It is intentionally independent of the answer's semantic screenshot label.
    screenshot_resource: tuple[str, str] | None = None
    error: str | None = None
    error_code: str | None = None
    outcome: str | None = "success"


class FakeHClient:
    """Implements ``HClientProtocol`` and records observable interactions."""

    def __init__(self, scenario: FakeSessionScenario | None = None):
        self.scenario = scenario or FakeSessionScenario()
        self.created: list[dict[str, Any]] = []
        self.cancelled: list[str] = []
        self.resources_requested: list[tuple[str, str, str]] = []
        self._positions: dict[str, int] = {}

    def create_session(
        self, *, agent: Mapping[str, Any], messages: str,
        max_steps: int, max_time_s: int, queue: bool,
    ) -> str:
        session_id = str(uuid4())
        self.created.append({
            "id": session_id,
            "agent": agent,
            "messages": messages,
            "max_steps": max_steps,
            "max_time_s": max_time_s,
            "queue": queue,
        })
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
        self.resources_requested.append((session_id, bucket, key))
        return self.scenario.resources[(bucket, key)]

    def cancel(self, session_id: str) -> None:
        self._positions[session_id]
        self.cancelled.append(session_id)

    def discover_latest_screenshot(self, session_id: str) -> tuple[str, str]:
        self._positions[session_id]
        if self.scenario.screenshot_resource is not None:
            return self.scenario.screenshot_resource
        if not self.scenario.resources:
            raise KeyError("no fake screenshot resource")
        return next(reversed(self.scenario.resources))
