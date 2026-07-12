"""Small, testable adapter around the H Python SDK.

The H SDK is deliberately hidden behind ``HClientProtocol``.  Production code
can use ``from_hai_sdk`` while tests use ``FakeHClient`` without an API key,
desktop, ETAP installation, or third-party dependency.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from threading import Lock
from time import monotonic, sleep
from typing import Any, Mapping, Protocol


class SessionState(StrEnum):
    QUEUED = "queued"
    PENDING = "pending"
    RUNNING = "running"
    PAUSED = "paused"
    IDLE = "idle"
    AWAITING_TOOL_RESULTS = "awaiting_tool_results"
    COMPLETED = "completed"
    FAILED = "failed"
    TIMED_OUT = "timed_out"
    INTERRUPTED = "interrupted"

    @property
    def terminal(self) -> bool:
        return self in {
            self.COMPLETED,
            self.FAILED,
            self.TIMED_OUT,
            self.INTERRUPTED,
        }


@dataclass(frozen=True)
class ScreenshotResource:
    session_id: str
    key: str
    bucket: str = "screenshots"


@dataclass(frozen=True)
class SessionResult:
    session_id: str
    state: SessionState
    answer: Mapping[str, Any] | None
    error: str | None = None
    error_code: str | None = None
    outcome: str | None = None


class HOperatorError(RuntimeError):
    """Base error for the H integration boundary."""


class SessionBusyError(HOperatorError):
    """Raised instead of allowing H to cancel an already active desktop run."""


class SessionTimeoutError(HOperatorError):
    """Raised when the local wait deadline expires after cancellation."""


class HClientProtocol(Protocol):
    def create_session(self, *, agent: Mapping[str, Any], messages: str) -> str: ...
    def get_status(self, session_id: str) -> Mapping[str, Any]: ...
    def get_answer(self, session_id: str) -> Mapping[str, Any] | None: ...
    def get_resource(self, session_id: str, bucket: str, key: str) -> bytes: ...
    def cancel(self, session_id: str) -> None: ...


CHECKPOINT_ANSWER_SCHEMA: Mapping[str, Any] = {
    "type": "object",
    "properties": {
        "step": {
            "type": "string",
            "enum": ["OPEN_PROJECT", "LOAD_FLOW", "COORDINATION", "ARC_FLASH", "REPORT"],
        },
        "status": {"type": "string", "enum": ["completed", "failed"]},
        "screenshot_key": {"type": ["string", "null"]},
        "error": {"type": ["string", "null"]},
    },
    "required": ["step", "status", "screenshot_key", "error"],
    "additionalProperties": False,
}


class HDesktopAdapter:
    """Own one H ``user_device`` desktop session at a time.

    H documents that starting a second local session cancels the first.  This
    adapter prevents that surprising handoff with a process-wide lock.
    """

    _desktop_lock = Lock()

    def __init__(
        self,
        client: HClientProtocol,
        *,
        environment_id: str = "etap-desktop",
        evidence_root: Path = Path("evidence"),
    ):
        self._client = client
        self._environment_id = environment_id
        self._evidence_root = evidence_root.resolve()
        self._active_session_id: str | None = None

    @classmethod
    def from_hai_sdk(
        cls,
        *,
        environment_id: str = "etap-desktop",
        evidence_root: Path = Path("evidence"),
    ) -> "HDesktopAdapter":
        """Create the production adapter; requires ``hai-agents[desktop]``."""
        try:
            from hai_agents import Client
        except ImportError as exc:  # pragma: no cover - depends on optional SDK
            raise HOperatorError(
                'H desktop support is not installed; install "hai-agents[desktop]"'
            ) from exc
        return cls(
            _HaiSdkClient(Client()),
            environment_id=environment_id,
            evidence_root=evidence_root,
        )

    def start(self, instruction: str) -> str:
        if not instruction.strip():
            raise ValueError("instruction must not be empty")
        if not self._desktop_lock.acquire(blocking=False):
            raise SessionBusyError("another local H desktop session is active")
        try:
            agent = {
                "name": "etap-local-desktop",
                "description": "Runs one approved ETAP MVP checkpoint.",
                "environments": [{
                    "id": self._environment_id,
                    "kind": "desktop",
                    "host": "user_device",
                }],
                "answer_format": CHECKPOINT_ANSWER_SCHEMA,
            }
            session_id = self._client.create_session(
                agent=agent, messages=instruction
            )
            if not session_id:
                raise HOperatorError("H did not return a session id")
            self._active_session_id = session_id
            return self._active_session_id
        except Exception:
            self._desktop_lock.release()
            raise

    def wait(self, *, timeout_seconds: float = 600, poll_seconds: float = 3) -> SessionResult:
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be greater than zero")
        if poll_seconds < 0:
            raise ValueError("poll_seconds must not be negative")
        session_id = self._require_active()
        deadline = monotonic() + timeout_seconds
        try:
            while monotonic() < deadline:
                status = self._client.get_status(session_id)
                state = SessionState(str(status["status"]))
                if state.terminal:
                    answer = self._client.get_answer(session_id) if state == state.COMPLETED else None
                    if state == state.COMPLETED:
                        answer = _validate_checkpoint_answer(answer)
                    return SessionResult(
                        session_id=session_id,
                        state=state,
                        answer=answer,
                        error=_optional_str(status.get("error")),
                        error_code=_optional_str(status.get("error_code")),
                        outcome=_optional_str(status.get("outcome")),
                    )
                sleep(poll_seconds)
            self._client.cancel(session_id)
            raise SessionTimeoutError(f"session {session_id} exceeded {timeout_seconds}s")
        finally:
            self._finish()

    def cancel(self) -> None:
        session_id = self._require_active()
        try:
            self._client.cancel(session_id)
        finally:
            self._finish()

    def save_screenshot(self, resource: ScreenshotResource, destination: Path) -> Path:
        """Validate and materialize a PNG below the configured evidence root."""
        if resource.bucket != "screenshots":
            raise HOperatorError("only screenshot resources are allowed")
        resolved = destination.resolve()
        if not resolved.is_relative_to(self._evidence_root):
            raise HOperatorError("screenshot destination is outside the evidence root")
        if resolved.suffix.lower() != ".png":
            raise HOperatorError("screenshot destination must use a .png extension")
        payload = self._client.get_resource(
            resource.session_id, resource.bucket, resource.key
        )
        _validate_png(payload)
        resolved.parent.mkdir(parents=True, exist_ok=True)
        resolved.write_bytes(payload)
        return resolved

    def _require_active(self) -> str:
        if self._active_session_id is None:
            raise HOperatorError("no active local desktop session")
        return self._active_session_id

    def _finish(self) -> None:
        if self._active_session_id is not None:
            self._active_session_id = None
            self._desktop_lock.release()


class _HaiSdkClient:
    """Translate the documented H SDK surface into ``HClientProtocol``."""

    def __init__(self, client: Any):
        self._client = client

    def create_session(self, *, agent: Mapping[str, Any], messages: str) -> str:
        session = self._client.sessions.create_session(agent=agent, messages=messages)
        return str(session.id)

    def get_status(self, session_id: str) -> Mapping[str, Any]:
        status = self._client.sessions.get_session_status(session_id)
        return {
            "status": status.status,
            "error": getattr(status, "error", None),
            "error_code": getattr(status, "error_code", None),
            "outcome": getattr(status, "outcome", None),
        }

    def get_answer(self, session_id: str) -> Mapping[str, Any] | None:
        value = self._client.sessions.get_session(session_id).latest_answer
        if value is None or isinstance(value, Mapping):
            return value
        raise HOperatorError("H returned an unstructured checkpoint answer")

    def get_resource(self, session_id: str, bucket: str, key: str) -> bytes:
        value = self._client.sessions.get_session_resource(session_id, bucket, key)
        if isinstance(value, bytes):
            return value
        if hasattr(value, "content"):
            return bytes(value.content)
        if hasattr(value, "read"):
            return bytes(value.read())
        raise HOperatorError("unsupported H resource response type")

    def cancel(self, session_id: str) -> None:
        self._client.sessions.cancel_session(session_id)


def _optional_str(value: Any) -> str | None:
    return None if value is None else str(value)


def _validate_checkpoint_answer(value: Mapping[str, Any] | None) -> Mapping[str, Any]:
    """Defend the orchestrator even when a client bypasses H schema validation."""
    if value is None:
        raise HOperatorError("completed H session did not return an answer")
    expected = {"step", "status", "screenshot_key", "error"}
    if set(value) != expected:
        raise HOperatorError("H checkpoint answer has unexpected fields")
    if value["step"] not in {
        "OPEN_PROJECT", "LOAD_FLOW", "COORDINATION", "ARC_FLASH", "REPORT"
    }:
        raise HOperatorError("H checkpoint answer has an invalid step")
    if value["status"] not in {"completed", "failed"}:
        raise HOperatorError("H checkpoint answer has an invalid status")
    for field in ("screenshot_key", "error"):
        if value[field] is not None and not isinstance(value[field], str):
            raise HOperatorError(f"H checkpoint answer field {field!r} must be text or null")
    if value["status"] == "completed" and not value["screenshot_key"]:
        raise HOperatorError("completed H checkpoint answer requires a screenshot key")
    return value


def _validate_png(payload: bytes) -> None:
    """Reject empty or mislabeled resources without adding an image dependency."""
    png_signature = b"\x89PNG\r\n\x1a\n"
    # A PNG starts with the 8-byte signature followed by a 13-byte IHDR chunk.
    if len(payload) < 33 or not payload.startswith(png_signature):
        raise HOperatorError("screenshot resource is not a readable PNG")
    if payload[12:16] != b"IHDR" or int.from_bytes(payload[8:12], "big") != 13:
        raise HOperatorError("screenshot resource has an invalid PNG header")
