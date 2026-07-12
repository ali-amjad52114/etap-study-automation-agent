"""Run the approved ETAP MVP once, stopping at the first failed checkpoint."""

from __future__ import annotations

from datetime import UTC, datetime
import json
import os
from pathlib import Path

from etap_automation.models import CheckpointStep
from etap_automation.paths import RunLayout
from etap_automation.settings import load_settings
from h_operator import HCheckpointRunner, HDesktopAdapter
from orchestrator import CHECKPOINT_ORDER, OperatorCheckpointRunner, Orchestrator
from reporting import ReportCheckpointRunner


class FirstPassRunner:
    def __init__(self, operator: OperatorCheckpointRunner, report: ReportCheckpointRunner):
        self._operator = operator
        self._report = report

    def run(self, step: CheckpointStep, attempt: int, layout: RunLayout):
        if step is CheckpointStep.REPORT:
            return self._report.run(step, attempt, layout)
        return self._operator.run(step, attempt, layout)


class NoRetryControl:
    """First-pass live runs stop on failure and never retry automatically."""

    def cancel_and_wait_terminal(self, step: CheckpointStep, attempt: int) -> bool:
        del step, attempt
        return False

    def reset(self, step: CheckpointStep) -> None:
        del step
        raise RuntimeError("automatic reset is disabled for the live MVP")


def main() -> int:
    settings = load_settings()
    os.environ["HAI_API_KEY"] = settings.hai_api_key.reveal()
    layout = RunLayout.create(settings.evidence_dir, Path("reports"), datetime.now(UTC))
    adapter = HDesktopAdapter.from_hai_sdk(
        region=settings.hai_region,
        evidence_root=layout.evidence_root,
    )
    runner = FirstPassRunner(
        OperatorCheckpointRunner(HCheckpointRunner(adapter)),
        ReportCheckpointRunner(settings.study_plan_path),
    )
    workflow = Orchestrator.create(layout, runner, NoRetryControl())

    print(json.dumps({"run_id": layout.run_id, "status": workflow.status()}), flush=True)
    for step in CHECKPOINT_ORDER:
        print(json.dumps({"starting": step.value}), flush=True)
        result = workflow.run_next()
        print(json.dumps(result.to_dict()), flush=True)
        if result.status.value != "completed":
            print(json.dumps({"stopped": step.value, "status": workflow.status()}), flush=True)
            return 1
    print(json.dumps({"completed": True, "status": workflow.status()}), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
