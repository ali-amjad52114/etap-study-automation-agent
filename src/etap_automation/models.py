from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime
from enum import StrEnum
from pathlib import Path, PureWindowsPath
from typing import Any


MVP_PROJECT = "EXAMPLE"
MVP_PROJECT_FILE = r"C:\ETAP Demo\Example-ANSI\EXAMPLE.OTI"
MVP_STUDIES = (
    ("load_flow", "study_case", "Base Case"),
    ("coordination", "view", "Main Bus - Feeder 1"),
    ("arc_flash", "study_case", "Normal Operation"),
)


class CheckpointStep(StrEnum):
    OPEN_PROJECT = "OPEN_PROJECT"
    LOAD_FLOW = "LOAD_FLOW"
    COORDINATION = "COORDINATION"
    ARC_FLASH = "ARC_FLASH"
    REPORT = "REPORT"


class CheckpointStatus(StrEnum):
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass(frozen=True)
class Study:
    type: str
    study_case: str | None = None
    view: str | None = None


@dataclass(frozen=True)
class StudyPlan:
    project: str
    project_file: str
    studies: tuple[Study, ...]

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> StudyPlan:
        if set(value) != {"project", "project_file", "studies"}:
            raise ValueError("study plan must contain only project, project_file, and studies")
        if not isinstance(value["project"], str) or not value["project"].strip():
            raise ValueError("project must be a non-empty string")
        if value["project"] != MVP_PROJECT:
            raise ValueError(f"MVP project must be {MVP_PROJECT}")
        project_file = value["project_file"]
        if not isinstance(project_file, str) or PureWindowsPath(project_file).suffix.lower() != ".oti":
            raise ValueError("project_file must be an ETAP .OTI path")
        if project_file != MVP_PROJECT_FILE:
            raise ValueError(f"MVP project_file must be {MVP_PROJECT_FILE}")
        raw_studies = value["studies"]
        if not isinstance(raw_studies, list) or len(raw_studies) != 3:
            raise ValueError("MVP plan must contain exactly three studies")

        studies: list[Study] = []
        for raw, (study_type, selector, expected_value) in zip(
            raw_studies, MVP_STUDIES, strict=True
        ):
            if not isinstance(raw, dict) or set(raw) != {"type", selector}:
                raise ValueError(f"{study_type} must contain only type and {selector}")
            if raw["type"] != study_type:
                raise ValueError("studies must follow load_flow, coordination, arc_flash order")
            if not isinstance(raw[selector], str) or not raw[selector].strip():
                raise ValueError(f"{selector} must be a non-empty string")
            if raw[selector] != expected_value:
                raise ValueError(f"{study_type} {selector} must be {expected_value}")
            studies.append(Study(type=study_type, **{selector: raw[selector]}))
        return cls(value["project"], project_file, tuple(studies))

    @classmethod
    def from_json_file(cls, path: Path) -> StudyPlan:
        with path.open(encoding="utf-8") as handle:
            value = json.load(handle)
        if not isinstance(value, dict):
            raise ValueError("study plan root must be an object")
        return cls.from_dict(value)


@dataclass(frozen=True)
class CheckpointResult:
    step: CheckpointStep
    status: CheckpointStatus
    project: str
    study: str
    timestamp: datetime
    screenshot: str | None
    error: str | None

    def __post_init__(self) -> None:
        if self.timestamp.tzinfo is None:
            raise ValueError("timestamp must include a timezone")
        if not self.project.strip() or not self.study.strip():
            raise ValueError("project and study must be non-empty")
        if self.status is CheckpointStatus.COMPLETED:
            if not self.screenshot or self.error is not None:
                raise ValueError("completed checkpoint requires screenshot and no error")
        elif not self.error:
            raise ValueError("failed checkpoint requires an error")

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["step"] = self.step.value
        value["status"] = self.status.value
        value["timestamp"] = self.timestamp.isoformat()
        return value
