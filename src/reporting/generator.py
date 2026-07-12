from __future__ import annotations

from datetime import UTC, datetime
import os
from pathlib import Path
import re
from tempfile import NamedTemporaryFile
from typing import Callable

from etap_automation.models import CheckpointResult, CheckpointStatus, CheckpointStep, StudyPlan
from etap_automation.paths import RunLayout
from etap_automation.persistence import read_checkpoint
from orchestrator.state import CHECKPOINT_ORDER

from .model import ReportEntry
from .validation import DRAFT_NOTICE, approved_screenshot


class ReportGenerationError(RuntimeError):
    pass


class ReportCheckpointRunner:
    """Build REPORT from approved inputs; never calls H or interprets results."""

    def __init__(
        self,
        plan_path: Path = Path("config/study_plan.json"),
        *,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self._plan_path = plan_path
        self._now = now or (lambda: datetime.now(UTC))

    def run(
        self, step: CheckpointStep, attempt: int, layout: RunLayout
    ) -> CheckpointResult:
        if step is not CheckpointStep.REPORT:
            raise ValueError("report runner accepts only REPORT")
        timestamp = self._now()
        if timestamp.tzinfo is None or timestamp.utcoffset() is None:
            raise ValueError("report clock must include a timezone")
        try:
            plan = StudyPlan.from_json_file(self._plan_path)
            entries = _load_entries(layout)
            target = layout.report_path()
            _write_pdf_atomic(target, plan, entries, layout.run_id, timestamp)
            return CheckpointResult(
                step=CheckpointStep.REPORT,
                status=CheckpointStatus.COMPLETED,
                project=plan.project,
                study="Draft report",
                timestamp=timestamp,
                # CheckpointResult's artifact field is named screenshot for the
                # MVP schema; REPORT stores its generated PDF path here.
                screenshot=str(target),
                error=None,
            )
        except Exception as exc:
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            return CheckpointResult(
                step=CheckpointStep.REPORT,
                status=CheckpointStatus.FAILED,
                project="EXAMPLE",
                study="Draft report",
                timestamp=timestamp,
                screenshot=None,
                error="draft report generation failed",
            )


def _load_entries(layout: RunLayout) -> list[ReportEntry]:
    entries: list[ReportEntry] = []
    run_root = layout.evidence_root / layout.run_id
    for step in CHECKPOINT_ORDER:
        if step is CheckpointStep.REPORT:
            entries.append(ReportEntry(step, "COMPLETED", None, None, "Draft report generated."))
            continue
        result = _latest_result(run_root, step)
        if result is None:
            entries.append(
                ReportEntry(step, "PENDING", None, None, "Missing evidence: checkpoint has no stored result.")
            )
            continue
        if result.status is CheckpointStatus.FAILED:
            cancelled = bool(result.error and "cancel" in result.error.lower())
            label = "CANCELLED" if cancelled else "FAILED"
            entries.append(
                ReportEntry(
                    step,
                    label,
                    result,
                    None,
                    "Missing evidence: checkpoint did not complete.",
                )
            )
            continue
        screenshot, message = approved_screenshot(result.screenshot, layout.evidence_root)
        entries.append(ReportEntry(step, "COMPLETED", result, screenshot, message))
    return entries


def _latest_result(run_root: Path, step: CheckpointStep) -> CheckpointResult | None:
    step_root = run_root / step.value
    if not step_root.exists():
        return None
    candidates: list[tuple[int, Path]] = []
    for child in step_root.iterdir():
        match = re.fullmatch(r"attempt-([0-9]+)", child.name)
        record = child / "checkpoint.json"
        if match and int(match.group(1)) > 0 and record.is_file():
            candidates.append((int(match.group(1)), record))
    if not candidates:
        return None
    result = read_checkpoint(max(candidates, key=lambda item: item[0])[1])
    if result.step is not step:
        raise ReportGenerationError("checkpoint record is stored under the wrong step")
    return result


def _write_pdf_atomic(
    target: Path,
    plan: StudyPlan,
    entries: list[ReportEntry],
    run_id: str,
    timestamp: datetime,
) -> None:
    try:
        from reportlab.lib import colors
        from reportlab.lib.pagesizes import letter
        from reportlab.lib.utils import ImageReader
        from reportlab.pdfgen import canvas
    except ImportError as exc:
        raise ReportGenerationError("reportlab is not installed") from exc

    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        raise FileExistsError("draft report already exists")
    temporary: Path | None = None
    lock = target.parent / f".{target.name}.lock"
    lock_fd: int | None = None
    try:
        lock_fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        with NamedTemporaryFile(dir=target.parent, prefix=f".{target.name}.", suffix=".tmp", delete=False) as handle:
            temporary = Path(handle.name)
        pdf = canvas.Canvas(str(temporary), pagesize=letter, invariant=1, pageCompression=0)
        width, height = letter

        def page_background() -> None:
            # An explicit white page avoids transparent-page rendering differences
            # across PDF viewers and image renderers.
            pdf.setFillColor(colors.white)
            pdf.rect(0, 0, width, height, fill=1, stroke=0)
            pdf.setFillColor(colors.black)

        def footer() -> None:
            pdf.setFont("Helvetica-Bold", 9)
            pdf.drawCentredString(width / 2, 24, DRAFT_NOTICE)

        pdf.setTitle("ETAP Study Automation Draft Report")
        page_background()
        pdf.setFont("Helvetica-Bold", 18)
        pdf.drawString(54, height - 64, "ETAP Study Automation Draft Report")
        pdf.setFont("Helvetica", 11)
        pdf.drawString(54, height - 92, f"Project: {plan.project}")
        pdf.drawString(54, height - 110, f"Run: {run_id}")
        pdf.drawString(54, height - 128, f"Generated: {timestamp.isoformat()}")
        pdf.drawString(54, height - 162, "Fixed sequence:")
        for index, item in enumerate(entries, 1):
            pdf.drawString(72, height - 162 - index * 18, f"{index}. {item.step.value}: {item.display_status}")
        footer()
        pdf.showPage()

        for entry in entries:
            page_background()
            pdf.setFont("Helvetica-Bold", 16)
            pdf.drawString(54, height - 64, entry.step.value)
            pdf.setFont("Helvetica-Bold", 11)
            pdf.drawString(54, height - 90, f"Status: {entry.display_status}")
            pdf.setFont("Helvetica", 10)
            pdf.drawString(54, height - 112, entry.evidence_message[:100])
            if entry.result is not None:
                pdf.drawString(54, height - 130, f"Study: {entry.result.study}"[:100])
                pdf.drawString(54, height - 148, f"Timestamp: {entry.result.timestamp.isoformat()}"[:100])
                if entry.result.error:
                    pdf.drawString(54, height - 166, f"Error: {entry.result.error}"[:100])
            if entry.screenshot is not None:
                try:
                    image = ImageReader(str(entry.screenshot))
                    iw, ih = image.getSize()
                    scale = min(500 / iw, 430 / ih, 1.0)
                    pdf.drawImage(image, 54, 80, iw * scale, ih * scale, preserveAspectRatio=True)
                except Exception:
                    pdf.drawString(54, height - 184, "Missing evidence: screenshot could not be rendered.")
            footer()
            pdf.showPage()
        pdf.save()
        # Windows requires a writable descriptor for FlushFileBuffers/fsync.
        with temporary.open("r+b") as handle:
            if handle.read(5) != b"%PDF-":
                raise ReportGenerationError("generated report is not a PDF")
            os.fsync(handle.fileno())
        if target.exists():
            raise FileExistsError("draft report already exists")
        os.replace(temporary, target)
        temporary = None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
        if lock_fd is not None:
            os.close(lock_fd)
            lock.unlink(missing_ok=True)
