from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from .models import CheckpointStep


class PathSafetyError(ValueError):
    """Raised when a requested artifact path leaves its configured root."""


@dataclass(frozen=True)
class RunLayout:
    evidence_root: Path
    report_root: Path
    run_id: str

    @classmethod
    def create(
        cls, evidence_root: Path, report_root: Path, now: datetime
    ) -> "RunLayout":
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("now must include a timezone")
        evidence = _prepare_root(evidence_root)
        reports = _prepare_root(report_root)
        base = now.astimezone(UTC).strftime("%Y%m%dT%H%M%S%fZ")

        # mkdir is the collision arbiter, so parallel creators cannot receive
        # the same run directory even under a deterministic clock.
        for index in range(1000):
            run_id = base if index == 0 else f"{base}-{index:03d}"
            evidence_run = _child(evidence, run_id)
            try:
                evidence_run.mkdir(exist_ok=False)
            except FileExistsError:
                continue
            try:
                if reports != evidence:
                    _child(reports, run_id).mkdir(exist_ok=False)
            except Exception:
                evidence_run.rmdir()
                raise
            return cls(evidence, reports, run_id)
        raise FileExistsError("could not allocate a unique run id")

    def checkpoint_json(self, step: CheckpointStep | str, attempt: int) -> Path:
        return self._attempt_dir(step, attempt) / "checkpoint.json"

    def screenshot_png(self, step: CheckpointStep | str, attempt: int) -> Path:
        return self._attempt_dir(step, attempt) / "screenshot.png"

    def report_path(self) -> Path:
        return _child(self.report_root, self.run_id, "draft-report.pdf")

    def _attempt_dir(self, step: CheckpointStep | str, attempt: int) -> Path:
        normalized = _step_name(step)
        if isinstance(attempt, bool) or not isinstance(attempt, int) or attempt < 1:
            raise ValueError("attempt must be a positive integer")
        path = _child(
            self.evidence_root,
            self.run_id,
            normalized,
            f"attempt-{attempt:03d}",
        )
        path.mkdir(parents=True, exist_ok=True)
        # Resolve once more after creation to reject a planted symlink.
        return _child(self.evidence_root, self.run_id, normalized, f"attempt-{attempt:03d}")


def _step_name(step: CheckpointStep | str) -> str:
    try:
        return CheckpointStep(step).value
    except (TypeError, ValueError) as exc:
        raise ValueError("step must be an approved MVP checkpoint") from exc


def _prepare_root(root: Path) -> Path:
    if not isinstance(root, Path):
        root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    resolved = root.resolve(strict=True)
    if not resolved.is_dir():
        raise PathSafetyError("configured artifact root is not a directory")
    return resolved


def _child(root: Path, *parts: str) -> Path:
    if any(not part or Path(part).is_absolute() for part in parts):
        raise PathSafetyError("invalid artifact path component")
    candidate = root.joinpath(*parts).resolve(strict=False)
    if not candidate.is_relative_to(root):
        raise PathSafetyError("artifact path escapes its configured root")
    return candidate
