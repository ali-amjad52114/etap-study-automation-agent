from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
import hashlib
import threading
import zlib

import pytest

from etap_automation.models import CheckpointResult, CheckpointStatus, CheckpointStep
from etap_automation.paths import RunLayout
from orchestrator.engine import (
    CancellationNotTerminal,
    IllegalTransition,
    Orchestrator,
    WorkflowBlocked,
)
from orchestrator.state import CHECKPOINT_ORDER
from h_operator.adapter import (
    HDesktopAdapter,
    HOperatorError,
    MAX_SCREENSHOT_BYTES,
    ScreenshotResource,
    SessionBusyError,
    SessionTimeoutError,
)
from h_operator.fake_client import FakeHClient, FakeSessionScenario


def make_result(step: CheckpointStep, status: CheckpointStatus) -> CheckpointResult:
    return CheckpointResult(
        step=step,
        status=status,
        project="EXAMPLE",
        study=step.value,
        timestamp=datetime.now(UTC),
        screenshot=f"evidence/{step.value}.png" if status is CheckpointStatus.COMPLETED else None,
        error=None if status is CheckpointStatus.COMPLETED else "injected failure",
    )


@dataclass
class FakeRunner:
    failures: set[CheckpointStep] = field(default_factory=set)
    calls: list[tuple[CheckpointStep, int]] = field(default_factory=list)

    def run(self, step, attempt, layout):
        self.calls.append((step, attempt))
        status = CheckpointStatus.FAILED if step in self.failures else CheckpointStatus.COMPLETED
        return make_result(step, status)


@dataclass
class FakeRetryControl:
    terminal: bool = True
    calls: list[tuple] = field(default_factory=list)

    def cancel_and_wait_terminal(self, step, attempt):
        self.calls.append(("cancel", step, attempt))
        return self.terminal

    def reset(self, step):
        self.calls.append(("reset", step))


def layout(tmp_path):
    return RunLayout.create(tmp_path / "evidence", tmp_path / "reports", datetime.now(UTC))


def test_fixed_transition_order_is_exactly_the_readme_sequence(tmp_path) -> None:
    runner = FakeRunner()
    workflow = Orchestrator.create(layout(tmp_path), runner, FakeRetryControl())

    for expected in CHECKPOINT_ORDER:
        assert workflow.run_next().step is expected

    assert [step for step, _ in runner.calls] == list(CHECKPOINT_ORDER)
    with pytest.raises(IllegalTransition, match="already complete"):
        workflow.run_next()


@pytest.mark.parametrize("failed_step", list(CHECKPOINT_ORDER))
def test_failure_at_each_checkpoint_stops_every_downstream_call(tmp_path, failed_step) -> None:
    runner = FakeRunner(failures={failed_step})
    workflow = Orchestrator.create(layout(tmp_path), runner, FakeRetryControl())

    for step in CHECKPOINT_ORDER:
        result = workflow.run_next()
        if step is failed_step:
            assert result.status is CheckpointStatus.FAILED
            break
    with pytest.raises(WorkflowBlocked):
        workflow.run_next()

    assert [step for step, _ in runner.calls] == list(CHECKPOINT_ORDER[: CHECKPOINT_ORDER.index(failed_step) + 1])


@pytest.mark.parametrize("failed_step", list(CHECKPOINT_ORDER))
def test_retry_runs_only_failed_checkpoint_with_new_attempt(tmp_path, failed_step) -> None:
    runner = FakeRunner(failures={failed_step})
    control = FakeRetryControl()
    workflow = Orchestrator.create(layout(tmp_path), runner, control)
    for step in CHECKPOINT_ORDER:
        workflow.run_next()
        if step is failed_step:
            break
    before = list(runner.calls)
    runner.failures.clear()

    retried = workflow.retry(failed_step)

    assert retried.status is CheckpointStatus.COMPLETED
    assert runner.calls == before + [(failed_step, 2)]
    assert control.calls == [("cancel", failed_step, 1), ("reset", failed_step)]


def test_nonterminal_cancellation_blocks_reset_retry_and_runner(tmp_path) -> None:
    runner = FakeRunner(failures={CheckpointStep.OPEN_PROJECT})
    control = FakeRetryControl(terminal=False)
    workflow = Orchestrator.create(layout(tmp_path), runner, control)
    workflow.run_next()

    with pytest.raises(CancellationNotTerminal):
        workflow.retry(CheckpointStep.OPEN_PROJECT)

    assert control.calls == [("cancel", CheckpointStep.OPEN_PROJECT, 1)]
    assert runner.calls == [(CheckpointStep.OPEN_PROJECT, 1)]


def test_cannot_retry_completed_pending_or_nonblocking_checkpoint(tmp_path) -> None:
    runner = FakeRunner()
    workflow = Orchestrator.create(layout(tmp_path), runner, FakeRetryControl())
    workflow.run_next()

    with pytest.raises(IllegalTransition):
        workflow.retry(CheckpointStep.OPEN_PROJECT)
    with pytest.raises(IllegalTransition):
        workflow.retry(CheckpointStep.LOAD_FLOW)


def test_resume_from_disk_selects_same_next_step_and_attempt(tmp_path) -> None:
    run_layout = layout(tmp_path)
    first_runner = FakeRunner()
    control = FakeRetryControl()
    first = Orchestrator.create(run_layout, first_runner, control)
    first.run_next()
    first.run_next()

    restarted_runner = FakeRunner()
    restarted = Orchestrator.resume(run_layout, restarted_runner, control)

    assert restarted.run_next().step is CheckpointStep.COORDINATION
    assert restarted_runner.calls == [(CheckpointStep.COORDINATION, 1)]


def test_status_always_exposes_exactly_five_checkpoint_outcomes(tmp_path) -> None:
    workflow = Orchestrator.create(layout(tmp_path), FakeRunner(), FakeRetryControl())
    assert workflow.status() == {step.value: "pending" for step in CHECKPOINT_ORDER}

    workflow.run_next()
    status = workflow.status()
    assert list(status) == [step.value for step in CHECKPOINT_ORDER]
    assert len(status) == 5
    assert status["OPEN_PROJECT"] == "completed"
    assert all(status[step.value] == "pending" for step in CHECKPOINT_ORDER[1:])


def test_runner_returning_wrong_step_is_rejected_without_persistence(tmp_path) -> None:
    class WrongRunner:
        def run(self, step, attempt, run_layout):
            return make_result(CheckpointStep.ARC_FLASH, CheckpointStatus.COMPLETED)

    run_layout = layout(tmp_path)
    workflow = Orchestrator.create(run_layout, WrongRunner(), FakeRetryControl())

    with pytest.raises(IllegalTransition, match="different checkpoint"):
        workflow.run_next()
    assert not run_layout.checkpoint_json(CheckpointStep.OPEN_PROJECT, 1).exists()


def png_chunk(kind: bytes, data: bytes) -> bytes:
    return (
        len(data).to_bytes(4, "big")
        + kind
        + data
        + (zlib.crc32(kind + data) & 0xFFFFFFFF).to_bytes(4, "big")
    )


def valid_png(width: int = 1, height: int = 1, total_size: int | None = None) -> bytes:
    signature = b"\x89PNG\r\n\x1a\n"
    ihdr_data = width.to_bytes(4, "big") + height.to_bytes(4, "big") + b"\x08\x06\x00\x00\x00"
    fixed = signature + png_chunk(b"IHDR", ihdr_data) + png_chunk(b"IDAT", b"x")
    end = png_chunk(b"IEND", b"")
    if total_size is None:
        return fixed + end
    filler_length = total_size - len(fixed) - len(end) - 12
    assert filler_length >= 0
    return fixed + png_chunk(b"tEXt", b"x" * filler_length) + end


def completed_adapter(tmp_path, *, key="proof.png", payload=None):
    payload = valid_png() if payload is None else payload
    client = FakeHClient(
        FakeSessionScenario(
            statuses=("completed",),
            answer={
                "step": "OPEN_PROJECT",
                "status": "completed",
                "screenshot_key": key,
                "error": None,
                "observed_identity": "EXAMPLE",
                "visible_confirmation": True,
            },
            resources={("screenshots", key): payload},
        )
    )
    adapter = HDesktopAdapter(client, evidence_root=tmp_path)
    session_id = adapter.start("checkpoint", expected_step="OPEN_PROJECT")
    adapter.wait(timeout_seconds=1, poll_seconds=0)
    return adapter, session_id, payload


def test_owned_exact_screenshot_key_writes_atomically_with_matching_metadata(tmp_path) -> None:
    adapter, session_id, payload = completed_adapter(tmp_path)
    destination = tmp_path / "proof.png"

    saved, metadata = adapter.save_screenshot_with_metadata(
        ScreenshotResource(session_id, "proof.png"), destination
    )

    assert saved == destination.resolve()
    assert destination.read_bytes() == payload
    assert metadata.session_id == session_id
    assert metadata.resource_key == "proof.png"
    assert metadata.size_bytes == len(payload)
    assert metadata.sha256 == hashlib.sha256(payload).hexdigest()
    assert metadata.captured_at.tzinfo is not None
    assert list(tmp_path.glob(".proof.png.*.tmp")) == []


def test_screenshot_collision_never_overwrites_prior_bytes(tmp_path) -> None:
    adapter, session_id, _ = completed_adapter(tmp_path)
    destination = tmp_path / "proof.png"
    destination.write_bytes(b"prior immutable evidence")

    with pytest.raises(FileExistsError):
        adapter.save_screenshot_with_metadata(
            ScreenshotResource(session_id, "proof.png"), destination
        )

    assert destination.read_bytes() == b"prior immutable evidence"


def test_foreign_session_and_wrong_returned_key_are_rejected(tmp_path) -> None:
    adapter, session_id, _ = completed_adapter(tmp_path)
    with pytest.raises(HOperatorError, match="session started"):
        adapter.save_screenshot(
            ScreenshotResource("foreign", "proof.png"), tmp_path / "foreign.png"
        )
    with pytest.raises(HOperatorError, match="not returned"):
        adapter.save_screenshot(
            ScreenshotResource(session_id, "other.png"), tmp_path / "other.png"
        )


@pytest.mark.parametrize(
    "key",
    ["../proof.png", "folder/proof.png", r"folder\proof.png", "/proof.png", "proof.jpg", ""],
)
def test_screenshot_key_traversal_paths_and_wrong_type_are_rejected(tmp_path, key) -> None:
    adapter, session_id, _ = completed_adapter(tmp_path)
    with pytest.raises(HOperatorError):
        adapter.save_screenshot(ScreenshotResource(session_id, key), tmp_path / "saved.png")


def test_exactly_five_mib_valid_png_is_accepted_and_one_byte_more_rejected(tmp_path) -> None:
    exact = valid_png(total_size=MAX_SCREENSHOT_BYTES)
    adapter, session_id, _ = completed_adapter(tmp_path / "exact", payload=exact)
    adapter.save_screenshot(ScreenshotResource(session_id, "proof.png"), tmp_path / "exact" / "proof.png")

    oversized = valid_png(total_size=MAX_SCREENSHOT_BYTES + 1)
    other, other_id, _ = completed_adapter(tmp_path / "large", payload=oversized)
    with pytest.raises(HOperatorError, match="5 MiB"):
        other.save_screenshot(
            ScreenshotResource(other_id, "proof.png"), tmp_path / "large" / "proof.png"
        )


@pytest.mark.parametrize(
    "payload",
    [
        b"",
        valid_png()[:-12],
        valid_png(width=0),
        valid_png(width=16_385),
        b"not png",
    ],
    ids=["empty", "missing-iend", "zero-dimension", "absurd-dimension", "mislabeled"],
)
def test_invalid_png_integrity_leaves_no_output_or_temp(tmp_path, payload) -> None:
    adapter, session_id, _ = completed_adapter(tmp_path, payload=payload)
    destination = tmp_path / "proof.png"
    with pytest.raises(HOperatorError):
        adapter.save_screenshot(ScreenshotResource(session_id, "proof.png"), destination)
    assert not destination.exists()
    assert list(tmp_path.glob(".proof.png.*.tmp")) == []


def test_timeout_remains_primary_when_vendor_cancel_also_fails(tmp_path) -> None:
    class CancelFailClient(FakeHClient):
        def cancel(self, session_id):
            raise RuntimeError("vendor cancel detail")

    adapter = HDesktopAdapter(
        CancelFailClient(FakeSessionScenario(statuses=("running",))), evidence_root=tmp_path
    )
    adapter.start("checkpoint")

    with pytest.raises(SessionTimeoutError) as captured:
        adapter.wait(timeout_seconds=0.001, poll_seconds=0)

    assert isinstance(captured.value.__cause__, RuntimeError)
    replacement = HDesktopAdapter(FakeHClient(), evidence_root=tmp_path)
    replacement.start("replacement")
    replacement.cancel()


def test_concurrent_cancel_does_not_release_global_lease_until_waiter_exits(tmp_path) -> None:
    entered_status = threading.Event()
    release_status = threading.Event()

    class BlockingClient(FakeHClient):
        def get_status(self, session_id):
            entered_status.set()
            assert release_status.wait(timeout=2)
            return {"status": "interrupted", "error": "cancelled"}

    adapter = HDesktopAdapter(BlockingClient(), evidence_root=tmp_path)
    adapter.start("first")
    waiter = threading.Thread(
        target=lambda: adapter.wait(timeout_seconds=2, poll_seconds=0), daemon=True
    )
    waiter.start()
    assert entered_status.wait(timeout=1)
    adapter.cancel()

    replacement = HDesktopAdapter(FakeHClient(), evidence_root=tmp_path)
    try:
        with pytest.raises(SessionBusyError):
            replacement.start("must remain blocked")
    finally:
        release_status.set()
        waiter.join(timeout=2)
        if replacement._active_session_id is not None:
            replacement.cancel()
    assert not waiter.is_alive()
