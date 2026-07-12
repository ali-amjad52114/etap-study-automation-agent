"""Small, testable adapter around the H Python SDK.

The H SDK is deliberately hidden behind ``HClientProtocol``.  Production code
can use ``from_hai_sdk`` while tests use ``FakeHClient`` without an API key,
desktop, ETAP installation, or third-party dependency.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from pathlib import PurePosixPath
import hashlib
import os
import tempfile
from threading import Lock
from time import monotonic, sleep
from typing import Any, Mapping, Protocol
from urllib.parse import unquote, urlparse
import re

from .contracts import EvidenceMetadata


MAX_SCREENSHOT_BYTES = 5 * 1024 * 1024
MAX_PNG_DIMENSION = 16_384
H_PRODUCTION_SCREENSHOT_BUCKET = (
    "production-agentplatformb-screenshotbucketv2f6e481-kjfhukx6imoq"
)


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
    screenshot: ScreenshotResource | None = None


class HOperatorError(RuntimeError):
    """Base error for the H integration boundary."""


class SessionBusyError(HOperatorError):
    """Raised instead of allowing H to cancel an already active desktop run."""


class SessionTimeoutError(HOperatorError):
    """Raised when the local wait deadline expires after cancellation."""


class HClientProtocol(Protocol):
    def create_session(
        self, *, agent: Mapping[str, Any], messages: str,
        max_steps: int, max_time_s: int, queue: bool,
    ) -> str: ...
    def get_status(self, session_id: str) -> Mapping[str, Any]: ...
    def get_answer(self, session_id: str) -> Mapping[str, Any] | None: ...
    def get_resource(self, session_id: str, bucket: str, key: str) -> bytes: ...
    def cancel(self, session_id: str) -> None: ...
    def discover_latest_screenshot(self, session_id: str) -> tuple[str, str]: ...


CHECKPOINT_ANSWER_SCHEMA: Mapping[str, Any] = {
    "type": "object",
    "properties": {
        "step": {
            "type": "string",
            "enum": ["OPEN_PROJECT", "LOAD_FLOW", "COORDINATION", "ARC_FLASH"],
        },
        "status": {"type": "string", "enum": ["completed", "failed"]},
        "screenshot_key": {"type": ["string", "null"]},
        "error": {"type": ["string", "null"]},
        "observed_identity": {"type": ["string", "null"]},
        "visible_confirmation": {"type": "boolean"},
    },
    "required": [
        "step", "status", "screenshot_key", "error",
        "observed_identity", "visible_confirmation",
    ],
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
        self._expected_step: str | None = None
        self._owned_session_ids: set[str] = set()
        self._screenshot_keys: dict[str, tuple[str, str]] = {}
        self._waiting_session_ids: set[str] = set()
        self._cancel_requested: set[str] = set()
        self._state_lock = Lock()

    @classmethod
    def from_hai_sdk(
        cls,
        *,
        environment_id: str = "etap-desktop",
        evidence_root: Path = Path("evidence"),
        region: str = "eu",
    ) -> "HDesktopAdapter":
        """Create the production adapter; requires ``hai-agents[desktop]``."""
        normalized_region = region.lower()
        if normalized_region not in {"eu", "us"}:
            raise ValueError("region must be 'eu' or 'us'")
        try:
            from hai_agents import Client, HaiAgentsEnvironment
        except ImportError as exc:  # pragma: no cover - depends on optional SDK
            raise HOperatorError(
                'H desktop support is not installed; install "hai-agents[desktop]"'
            ) from exc
        client = Client() if normalized_region == "eu" else Client(
            environment=HaiAgentsEnvironment.US
        )
        return cls(
            _HaiSdkClient(client),
            environment_id=environment_id,
            evidence_root=evidence_root,
        )

    def start(
        self,
        instruction: str,
        *,
        expected_step: str | None = None,
        max_steps: int = 80,
        max_time_s: int = 600,
    ) -> str:
        if not instruction.strip():
            raise ValueError("instruction must not be empty")
        allowed_steps = CHECKPOINT_ANSWER_SCHEMA["properties"]["step"]["enum"]
        if expected_step is not None and expected_step not in allowed_steps:
            raise ValueError("expected_step is not an MVP checkpoint")
        if not 1 <= max_steps <= 120:
            raise ValueError("max_steps must be between 1 and 120")
        if not 1 <= max_time_s <= 900:
            raise ValueError("max_time_s must be between 1 and 900")
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
                agent=agent,
                messages=instruction,
                max_steps=max_steps,
                max_time_s=max_time_s,
                queue=False,
            )
            if not session_id:
                raise HOperatorError("H did not return a session id")
            with self._state_lock:
                self._active_session_id = session_id
                self._expected_step = expected_step
                self._owned_session_ids.add(session_id)
            return self._active_session_id
        except Exception:
            self._desktop_lock.release()
            raise

    def wait(self, *, timeout_seconds: float = 600, poll_seconds: float = 3) -> SessionResult:
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be greater than zero")
        if poll_seconds < 0:
            raise ValueError("poll_seconds must not be negative")
        session_id = self._claim_waiter()
        deadline = monotonic() + timeout_seconds
        try:
            while monotonic() < deadline:
                status = self._client.get_status(session_id)
                state = SessionState(str(status["status"]))
                if state.terminal:
                    answer = self._client.get_answer(session_id) if state == state.COMPLETED else None
                    if state == state.COMPLETED:
                        answer = _validate_checkpoint_answer(answer)
                        with self._state_lock:
                            expected_step = self._expected_step
                        if expected_step is not None and answer["step"] != expected_step:
                            raise HOperatorError(
                                f"H returned step {answer['step']!r}; expected {expected_step!r}"
                            )
                        if answer["status"] == "completed":
                            bucket, key = self._client.discover_latest_screenshot(session_id)
                            with self._state_lock:
                                self._screenshot_keys[session_id] = (bucket, key)
                            screenshot = ScreenshotResource(session_id, key, bucket)
                        else:
                            screenshot = None
                    else:
                        screenshot = None
                    return SessionResult(
                        session_id=session_id,
                        state=state,
                        answer=answer,
                        error=_optional_str(status.get("error")),
                        error_code=_optional_str(status.get("error_code")),
                        outcome=_optional_str(status.get("outcome")),
                        screenshot=screenshot,
                    )
                sleep(poll_seconds)
            timeout_error = SessionTimeoutError(
                f"session {session_id} exceeded {timeout_seconds}s"
            )
            try:
                self._client.cancel(session_id)
            except Exception as cancel_error:
                raise timeout_error from cancel_error
            raise timeout_error
        finally:
            with self._state_lock:
                self._waiting_session_ids.discard(session_id)
            self._finish(session_id)

    def cancel(self) -> None:
        with self._state_lock:
            session_id = self._active_session_id
            if session_id is not None and session_id in self._cancel_requested:
                return
            if session_id is not None:
                self._cancel_requested.add(session_id)
            waiter_owns_lease = session_id in self._waiting_session_ids if session_id else False
        if session_id is None:
            return
        try:
            self._client.cancel(session_id)
        except Exception:
            with self._state_lock:
                self._cancel_requested.discard(session_id)
            raise
        finally:
            if not waiter_owns_lease:
                self._finish(session_id)

    def save_screenshot(self, resource: ScreenshotResource, destination: Path) -> Path:
        """Validate and materialize a PNG below the configured evidence root."""
        return self.save_screenshot_with_metadata(resource, destination)[0]

    def save_screenshot_with_metadata(
        self, resource: ScreenshotResource, destination: Path
    ) -> tuple[Path, EvidenceMetadata]:
        """Atomically publish exact session evidence and return its provenance."""
        if resource.bucket not in {"screenshots", H_PRODUCTION_SCREENSHOT_BUCKET}:
            raise HOperatorError("only screenshot resources are allowed")
        resolved = destination.resolve()
        if not resolved.is_relative_to(self._evidence_root):
            raise HOperatorError("screenshot destination is outside the evidence root")
        if resolved.suffix.lower() != ".png":
            raise HOperatorError("screenshot destination must use a .png extension")
        if resource.session_id not in self._owned_session_ids:
            raise HOperatorError("screenshot does not belong to a session started by this adapter")
        key_path = PurePosixPath(resource.key)
        if (
            not resource.key
            or "\\" in resource.key
            or key_path.is_absolute()
            or any(part in {"", ".", ".."} for part in key_path.parts)
            or key_path.suffix.lower() != ".png"
        ):
            raise HOperatorError("screenshot resource key must be a safe PNG path")
        with self._state_lock:
            expected_key = self._screenshot_keys.get(resource.session_id)
        if expected_key != (resource.bucket, resource.key):
            raise HOperatorError("screenshot key was not returned by this session")
        payload = self._client.get_resource(
            resource.session_id, resource.bucket, resource.key
        )
        _validate_png(payload)
        resolved.parent.mkdir(parents=True, exist_ok=True)
        _write_atomic_exclusive(resolved, payload)
        metadata = EvidenceMetadata(
            session_id=resource.session_id,
            key=resource.key,
            size=len(payload),
            timestamp=datetime.now(UTC),
            sha256=hashlib.sha256(payload).hexdigest(),
        )
        return resolved, metadata

    def _require_active(self) -> str:
        with self._state_lock:
            if self._active_session_id is None:
                raise HOperatorError("no active local desktop session")
            return self._active_session_id

    def _claim_waiter(self) -> str:
        """Atomically bind one waiter to the active session and its global lease."""
        with self._state_lock:
            if self._active_session_id is None:
                raise HOperatorError("no active local desktop session")
            session_id = self._active_session_id
            if session_id in self._waiting_session_ids:
                raise HOperatorError("a waiter already owns this desktop session")
            self._waiting_session_ids.add(session_id)
            return session_id

    def _finish(self, session_id: str) -> None:
        release = False
        with self._state_lock:
            if self._active_session_id == session_id:
                self._active_session_id = None
                self._expected_step = None
                self._cancel_requested.discard(session_id)
                self._waiting_session_ids.discard(session_id)
                release = True
        if release:
            self._desktop_lock.release()


class _HaiSdkClient:
    """Translate the documented H SDK surface into ``HClientProtocol``."""

    def __init__(self, client: Any):
        required = (
            "create_session",
            "get_session_status",
            "get_session",
            "get_session_resource",
            "cancel_session",
        )
        sessions = getattr(client, "sessions", None)
        missing = [name for name in required if not callable(getattr(sessions, name, None))]
        if missing:
            raise HOperatorError(
                "hai-agents 1.0.6 sessions API is missing: " + ", ".join(missing)
            )
        self._client = client

    def create_session(
        self, *, agent: Mapping[str, Any], messages: str,
        max_steps: int, max_time_s: int, queue: bool,
    ) -> str:
        session = self._client.sessions.create_session(
            agent=agent,
            messages=messages,
            max_steps=max_steps,
            max_time_s=max_time_s,
            queue=queue,
        )
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
        try:
            chunks = iter(value)
        except TypeError as exc:
            raise HOperatorError("unsupported H resource response type") from exc
        payload = bytearray()
        for chunk in chunks:
            if not isinstance(chunk, (bytes, bytearray, memoryview)):
                raise HOperatorError("H resource stream returned a non-bytes chunk")
            payload.extend(chunk)
            if len(payload) > MAX_SCREENSHOT_BYTES:
                raise HOperatorError("H resource stream exceeds the 5 MiB limit")
        return bytes(payload)

    def cancel(self, session_id: str) -> None:
        self._client.sessions.cancel_session(session_id)

    def discover_latest_screenshot(self, session_id: str) -> tuple[str, str]:
        list_events = getattr(self._client.sessions, "list_session_events", None)
        if not callable(list_events):
            raise HOperatorError("hai-agents 1.0.6 list_session_events API is unavailable")
        page = list_events(
            session_id, size=200, sort=["-timestamp"], type="AgentEvent"
        )
        for event in _field(page, "items", ()):
            if _field(event, "type") != "AgentEvent":
                continue
            data = _field(event, "data", {})
            if _field(data, "kind") != "observation_event":
                continue
            image = _field(data, "image", None)
            if image is None:
                continue
            if _field(image, "type") != "url" or _field(image, "media_type") != "image/png":
                continue
            parsed = _parse_observation_resource_url(str(_field(image, "source", "")), session_id)
            if parsed is not None:
                return parsed
        raise HOperatorError("no safe PNG observation resource found for completed session")


def _optional_str(value: Any) -> str | None:
    return None if value is None else str(value)


def _field(value: Any, name: str, default: Any = None) -> Any:
    if isinstance(value, Mapping):
        return value.get(name, default)
    return getattr(value, name, default)


def _parse_observation_resource_url(source: str, session_id: str) -> tuple[str, str] | None:
    try:
        parsed = urlparse(source)
        hostname = parsed.hostname
        port = parsed.port
    except ValueError:
        return None
    if (
        parsed.scheme != "https"
        or hostname not in {"agp.eu.hcompany.ai", "agp.hcompany.ai"}
        or port is not None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.params
        or parsed.query
        or parsed.fragment
    ):
        return None
    decoded_path = unquote(parsed.path)
    if decoded_path != parsed.path:
        return None
    parts = decoded_path.split("/")
    expected_prefix = ["", "api", "v1", "trajectories", session_id, "resources"]
    if len(parts) != 9 or parts[:6] != expected_prefix or parts[7] != session_id:
        return None
    bucket, filename = parts[6], parts[8]
    safe_component = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
    if (
        not safe_component.fullmatch(bucket)
        or not safe_component.fullmatch(filename)
        or not filename.lower().endswith(".png")
    ):
        return None
    return bucket, f"{session_id}/{filename}"


def _validate_checkpoint_answer(value: Mapping[str, Any] | None) -> Mapping[str, Any]:
    """Defend the orchestrator even when a client bypasses H schema validation."""
    if value is None:
        raise HOperatorError("completed H session did not return an answer")
    expected = {
        "step", "status", "screenshot_key", "error",
        "observed_identity", "visible_confirmation",
    }
    if set(value) != expected:
        raise HOperatorError("H checkpoint answer has unexpected fields")
    if value["step"] not in {
        "OPEN_PROJECT", "LOAD_FLOW", "COORDINATION", "ARC_FLASH"
    }:
        raise HOperatorError("H checkpoint answer has an invalid step")
    if value["status"] not in {"completed", "failed"}:
        raise HOperatorError("H checkpoint answer has an invalid status")
    for field in ("screenshot_key", "error", "observed_identity"):
        if value[field] is not None and not isinstance(value[field], str):
            raise HOperatorError(f"H checkpoint answer field {field!r} must be text or null")
    if value["status"] == "completed" and not value["screenshot_key"]:
        raise HOperatorError("completed H checkpoint answer requires a screenshot key")
    if value["status"] == "completed" and value["error"] is not None:
        raise HOperatorError("completed H checkpoint answer cannot contain an error")
    if value["status"] == "failed" and not value["error"]:
        raise HOperatorError("failed H checkpoint answer requires an error")
    if value["status"] == "failed" and value["screenshot_key"] is not None:
        raise HOperatorError("failed H checkpoint answer cannot contain a screenshot key")
    if not isinstance(value["visible_confirmation"], bool):
        raise HOperatorError("visible_confirmation must be boolean")
    if value["status"] == "completed" and (
        not value["observed_identity"] or value["visible_confirmation"] is not True
    ):
        raise HOperatorError("completed H checkpoint requires final visible confirmation")
    return value


def _validate_png(payload: bytes) -> None:
    """Validate bounded PNG framing, chunks, dimensions, CRCs, and terminator."""
    import zlib

    png_signature = b"\x89PNG\r\n\x1a\n"
    if not payload or len(payload) > MAX_SCREENSHOT_BYTES:
        raise HOperatorError("screenshot resource exceeds the 5 MiB limit or is empty")
    if len(payload) < 45 or not payload.startswith(png_signature):
        raise HOperatorError("screenshot resource is not a readable PNG")
    offset = len(png_signature)
    chunks: list[bytes] = []
    while offset + 12 <= len(payload):
        length = int.from_bytes(payload[offset:offset + 4], "big")
        chunk_type = payload[offset + 4:offset + 8]
        end = offset + 12 + length
        if end > len(payload):
            raise HOperatorError("screenshot resource has a truncated PNG chunk")
        data = payload[offset + 8:offset + 8 + length]
        expected_crc = int.from_bytes(payload[offset + 8 + length:end], "big")
        if zlib.crc32(chunk_type + data) & 0xFFFFFFFF != expected_crc:
            raise HOperatorError("screenshot resource has an invalid PNG checksum")
        chunks.append(chunk_type)
        if len(chunks) == 1:
            if chunk_type != b"IHDR" or length != 13:
                raise HOperatorError("screenshot resource has an invalid PNG header")
            width = int.from_bytes(data[0:4], "big")
            height = int.from_bytes(data[4:8], "big")
            if not (1 <= width <= MAX_PNG_DIMENSION and 1 <= height <= MAX_PNG_DIMENSION):
                raise HOperatorError("screenshot PNG dimensions are outside policy")
        offset = end
        if chunk_type == b"IEND":
            if length != 0 or offset != len(payload):
                raise HOperatorError("screenshot resource has an invalid PNG terminator")
            break
    if not chunks or chunks[-1] != b"IEND" or b"IDAT" not in chunks:
        raise HOperatorError("screenshot resource is missing PNG image data or IEND")


def _write_atomic_exclusive(destination: Path, payload: bytes) -> None:
    if destination.exists():
        raise FileExistsError(f"screenshot already exists: {destination}")
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{destination.name}.", suffix=".tmp", dir=destination.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.link(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)
