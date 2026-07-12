from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import pytest

from etap_automation.models import CheckpointResult, CheckpointStatus, CheckpointStep
from etap_automation.paths import RunLayout
from h_operator.fake_client import FAKE_PNG
from orchestrator.engine import CancellationNotTerminal, Orchestrator, WorkflowBlocked
from orchestrator.state import CHECKPOINT_ORDER
from reporting import DRAFT_NOTICE, ReportCheckpointRunner


NOW = datetime(2026, 7, 12, 20, 0, tzinfo=UTC)
UI_STEPS = CHECKPOINT_ORDER[:-1]


@dataclass
class OfflineMvpRunner:
    report_runner: ReportCheckpointRunner
    failures: dict[CheckpointStep, str] = field(default_factory=dict)
    mismatches: dict[CheckpointStep, str] = field(default_factory=dict)
    missing_evidence: set[CheckpointStep] = field(default_factory=set)
    calls: list[tuple[CheckpointStep, int]] = field(default_factory=list)

    def run(self, step, attempt, layout):
        self.calls.append((step, attempt))
        if step in self.failures:
            return CheckpointResult(
                step, CheckpointStatus.FAILED, "EXAMPLE", step.value, NOW, None,
                self.failures[step],
            )
        if step in self.mismatches:
            return CheckpointResult(
                step, CheckpointStatus.FAILED, "EXAMPLE", step.value, NOW, None,
                self.mismatches[step],
            )
        if step is CheckpointStep.REPORT:
            return self.report_runner.run(step, attempt, layout)
        screenshot = layout.screenshot_png(step, attempt)
        if step not in self.missing_evidence:
            screenshot.write_bytes(FAKE_PNG)
        return CheckpointResult(
            step, CheckpointStatus.COMPLETED, "EXAMPLE", step.value, NOW,
            str(screenshot), None,
        )


@dataclass
class OfflineRetryControl:
    terminal: bool = True
    calls: list[tuple] = field(default_factory=list)

    def cancel_and_wait_terminal(self, step, attempt):
        self.calls.append(("cancel", step, attempt))
        return self.terminal

    def reset(self, step):
        self.calls.append(("reset", step))


def setup_workflow(tmp_path, **runner_options):
    layout = RunLayout.create(tmp_path / "evidence", tmp_path / "reports", NOW)
    reporter = ReportCheckpointRunner(now=lambda: NOW)
    runner = OfflineMvpRunner(reporter, **runner_options)
    retry = OfflineRetryControl()
    return layout, runner, retry, Orchestrator.create(layout, runner, retry)


@pytest.mark.skipif(pytest.importorskip("reportlab") is None, reason="reportlab unavailable")
def test_complete_offline_mvp_success_produces_real_pdf_and_five_completed_statuses(tmp_path) -> None:
    layout, runner, _, workflow = setup_workflow(tmp_path)

    results = [workflow.run_next() for _ in CHECKPOINT_ORDER]

    assert [result.step for result in results] == list(CHECKPOINT_ORDER)
    assert all(result.status is CheckpointStatus.COMPLETED for result in results)
    report = Path(results[-1].screenshot)
    assert report.read_bytes().startswith(b"%PDF-")
    assert workflow.status() == {step.value: "completed" for step in CHECKPOINT_ORDER}
    assert runner.calls == [(step, 1) for step in CHECKPOINT_ORDER]


@pytest.mark.parametrize("failed_step", list(CHECKPOINT_ORDER))
def test_failure_at_every_checkpoint_blocks_all_downstream_calls(tmp_path, failed_step) -> None:
    layout, runner, _, workflow = setup_workflow(
        tmp_path, failures={failed_step: "injected failure"}
    )
    for step in CHECKPOINT_ORDER:
        result = workflow.run_next()
        if step is failed_step:
            assert result.status is CheckpointStatus.FAILED
            break
    with pytest.raises(WorkflowBlocked):
        workflow.run_next()
    assert runner.calls == [
        (step, 1) for step in CHECKPOINT_ORDER[: CHECKPOINT_ORDER.index(failed_step) + 1]
    ]


@pytest.mark.parametrize("cancelled_step", list(CHECKPOINT_ORDER))
def test_cancelled_checkpoint_blocks_downstream_and_nonterminal_cancel_blocks_retry(
    tmp_path, cancelled_step
) -> None:
    _, runner, retry, workflow = setup_workflow(
        tmp_path, failures={cancelled_step: "cancelled by operator"}
    )
    for step in CHECKPOINT_ORDER:
        workflow.run_next()
        if step is cancelled_step:
            break
    retry.terminal = False
    with pytest.raises(CancellationNotTerminal):
        workflow.retry(cancelled_step)
    with pytest.raises(WorkflowBlocked):
        workflow.run_next()
    assert ("reset", cancelled_step) not in retry.calls


@pytest.mark.parametrize("failed_step", list(CHECKPOINT_ORDER))
def test_retry_only_failed_checkpoint_allocates_new_attempt_and_preserves_prior(tmp_path, failed_step) -> None:
    layout, runner, retry, workflow = setup_workflow(
        tmp_path, failures={failed_step: "injected failure"}
    )
    for step in CHECKPOINT_ORDER:
        workflow.run_next()
        if step is failed_step:
            break
    prior_records = {
        path: path.read_bytes() for path in layout.evidence_root.rglob("checkpoint.json")
    }
    runner.failures.clear()

    retried = workflow.retry(failed_step)

    assert retried.status is CheckpointStatus.COMPLETED
    assert runner.calls[-1] == (failed_step, 2)
    assert retry.calls == [("cancel", failed_step, 1), ("reset", failed_step)]
    assert all(path.read_bytes() == data for path, data in prior_records.items())


@pytest.mark.parametrize(
    ("reason", "message"),
    [
        ("observation", "visible observation mismatch"),
        ("session", "foreign session evidence rejected"),
        ("integrity", "screenshot integrity mismatch"),
    ],
)
def test_observation_session_and_evidence_mismatch_fail_closed(tmp_path, reason, message) -> None:
    _, runner, _, workflow = setup_workflow(
        tmp_path, mismatches={CheckpointStep.OPEN_PROJECT: message}
    )

    result = workflow.run_next()

    assert result.status is CheckpointStatus.FAILED
    assert reason in result.error
    with pytest.raises(WorkflowBlocked):
        workflow.run_next()
    assert runner.calls == [(CheckpointStep.OPEN_PROJECT, 1)]


def test_persistence_interruption_never_advances_and_resume_remains_pending(tmp_path, monkeypatch) -> None:
    layout, runner, retry, workflow = setup_workflow(tmp_path)

    def interrupted(*args, **kwargs):
        raise OSError("injected persistence interruption")

    monkeypatch.setattr("orchestrator.engine.write_checkpoint_atomic", interrupted)
    with pytest.raises(OSError, match="interruption"):
        workflow.run_next()
    assert workflow.status() == {step.value: "pending" for step in CHECKPOINT_ORDER}

    resumed = Orchestrator.resume(layout, runner, retry)
    assert resumed.status() == {step.value: "pending" for step in CHECKPOINT_ORDER}


def test_process_resume_continues_from_same_next_legal_checkpoint(tmp_path) -> None:
    layout, runner, retry, workflow = setup_workflow(tmp_path)
    workflow.run_next()
    workflow.run_next()

    restarted = Orchestrator.resume(layout, runner, retry)

    assert restarted.run_next().step is CheckpointStep.COORDINATION
    assert runner.calls[-1] == (CheckpointStep.COORDINATION, 1)


def test_missing_evidence_is_disclosed_in_real_partial_pdf(tmp_path) -> None:
    pytest.importorskip("reportlab")
    pypdf = pytest.importorskip("pypdf")
    _, _, _, workflow = setup_workflow(
        tmp_path, missing_evidence={CheckpointStep.LOAD_FLOW}
    )
    results = [workflow.run_next() for _ in CHECKPOINT_ORDER]
    pages = pypdf.PdfReader(results[-1].screenshot).pages
    text = "\n".join(page.extract_text() or "" for page in pages)

    assert "Missing evidence" in text
    assert "LOAD_FLOW" in text
    assert all(DRAFT_NOTICE in (page.extract_text() or "") for page in pages)
    assert len(workflow.status()) == 5
