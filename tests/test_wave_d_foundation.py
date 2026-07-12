import os
import unittest
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from etap_automation.models import CheckpointResult, CheckpointStatus, CheckpointStep
from etap_automation.paths import PathSafetyError, RunLayout
from etap_automation.persistence import PersistenceError, read_checkpoint, write_checkpoint_atomic


NOW = datetime(2026, 7, 12, 12, 30, tzinfo=UTC)


def completed(path: Path) -> CheckpointResult:
    return CheckpointResult(
        CheckpointStep.OPEN_PROJECT,
        CheckpointStatus.COMPLETED,
        "EXAMPLE",
        "EXAMPLE",
        NOW,
        str(path),
        None,
    )


class RunLayoutTests(unittest.TestCase):
    def test_clock_collision_allocates_distinct_immutable_runs(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            first = RunLayout.create(root / "evidence", root / "reports", NOW)
            second = RunLayout.create(root / "evidence", root / "reports", NOW)
            self.assertNotEqual(first.run_id, second.run_id)
            self.assertNotEqual(first.checkpoint_json("OPEN_PROJECT", 1), second.checkpoint_json("OPEN_PROJECT", 1))

    def test_attempts_have_distinct_paths(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            layout = RunLayout.create(root / "evidence", root / "reports", NOW)
            self.assertNotEqual(layout.screenshot_png("LOAD_FLOW", 1), layout.screenshot_png("LOAD_FLOW", 2))

    @unittest.skipUnless(hasattr(os, "symlink"), "symlinks unavailable")
    def test_planted_symlink_escape_is_rejected(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            layout = RunLayout.create(root / "evidence", root / "reports", NOW)
            step = layout.evidence_root / layout.run_id / "LOAD_FLOW"
            step.mkdir()
            try:
                os.symlink(root / "outside", step / "attempt-001", target_is_directory=True)
            except OSError:
                self.skipTest("symlink creation not permitted")
            with self.assertRaises(PathSafetyError):
                layout.checkpoint_json("LOAD_FLOW", 1)


class PersistenceTests(unittest.TestCase):
    def test_atomic_round_trip_and_collision_rejection(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            layout = RunLayout.create(root / "evidence", root / "reports", NOW)
            path = layout.checkpoint_json("OPEN_PROJECT", 1)
            result = completed(layout.screenshot_png("OPEN_PROJECT", 1))
            self.assertEqual(read_checkpoint(write_checkpoint_atomic(result, path)), result)
            before = path.read_bytes()
            with self.assertRaises(FileExistsError):
                write_checkpoint_atomic(result, path)
            self.assertEqual(path.read_bytes(), before)

    def test_partial_json_is_rejected(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "partial.json"
            path.write_text('{"step":', encoding="utf-8")
            with self.assertRaises(PersistenceError):
                read_checkpoint(path)

    def test_replace_failure_cleans_temporary_file(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            layout = RunLayout.create(root / "evidence", root / "reports", NOW)
            path = layout.checkpoint_json("OPEN_PROJECT", 1)
            result = completed(layout.screenshot_png("OPEN_PROJECT", 1))
            with patch("etap_automation.persistence.os.replace", side_effect=OSError("injected")):
                with self.assertRaises(PersistenceError):
                    write_checkpoint_atomic(result, path)
            self.assertFalse(path.exists())
            self.assertEqual(list(path.parent.glob("*.tmp")), [])


if __name__ == "__main__":
    unittest.main()
