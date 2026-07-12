"""Typed single-checkpoint contracts for the fixed ETAP MVP."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from pathlib import Path


class OperatorStep(StrEnum):
    OPEN_PROJECT = "OPEN_PROJECT"
    LOAD_FLOW = "LOAD_FLOW"
    COORDINATION = "COORDINATION"
    ARC_FLASH = "ARC_FLASH"


APPROVED_PROJECT = "EXAMPLE"
APPROVED_PROJECT_FILE = Path(r"C:\ETAP Demo\Example-ANSI\EXAMPLE.OTI")


@dataclass(frozen=True)
class CheckpointCommand:
    """One bounded UI action; it deliberately carries no workflow sequence."""

    step: OperatorStep
    project: str
    project_file: Path
    study_case: str | None = None
    view: str | None = None
    timeout_seconds: int = 600
    max_steps: int = 80

    def __post_init__(self) -> None:
        if not isinstance(self.project_file, Path):
            object.__setattr__(self, "project_file", Path(self.project_file))
        if not self.project.strip():
            raise ValueError("project must not be empty")
        if self.project != APPROVED_PROJECT:
            raise ValueError("project must match the approved MVP project")
        if self.project_file.suffix.lower() != ".oti":
            raise ValueError("project_file must be an ETAP .oti file")
        if self.project_file != APPROVED_PROJECT_FILE:
            raise ValueError("project_file must match the approved MVP project file")
        if not 1 <= self.timeout_seconds <= 900:
            raise ValueError("timeout_seconds must be between 1 and 900")
        if not 1 <= self.max_steps <= 120:
            raise ValueError("max_steps must be between 1 and 120")

        case = self.study_case.strip() if self.study_case else None
        view = self.view.strip() if self.view else None
        if self.step is OperatorStep.OPEN_PROJECT and (case or view):
            raise ValueError("OPEN_PROJECT does not accept a study case or view")
        if self.step in {OperatorStep.LOAD_FLOW, OperatorStep.ARC_FLASH}:
            if not case or view:
                raise ValueError(f"{self.step} requires only a study case")
            approved_case = "Base Case" if self.step is OperatorStep.LOAD_FLOW else "Normal Operation"
            if case != approved_case:
                raise ValueError(f"{self.step} requires the approved study case")
        if self.step is OperatorStep.COORDINATION:
            if not view or case:
                raise ValueError("COORDINATION requires only a view")
            if view != "Main Bus - Feeder 1":
                raise ValueError("COORDINATION requires the approved view")

    @property
    def expected_observed_identity(self) -> str:
        """Exact visible label required for this checkpoint."""
        if self.step is OperatorStep.OPEN_PROJECT:
            return self.project
        if self.step is OperatorStep.COORDINATION:
            return str(self.view)
        return str(self.study_case)


@dataclass(frozen=True)
class EvidenceMetadata:
    session_id: str
    key: str
    size: int
    timestamp: datetime
    sha256: str

    @property
    def resource_key(self) -> str:
        return self.key

    @property
    def size_bytes(self) -> int:
        return self.size

    @property
    def captured_at(self) -> datetime:
        return self.timestamp


@dataclass(frozen=True)
class OperatorOutcome:
    step: OperatorStep
    status: str
    session_id: str
    screenshot: Path | None
    error: str | None
    error_code: str | None = None
    evidence: EvidenceMetadata | None = None
    observed_identity: str | None = None
    visible_confirmation: bool = False

    def __post_init__(self) -> None:
        if self.status not in {"completed", "failed", "cancelled"}:
            raise ValueError("invalid operator outcome status")
        if self.status == "completed" and (self.screenshot is None or self.error is not None):
            raise ValueError("completed outcome requires a screenshot and no error")
        if self.status == "completed" and (
            not self.observed_identity or self.visible_confirmation is not True
        ):
            raise ValueError("completed outcome requires a confirmed visible identity")
        if self.status != "completed" and not self.error:
            raise ValueError("failed/cancelled outcome requires an error")
