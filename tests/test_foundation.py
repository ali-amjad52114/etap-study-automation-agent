import json
import unittest
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory

from etap_automation.models import (
    CheckpointResult,
    CheckpointStatus,
    CheckpointStep,
    StudyPlan,
)
from etap_automation.settings import load_settings


class StudyPlanTests(unittest.TestCase):
    def test_repository_plan_has_exact_mvp_sequence(self) -> None:
        plan = StudyPlan.from_json_file(Path("config/study_plan.json"))
        self.assertEqual(plan.project_file, r"C:\ETAP Demo\Example-ANSI\EXAMPLE.OTI")
        self.assertEqual([study.type for study in plan.studies], ["load_flow", "coordination", "arc_flash"])

    def test_extra_study_is_rejected(self) -> None:
        value = json.loads(Path("config/study_plan.json").read_text(encoding="utf-8"))
        value["studies"].append({"type": "short_circuit", "study_case": "Base Case"})
        with self.assertRaises(ValueError):
            StudyPlan.from_dict(value)


class CheckpointResultTests(unittest.TestCase):
    def test_completed_result_requires_evidence(self) -> None:
        with self.assertRaises(ValueError):
            CheckpointResult(
                CheckpointStep.LOAD_FLOW,
                CheckpointStatus.COMPLETED,
                "EXAMPLE",
                "Base Case",
                datetime.now(UTC),
                None,
                None,
            )


class SettingsTests(unittest.TestCase):
    def test_environment_wins_and_secret_is_redacted(self) -> None:
        with TemporaryDirectory() as directory:
            env_path = Path(directory) / ".env"
            env_path.write_text("HAI_API_KEY=file-secret\nHAI_REGION=eu\n", encoding="utf-8")
            settings = load_settings(env={"HAI_API_KEY": "process-secret", "HAI_REGION": "us"}, env_file=env_path)
        self.assertEqual(settings.hai_api_key.reveal(), "process-secret")
        self.assertNotIn("process-secret", repr(settings))
        self.assertEqual(settings.hai_region, "us")

    def test_missing_key_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            load_settings(env={}, env_file=None)


if __name__ == "__main__":
    unittest.main()
