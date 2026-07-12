"""Observation-backed conversion from H operator outcomes to local results.

This module depends only on the H-side contracts/protocol. It does not import
the H SDK and performs no UI or desktop actions.
"""

from __future__ import annotations

from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from typing import Callable, Protocol

from etap_automation.models import CheckpointResult, CheckpointStatus, CheckpointStep
from etap_automation.paths import RunLayout
from h_operator.contracts import (
    APPROVED_PROJECT,
    APPROVED_PROJECT_FILE,
    CheckpointCommand,
    OperatorOutcome,
    OperatorStep,
)
from h_operator.adapter import HOperatorError


EXPECTED_IDENTITIES = {
    CheckpointStep.OPEN_PROJECT: "EXAMPLE",
    CheckpointStep.LOAD_FLOW: "Base Case",
    CheckpointStep.COORDINATION: "Main Bus - Feeder 1",
    CheckpointStep.ARC_FLASH: "Normal Operation",
}


class OperatorExecutor(Protocol):
    def execute(
        self, command: CheckpointCommand, screenshot_path: Path
    ) -> OperatorOutcome: ...


class OperatorCheckpointRunner:
    """Make vendor completion conditional on local observation and evidence."""

    def __init__(
        self,
        executor: OperatorExecutor,
        *,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self._executor = executor
        self._now = now or (lambda: datetime.now(UTC))

    def run(
        self, step: CheckpointStep, attempt: int, layout: RunLayout
    ) -> CheckpointResult:
        if step is CheckpointStep.REPORT:
            raise ValueError("REPORT is produced by the reporting runner, not H")
        expected_identity = EXPECTED_IDENTITIES[step]
        screenshot_path = layout.screenshot_png(step, attempt)
        command = _command(step)
        try:
            outcome = self._executor.execute(command, screenshot_path)
        except HOperatorError as exc:
            # Adapter errors are stable, sanitized integration messages.
            return self._failed(step, expected_identity, str(exc))
        except Exception:
            # Vendor/UI exceptions are deliberately collapsed at this boundary;
            # raw exceptions can contain credentials or unstable SDK details.
            return self._failed(step, expected_identity, "operator checkpoint execution failed")

        failure = _outcome_failure(outcome, step, expected_identity, screenshot_path)
        if failure is not None:
            return self._failed(step, expected_identity, failure)

        evidence = outcome.evidence
        assert evidence is not None and outcome.screenshot is not None
        return CheckpointResult(
            step=step,
            status=CheckpointStatus.COMPLETED,
            project=APPROVED_PROJECT,
            study=expected_identity,
            timestamp=evidence.timestamp,
            screenshot=str(outcome.screenshot),
            error=None,
        )

    def _failed(
        self, step: CheckpointStep, identity: str, error: str
    ) -> CheckpointResult:
        timestamp = self._now()
        if timestamp.tzinfo is None or timestamp.utcoffset() is None:
            raise ValueError("orchestrator clock must include a timezone")
        return CheckpointResult(
            step=step,
            status=CheckpointStatus.FAILED,
            project=APPROVED_PROJECT,
            study=identity,
            timestamp=timestamp,
            screenshot=None,
            error=error,
        )


def _command(step: CheckpointStep) -> CheckpointCommand:
    common = {
        "step": OperatorStep(step.value),
        "project": APPROVED_PROJECT,
        "project_file": APPROVED_PROJECT_FILE,
    }
    if step is CheckpointStep.LOAD_FLOW:
        return CheckpointCommand(**common, study_case="Base Case")
    if step is CheckpointStep.COORDINATION:
        return CheckpointCommand(**common, view="Main Bus - Feeder 1")
    if step is CheckpointStep.ARC_FLASH:
        return CheckpointCommand(**common, study_case="Normal Operation")
    return CheckpointCommand(**common)


def _outcome_failure(
    outcome: OperatorOutcome,
    expected_step: CheckpointStep,
    expected_identity: str,
    expected_screenshot: Path,
) -> str | None:
    if not outcome.session_id or not outcome.session_id.strip():
        return "operator outcome has no attached session"
    if outcome.step.value != expected_step.value:
        return "operator outcome step does not match the checkpoint"
    if outcome.status != "completed":
        return outcome.error or "operator checkpoint did not complete"
    if not bool(getattr(outcome, "visible_confirmation", False)):
        return "expected visible checkpoint state was not confirmed"
    if getattr(outcome, "observed_identity", None) != expected_identity:
        return "observed identity does not match the approved checkpoint"
    if outcome.screenshot is None or outcome.evidence is None:
        return "completed operator outcome has no evidence"

    evidence = outcome.evidence
    if evidence.session_id != outcome.session_id:
        return "evidence does not belong to the operator session"
    if not evidence.key or not evidence.key.strip():
        return "evidence resource key is missing"
    if evidence.timestamp.tzinfo is None or evidence.timestamp.utcoffset() is None:
        return "evidence timestamp has no timezone"

    expected = expected_screenshot.resolve(strict=False)
    actual_path = Path(outcome.screenshot)
    if actual_path.is_symlink() or actual_path.resolve(strict=False) != expected:
        return "evidence path does not match the immutable attempt path"
    try:
        payload = actual_path.read_bytes()
    except OSError:
        return "evidence file is missing or unreadable"
    if not payload:
        return "evidence file is empty"
    if evidence.size != len(payload):
        return "evidence byte size does not match the stored file"
    if evidence.sha256.lower() != sha256(payload).hexdigest():
        return "evidence digest does not match the stored file"
    return None
