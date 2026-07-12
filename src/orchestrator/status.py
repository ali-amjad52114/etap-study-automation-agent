from __future__ import annotations

from typing import Mapping

from etap_automation.models import CheckpointResult, CheckpointStatus, CheckpointStep

from .state import CHECKPOINT_ORDER, CheckpointOutcome


def project_status(
    latest: Mapping[CheckpointStep, CheckpointResult],
) -> dict[str, str]:
    """Return exactly the five README checkpoint outcomes."""
    return {
        step.value: (
            CheckpointOutcome.PENDING.value
            if step not in latest
            else (
                CheckpointOutcome.COMPLETED.value
                if latest[step].status is CheckpointStatus.COMPLETED
                else CheckpointOutcome.FAILED.value
            )
        )
        for step in CHECKPOINT_ORDER
    }

