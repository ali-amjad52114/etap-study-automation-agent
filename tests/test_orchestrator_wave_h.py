import unittest
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory

from etap_automation.models import CheckpointResult, CheckpointStatus, CheckpointStep
from etap_automation.paths import RunLayout
from orchestrator import (
    CHECKPOINT_ORDER,
    CancellationNotTerminal,
    IllegalTransition,
    Orchestrator,
    WorkflowBlocked,
)


NOW = datetime(2026, 7, 12, 12, 30, tzinfo=UTC)


class Runner:
    def __init__(self, fail: CheckpointStep | None = None):
        self.fail = fail
        self.calls = []

    def run(self, step, attempt, layout):
        self.calls.append((step, attempt))
        failed = step is self.fail
        return CheckpointResult(
            step, CheckpointStatus.FAILED if failed else CheckpointStatus.COMPLETED,
            "EXAMPLE", step.value, NOW,
            None if failed else str(layout.screenshot_png(step, attempt)),
            "injected failure" if failed else None,
        )


class Retry:
    def __init__(self, terminal=True):
        self.terminal = terminal
        self.calls = []

    def cancel_and_wait_terminal(self, step, attempt):
        self.calls.append(("cancel", step, attempt))
        return self.terminal

    def reset(self, step):
        self.calls.append(("reset", step))


class OrchestratorTests(unittest.TestCase):
    def make(self, directory, runner=None, retry=None):
        root = Path(directory)
        layout = RunLayout.create(root / "evidence", root / "reports", NOW)
        return layout, Orchestrator.create(layout, runner or Runner(), retry or Retry())

    def test_fixed_order_and_exact_five_statuses(self):
        with TemporaryDirectory() as directory:
            runner = Runner()
            _, engine = self.make(directory, runner)
            self.assertEqual(list(engine.status()), [step.value for step in CHECKPOINT_ORDER])
            for step in CHECKPOINT_ORDER:
                self.assertIs(engine.run_next().step, step)
            self.assertTrue(all(value == "completed" for value in engine.status().values()))
            with self.assertRaises(IllegalTransition):
                engine.run_next()

    def test_failure_stops_downstream_and_retry_runs_only_failure(self):
        with TemporaryDirectory() as directory:
            runner = Runner(CheckpointStep.LOAD_FLOW)
            retry = Retry()
            _, engine = self.make(directory, runner, retry)
            engine.run_next()
            engine.run_next()
            with self.assertRaises(WorkflowBlocked):
                engine.run_next()
            runner.fail = None
            engine.retry(CheckpointStep.LOAD_FLOW)
            self.assertEqual(runner.calls[-1], (CheckpointStep.LOAD_FLOW, 2))
            self.assertEqual([call[0] for call in retry.calls], ["cancel", "reset"])
            self.assertIs(engine.run_next().step, CheckpointStep.COORDINATION)

    def test_nonterminal_cancel_blocks_reset_and_retry(self):
        with TemporaryDirectory() as directory:
            runner = Runner(CheckpointStep.OPEN_PROJECT)
            retry = Retry(False)
            _, engine = self.make(directory, runner, retry)
            engine.run_next()
            with self.assertRaises(CancellationNotTerminal):
                engine.retry(CheckpointStep.OPEN_PROJECT)
            self.assertEqual(len(runner.calls), 1)
            self.assertEqual([call[0] for call in retry.calls], ["cancel"])

    def test_resume_from_records_selects_same_next_step(self):
        with TemporaryDirectory() as directory:
            runner = Runner()
            retry = Retry()
            layout, engine = self.make(directory, runner, retry)
            engine.run_next()
            resumed = Orchestrator.resume(layout, runner, retry)
            self.assertEqual(resumed.status()["OPEN_PROJECT"], "completed")
            self.assertIs(resumed.run_next().step, CheckpointStep.LOAD_FLOW)


if __name__ == "__main__":
    unittest.main()
