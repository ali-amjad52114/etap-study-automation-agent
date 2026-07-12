import unittest
from dataclasses import replace
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from tempfile import TemporaryDirectory

from etap_automation.models import CheckpointStatus, CheckpointStep
from etap_automation.paths import RunLayout
from h_operator.contracts import EvidenceMetadata, OperatorOutcome, OperatorStep
from orchestrator import OperatorCheckpointRunner, Orchestrator, WorkflowBlocked


NOW = datetime(2026, 7, 12, 12, 30, tzinfo=UTC)
PNG = b"owned-evidence"


class Executor:
    def __init__(self, mutate=None):
        self.mutate = mutate
        self.calls = []

    def execute(self, command, screenshot_path):
        self.calls.append(command)
        screenshot_path.write_bytes(PNG)
        outcome = OperatorOutcome(
            step=command.step,
            status="completed",
            session_id="session-1",
            screenshot=screenshot_path,
            error=None,
            evidence=EvidenceMetadata(
                "session-1", "proof.png", len(PNG), NOW, sha256(PNG).hexdigest()
            ),
            observed_identity=command.expected_observed_identity,
            visible_confirmation=True,
        )
        return self.mutate(outcome) if self.mutate else outcome


class RaisingExecutor:
    def execute(self, command, screenshot_path):
        raise RuntimeError("vendor detail must not escape")


class Retry:
    def cancel_and_wait_terminal(self, step, attempt):
        return True

    def reset(self, step):
        pass


class WaveFTests(unittest.TestCase):
    def make(self, directory, mutate=None):
        root = Path(directory)
        layout = RunLayout.create(root / "evidence", root / "reports", NOW)
        runner = OperatorCheckpointRunner(Executor(mutate), now=lambda: NOW)
        return layout, Orchestrator.create(layout, runner, Retry())

    def test_matching_observation_and_owned_evidence_complete_and_persist(self):
        with TemporaryDirectory() as directory:
            layout, engine = self.make(directory)
            result = engine.run_next()
            self.assertIs(result.status, CheckpointStatus.COMPLETED)
            self.assertTrue(layout.checkpoint_json(CheckpointStep.OPEN_PROJECT, 1).exists())

    def test_wrong_observation_maps_to_failure_and_blocks_downstream(self):
        with TemporaryDirectory() as directory:
            _, engine = self.make(
                directory, lambda value: replace(value, observed_identity="OTHER")
            )
            result = engine.run_next()
            self.assertIs(result.status, CheckpointStatus.FAILED)
            with self.assertRaises(WorkflowBlocked):
                engine.run_next()

    def test_missing_confirmation_maps_to_failure(self):
        with TemporaryDirectory() as directory:
            def unconfirmed(value):
                # Simulate a malformed implementation crossing the protocol boundary;
                # the frozen contract itself correctly prevents constructing this.
                object.__setattr__(value, "visible_confirmation", False)
                return value
            _, engine = self.make(
                directory, unconfirmed
            )
            self.assertIs(engine.run_next().status, CheckpointStatus.FAILED)

    def test_foreign_evidence_session_maps_to_failure(self):
        with TemporaryDirectory() as directory:
            def foreign(value):
                return replace(value, evidence=replace(value.evidence, session_id="other"))
            _, engine = self.make(directory, foreign)
            self.assertIs(engine.run_next().status, CheckpointStatus.FAILED)

    def test_executor_exception_becomes_persisted_stable_failure(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            layout = RunLayout.create(root / "evidence", root / "reports", NOW)
            engine = Orchestrator.create(
                layout,
                OperatorCheckpointRunner(RaisingExecutor(), now=lambda: NOW),
                Retry(),
            )
            result = engine.run_next()
            self.assertEqual(result.error, "operator checkpoint execution failed")
            self.assertTrue(layout.checkpoint_json(CheckpointStep.OPEN_PROJECT, 1).exists())


if __name__ == "__main__":
    unittest.main()
