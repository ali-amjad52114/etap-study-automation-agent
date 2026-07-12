from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from etap_automation.models import CheckpointResult, CheckpointStep


@dataclass(frozen=True)
class ReportEntry:
    step: CheckpointStep
    display_status: str
    result: CheckpointResult | None
    screenshot: Path | None
    evidence_message: str

