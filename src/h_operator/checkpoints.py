"""Execute one typed H checkpoint without deciding workflow order."""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

from .adapter import HDesktopAdapter, HOperatorError, ScreenshotResource, SessionState
from .contracts import CheckpointCommand, OperatorOutcome
from .prompts import build_checkpoint_prompt


class CheckpointRunner(Protocol):
    def execute(self, command: CheckpointCommand, screenshot_path: Path) -> OperatorOutcome: ...


class HCheckpointRunner:
    def __init__(self, adapter: HDesktopAdapter):
        self._adapter = adapter

    def execute(self, command: CheckpointCommand, screenshot_path: Path) -> OperatorOutcome:
        session_id = self._adapter.start(
            build_checkpoint_prompt(command),
            expected_step=command.step.value,
            max_steps=command.max_steps,
            max_time_s=command.timeout_seconds,
        )
        result = self._adapter.wait(timeout_seconds=command.timeout_seconds)
        if result.state is not SessionState.COMPLETED:
            status = "cancelled" if result.state is SessionState.INTERRUPTED else "failed"
            return OperatorOutcome(
                step=command.step,
                status=status,
                session_id=session_id,
                screenshot=None,
                error=result.error or f"H session ended as {result.state.value}",
                error_code=result.error_code,
            )
        answer = result.answer
        if answer is None:  # defensive; adapter already enforces this
            raise HOperatorError("completed checkpoint has no structured answer")
        if answer["status"] == "failed":
            return OperatorOutcome(
                step=command.step,
                status="failed",
                session_id=session_id,
                screenshot=None,
                error=str(answer["error"] or "H reported checkpoint failure"),
                observed_identity=answer["observed_identity"],
                visible_confirmation=bool(answer["visible_confirmation"]),
            )
        observed_identity = str(answer["observed_identity"])
        if (
            answer["visible_confirmation"] is not True
            or observed_identity != command.expected_observed_identity
        ):
            raise HOperatorError(
                "final visible identity does not match the canonical checkpoint target"
            )
        if result.screenshot is None:
            raise HOperatorError("completed checkpoint has no discovered observation screenshot")
        saved, metadata = self._adapter.save_screenshot_with_metadata(
            result.screenshot, screenshot_path
        )
        return OperatorOutcome(
            step=command.step,
            status="completed",
            session_id=session_id,
            screenshot=saved,
            error=None,
            evidence=metadata,
            observed_identity=observed_identity,
            visible_confirmation=True,
        )
