from __future__ import annotations

from enum import StrEnum

from etap_automation.models import CheckpointStep


CHECKPOINT_ORDER = (
    CheckpointStep.OPEN_PROJECT,
    CheckpointStep.LOAD_FLOW,
    CheckpointStep.COORDINATION,
    CheckpointStep.ARC_FLASH,
    CheckpointStep.REPORT,
)


class CheckpointOutcome(StrEnum):
    PENDING = "pending"
    COMPLETED = "completed"
    FAILED = "failed"

