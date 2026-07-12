import importlib.util
import unittest
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory

from etap_automation.models import CheckpointResult, CheckpointStatus, CheckpointStep
from etap_automation.paths import RunLayout
from etap_automation.persistence import write_checkpoint_atomic
from reporting import DRAFT_NOTICE, ReportCheckpointRunner


NOW = datetime(2026, 7, 12, 12, 30, tzinfo=UTC)


@unittest.skipUnless(importlib.util.find_spec("reportlab"), "reportlab not installed")
class ReportingTests(unittest.TestCase):
    def test_partial_report_discloses_all_five_and_marks_every_page(self):
        if not importlib.util.find_spec("pypdf"):
            self.skipTest("pypdf not installed")
        from pypdf import PdfReader
        with TemporaryDirectory() as directory:
            root = Path(directory)
            layout = RunLayout.create(root / "evidence", root / "reports", NOW)
            failed = CheckpointResult(
                CheckpointStep.OPEN_PROJECT, CheckpointStatus.FAILED, "EXAMPLE", "EXAMPLE",
                NOW, None, "cancelled by operator",
            )
            write_checkpoint_atomic(failed, layout.checkpoint_json("OPEN_PROJECT", 1))
            runner = ReportCheckpointRunner(Path("config/study_plan.json"), now=lambda: NOW)
            result = runner.run(CheckpointStep.REPORT, 1, layout)
            self.assertIs(result.status, CheckpointStatus.COMPLETED)
            pages = PdfReader(result.screenshot).pages
            text = "\n".join(page.extract_text() or "" for page in pages)
            for step in CheckpointStep:
                self.assertIn(step.value, text)
            self.assertIn("CANCELLED", text)
            self.assertIn("Missing evidence", text)
            for page in pages:
                self.assertIn(DRAFT_NOTICE, page.extract_text() or "")

    def test_collision_returns_failure_and_preserves_existing_report(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            layout = RunLayout.create(root / "evidence", root / "reports", NOW)
            target = layout.report_path()
            target.write_bytes(b"prior")
            runner = ReportCheckpointRunner(Path("config/study_plan.json"), now=lambda: NOW)
            result = runner.run(CheckpointStep.REPORT, 1, layout)
            self.assertIs(result.status, CheckpointStatus.FAILED)
            self.assertEqual(target.read_bytes(), b"prior")


if __name__ == "__main__":
    unittest.main()
