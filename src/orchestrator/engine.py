from __future__ import annotations

from pathlib import Path
import re
from typing import Protocol

from etap_automation.models import CheckpointResult, CheckpointStatus, CheckpointStep
from etap_automation.paths import RunLayout
from etap_automation.persistence import read_checkpoint, write_checkpoint_atomic

from .state import CHECKPOINT_ORDER
from .status import project_status


class WorkflowBlocked(RuntimeError):
    """A failed checkpoint prevents downstream execution."""


class IllegalTransition(RuntimeError):
    """A requested operation violates the fixed workflow."""


class CancellationNotTerminal(RuntimeError):
    """Retry is unsafe because the prior session did not finish cancelling."""


class CheckpointRunner(Protocol):
    def run(
        self, step: CheckpointStep, attempt: int, layout: RunLayout
    ) -> CheckpointResult: ...


class RetryControl(Protocol):
    def cancel_and_wait_terminal(self, step: CheckpointStep, attempt: int) -> bool: ...
    def reset(self, step: CheckpointStep) -> None: ...


class Orchestrator:
    def __init__(
        self,
        layout: RunLayout,
        runner: CheckpointRunner,
        retry_control: RetryControl,
        history: dict[CheckpointStep, list[tuple[int, CheckpointResult]]] | None = None,
    ) -> None:
        self.layout = layout
        self._runner = runner
        self._retry_control = retry_control
        self._history = history or {step: [] for step in CHECKPOINT_ORDER}
        self._validate_history()

    @classmethod
    def create(
        cls, layout: RunLayout, runner: CheckpointRunner, retry_control: RetryControl
    ) -> "Orchestrator":
        return cls(layout, runner, retry_control)

    @classmethod
    def resume(
        cls, layout: RunLayout, runner: CheckpointRunner, retry_control: RetryControl
    ) -> "Orchestrator":
        history = {step: [] for step in CHECKPOINT_ORDER}
        run_root = layout.evidence_root / layout.run_id
        for step in CHECKPOINT_ORDER:
            step_root = run_root / step.value
            if not step_root.exists():
                continue
            for attempt_dir in step_root.iterdir():
                match = re.fullmatch(r"attempt-([0-9]+)", attempt_dir.name)
                if not match or not attempt_dir.is_dir():
                    raise IllegalTransition("stored attempt layout is invalid")
                record = attempt_dir / "checkpoint.json"
                if not record.exists():
                    # Screenshot-only or interrupted attempt directories are not completion.
                    continue
                attempt = int(match.group(1))
                if attempt < 1:
                    raise IllegalTransition("stored attempt identity is invalid")
                result = read_checkpoint(record)
                if result.step is not step:
                    raise IllegalTransition("stored checkpoint is under the wrong step")
                history[step].append((attempt, result))
            history[step].sort(key=lambda item: item[0])
            attempts = [attempt for attempt, _ in history[step]]
            if len(attempts) != len(set(attempts)):
                raise IllegalTransition("stored attempt identity is duplicated")
        return cls(layout, runner, retry_control, history)

    def run_next(self) -> CheckpointResult:
        step = self._next_step()
        if step is None:
            raise IllegalTransition("workflow is already complete")
        attempt = self._next_attempt(step)
        result = self._runner.run(step, attempt, self.layout)
        return self._record(step, attempt, result)

    def retry(self, step: CheckpointStep | str) -> CheckpointResult:
        step = CheckpointStep(step)
        latest = self._latest(step)
        if latest is None or latest[1].status is not CheckpointStatus.FAILED:
            raise IllegalTransition("only the failed checkpoint may be retried")
        if self._first_failed_step() is not step:
            raise IllegalTransition("only the blocking failed checkpoint may be retried")
        prior_attempt = latest[0]
        if not self._retry_control.cancel_and_wait_terminal(step, prior_attempt):
            raise CancellationNotTerminal("prior checkpoint cancellation is not terminal")
        self._retry_control.reset(step)
        attempt = self._next_attempt(step)
        result = self._runner.run(step, attempt, self.layout)
        return self._record(step, attempt, result)

    def status(self) -> dict[str, str]:
        latest = {
            step: entries[-1][1]
            for step, entries in self._history.items()
            if entries
        }
        return project_status(latest)

    def _next_step(self) -> CheckpointStep | None:
        failed = self._first_failed_step()
        if failed is not None:
            raise WorkflowBlocked(f"{failed.value} failed; retry it before continuing")
        for step in CHECKPOINT_ORDER:
            latest = self._latest(step)
            if latest is None:
                return step
            if latest[1].status is not CheckpointStatus.COMPLETED:
                raise WorkflowBlocked(f"{step.value} did not complete")
        return None

    def _record(
        self, expected_step: CheckpointStep, attempt: int, result: CheckpointResult
    ) -> CheckpointResult:
        if result.step is not expected_step:
            raise IllegalTransition("runner returned a different checkpoint step")
        path = self.layout.checkpoint_json(expected_step, attempt)
        write_checkpoint_atomic(result, path)
        self._history[expected_step].append((attempt, result))
        return result

    def _next_attempt(self, step: CheckpointStep) -> int:
        return 1 if not self._history[step] else self._history[step][-1][0] + 1

    def _latest(self, step: CheckpointStep) -> tuple[int, CheckpointResult] | None:
        entries = self._history[step]
        return entries[-1] if entries else None

    def _first_failed_step(self) -> CheckpointStep | None:
        for step in CHECKPOINT_ORDER:
            latest = self._latest(step)
            if latest is not None and latest[1].status is CheckpointStatus.FAILED:
                return step
        return None

    def _validate_history(self) -> None:
        predecessor_complete = True
        blocked = False
        for step in CHECKPOINT_ORDER:
            entries = self._history.setdefault(step, [])
            if entries and (not predecessor_complete or blocked):
                raise IllegalTransition("stored records violate checkpoint order")
            if entries:
                attempts = [attempt for attempt, _ in entries]
                if attempts != sorted(attempts) or attempts[0] < 1:
                    raise IllegalTransition("stored attempt order is invalid")
                latest = entries[-1][1]
                predecessor_complete = latest.status is CheckpointStatus.COMPLETED
                blocked = latest.status is CheckpointStatus.FAILED
            else:
                predecessor_complete = False
