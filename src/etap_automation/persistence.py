from __future__ import annotations

from datetime import datetime
import json
import os
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any

from .models import CheckpointResult, CheckpointStatus, CheckpointStep


class PersistenceError(RuntimeError):
    """Raised when an immutable checkpoint record cannot be persisted."""


def write_checkpoint_atomic(result: CheckpointResult, path: Path) -> Path:
    """Write one immutable result and validate it from disk before returning."""
    path = Path(path)
    parent = path.parent.resolve(strict=True)
    final = (parent / path.name).resolve(strict=False)
    if final.parent != parent or final.name != path.name:
        raise PersistenceError("checkpoint path is unsafe")
    if final.exists():
        raise FileExistsError(f"checkpoint record already exists: {final}")

    temporary: Path | None = None
    lock_path = parent / f".{final.name}.lock"
    lock_fd: int | None = None
    try:
        try:
            lock_fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError as exc:
            raise FileExistsError(f"checkpoint record is being committed: {final}") from exc
        with NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="\n",
            dir=parent,
            prefix=f".{final.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary = Path(handle.name)
            json.dump(result.to_dict(), handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())

        # A complete temp record must round-trip through the persisted schema.
        if read_checkpoint(temporary) != result:
            raise PersistenceError("checkpoint readback did not match the result")
        if final.exists():
            raise FileExistsError(f"checkpoint record already exists: {final}")
        os.replace(temporary, final)
        temporary = None
        if read_checkpoint(final) != result:
            raise PersistenceError("final checkpoint readback did not match the result")
        return final
    except (FileExistsError, PersistenceError):
        raise
    except Exception as exc:
        raise PersistenceError("checkpoint write failed") from exc
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
        if lock_fd is not None:
            os.close(lock_fd)
            lock_path.unlink(missing_ok=True)


def read_checkpoint(path: Path) -> CheckpointResult:
    try:
        with Path(path).open(encoding="utf-8") as handle:
            value = json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        raise PersistenceError("checkpoint record is missing or invalid JSON") from exc
    if not isinstance(value, dict):
        raise PersistenceError("checkpoint record must be a JSON object")
    expected = {"step", "status", "project", "study", "timestamp", "screenshot", "error"}
    if set(value) != expected:
        raise PersistenceError("checkpoint record fields do not match the schema")
    try:
        return CheckpointResult(
            step=CheckpointStep(_text(value, "step")),
            status=CheckpointStatus(_text(value, "status")),
            project=_text(value, "project"),
            study=_text(value, "study"),
            timestamp=datetime.fromisoformat(_text(value, "timestamp")),
            screenshot=_optional_text(value, "screenshot"),
            error=_optional_text(value, "error"),
        )
    except (TypeError, ValueError) as exc:
        raise PersistenceError("checkpoint record failed schema validation") from exc


def _text(value: dict[str, Any], key: str) -> str:
    item = value[key]
    if not isinstance(item, str):
        raise TypeError(f"{key} must be text")
    return item


def _optional_text(value: dict[str, Any], key: str) -> str | None:
    item = value[key]
    if item is not None and not isinstance(item, str):
        raise TypeError(f"{key} must be text or null")
    return item
