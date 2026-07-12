from __future__ import annotations

import inspect
import hashlib
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from h_operator.adapter import HDesktopAdapter, HOperatorError, _HaiSdkClient
from h_operator.contracts import CheckpointCommand, OperatorStep
from h_operator.fake_client import FakeHClient, FakeSessionScenario
from h_operator.prompts import build_checkpoint_prompt
from etap_automation.models import CheckpointResult, CheckpointStatus, CheckpointStep
from etap_automation.paths import PathSafetyError, RunLayout
from etap_automation.persistence import PersistenceError, read_checkpoint, write_checkpoint_atomic


PROJECT_FILE = Path(r"C:\ETAP Demo\Example-ANSI\EXAMPLE.OTI")


def command(step: OperatorStep, **overrides) -> CheckpointCommand:
    values = {
        "step": step,
        "project": "EXAMPLE",
        "project_file": PROJECT_FILE,
    }
    if step is OperatorStep.LOAD_FLOW:
        values["study_case"] = "Base Case"
    elif step is OperatorStep.COORDINATION:
        values["view"] = "Main Feeder"
    elif step is OperatorStep.ARC_FLASH:
        values["study_case"] = "Normal Operation"
    values.update(overrides)
    return CheckpointCommand(**values)


def test_h_sdk_dependency_is_pinned_exactly_to_approved_version() -> None:
    pyproject = Path("pyproject.toml").read_text(encoding="utf-8")

    assert '"hai-agents[desktop]==1.0.6"' in pyproject
    assert "hai-agents[desktop]>=" not in pyproject


def test_sdk_wrapper_uses_only_the_frozen_sync_surface_and_arguments() -> None:
    calls: list[tuple[str, tuple, dict]] = []

    class Sessions:
        def create_session(self, **kwargs):
            calls.append(("create_session", (), kwargs))
            return SimpleNamespace(id="session-1")

        def get_session_status(self, session_id):
            calls.append(("get_session_status", (session_id,), {}))
            return SimpleNamespace(status="completed")

        def get_session(self, session_id):
            calls.append(("get_session", (session_id,), {}))
            return SimpleNamespace(latest_answer={"ok": True})

        def get_session_resource(self, session_id, bucket, key):
            calls.append(("get_session_resource", (session_id, bucket, key), {}))
            return b"resource"

        def cancel_session(self, session_id):
            calls.append(("cancel_session", (session_id,), {}))

    wrapper = _HaiSdkClient(SimpleNamespace(sessions=Sessions()))
    assert wrapper.create_session(
        agent={"name": "fixed"}, messages="prompt", max_steps=11, max_time_s=22, queue=False
    ) == "session-1"
    wrapper.get_status("session-1")
    wrapper.get_answer("session-1")
    wrapper.get_resource("session-1", "screenshots", "proof.png")
    wrapper.cancel("session-1")

    assert [item[0] for item in calls] == [
        "create_session",
        "get_session_status",
        "get_session",
        "get_session_resource",
        "cancel_session",
    ]
    assert calls[0][2] == {
        "agent": {"name": "fixed"},
        "messages": "prompt",
        "max_steps": 11,
        "max_time_s": 22,
        "queue": False,
    }


def test_adapter_forwards_exact_execution_bounds_without_queueing() -> None:
    client = FakeHClient()
    adapter = HDesktopAdapter(client)
    adapter.start("one checkpoint", max_steps=17, max_time_s=123)
    try:
        assert client.created[0]["max_steps"] == 17
        assert client.created[0]["max_time_s"] == 123
        assert client.created[0]["queue"] is False
    finally:
        adapter.cancel()


@pytest.mark.parametrize(
    ("field", "value"),
    [("max_steps", 0), ("max_steps", 121), ("max_time_s", 0), ("max_time_s", 901)],
)
def test_adapter_rejects_out_of_policy_bounds_before_vendor_creation(field, value) -> None:
    client = FakeHClient()
    adapter = HDesktopAdapter(client)

    with pytest.raises(ValueError):
        adapter.start("one checkpoint", **{field: value})

    assert client.created == []


def test_adapter_rejects_answer_for_a_different_expected_step() -> None:
    client = FakeHClient(
        FakeSessionScenario(
            statuses=("completed",),
            answer={
                "step": "ARC_FLASH",
                "status": "completed",
                "screenshot_key": "proof.png",
                "error": None,
                "observed_identity": "Normal Operation",
                "visible_confirmation": True,
            },
        )
    )
    adapter = HDesktopAdapter(client)
    adapter.start("load flow", expected_step="LOAD_FLOW")

    with pytest.raises(HOperatorError, match="expected 'LOAD_FLOW'"):
        adapter.wait(timeout_seconds=1, poll_seconds=0)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"study_case": "Base Case"},
        {"view": "Main Feeder"},
    ],
)
def test_open_project_rejects_irrelevant_selectors(kwargs) -> None:
    with pytest.raises(ValueError):
        command(OperatorStep.OPEN_PROJECT, **kwargs)


@pytest.mark.parametrize(
    ("step", "kwargs"),
    [
        (OperatorStep.LOAD_FLOW, {"study_case": None}),
        (OperatorStep.LOAD_FLOW, {"view": "Main Feeder"}),
        (OperatorStep.COORDINATION, {"view": None}),
        (OperatorStep.COORDINATION, {"study_case": "Base Case"}),
        (OperatorStep.ARC_FLASH, {"study_case": None}),
        (OperatorStep.ARC_FLASH, {"view": "Main Feeder"}),
    ],
)
def test_study_commands_require_only_their_relevant_selector(step, kwargs) -> None:
    with pytest.raises(ValueError):
        command(step, **kwargs)


@pytest.mark.parametrize("step", list(OperatorStep))
def test_command_bounds_are_frozen(step) -> None:
    with pytest.raises(ValueError):
        command(step, max_steps=0)
    with pytest.raises(ValueError):
        command(step, max_steps=121)
    with pytest.raises(ValueError):
        command(step, timeout_seconds=0)
    with pytest.raises(ValueError):
        command(step, timeout_seconds=901)


EXPECTED_PROMPTS = {
    OperatorStep.OPEN_PROJECT: (
        'Execute exactly one checkpoint: OPEN_PROJECT. Open only "C:\\ETAP Demo\\Example-ANSI\\EXAMPLE.OTI" '
        'and verify the visible project name is exactly "EXAMPLE". Do not edit the electrical model, study '
        'settings, cases, equipment, or files. Do not run any other study and do not interpret, approve, or '
        'recommend engineering results. If any identity, case, or view does not match exactly, stop and return '
        'failed. Capture one PNG screenshot of the visible final state and return only the required structured answer.'
    ),
    OperatorStep.LOAD_FLOW: (
        'Execute exactly one checkpoint: LOAD_FLOW. In project "EXAMPLE", select only Load Flow study case '
        '"Base Case", run it in ETAP, and show its result view. Do not edit the electrical model, study settings, '
        'cases, equipment, or files. Do not run any other study and do not interpret, approve, or recommend '
        'engineering results. If any identity, case, or view does not match exactly, stop and return failed. '
        'Capture one PNG screenshot of the visible final state and return only the required structured answer.'
    ),
    OperatorStep.COORDINATION: (
        'Execute exactly one checkpoint: COORDINATION. In project "EXAMPLE", open only the existing protection '
        'coordination view "Main Feeder" and show it. Do not edit the electrical model, study settings, cases, '
        'equipment, or files. Do not run any other study and do not interpret, approve, or recommend engineering '
        'results. If any identity, case, or view does not match exactly, stop and return failed. Capture one PNG '
        'screenshot of the visible final state and return only the required structured answer.'
    ),
    OperatorStep.ARC_FLASH: (
        'Execute exactly one checkpoint: ARC_FLASH. In project "EXAMPLE", select only AC Arc Flash study case '
        '"Normal Operation", run it in ETAP, and show its result view. Do not edit the electrical model, study '
        'settings, cases, equipment, or files. Do not run any other study and do not interpret, approve, or '
        'recommend engineering results. If any identity, case, or view does not match exactly, stop and return '
        'failed. Capture one PNG screenshot of the visible final state and return only the required structured answer.'
    ),
}

# These sentences are safety-critical parts of the frozen contract for every
# checkpoint, while the target-specific text above remains independently clear.
EXPECTED_PROMPTS = {
    step: prompt.replace(
        "or files. Do not run",
        "or files. Use visible UI actions only. Do not run",
    ).replace(
        "stop and return failed. Capture",
        "stop and return failed. Claim success only when a final visible observation confirms the expected state. Capture",
    )
    for step, prompt in EXPECTED_PROMPTS.items()
}
EXPECTED_PROMPTS = {
    step: prompt.replace(
        "Capture one PNG screenshot",
        f'Return observed_identity exactly as the visible label "{command(step).expected_observed_identity}"; '
        "set visible_confirmation true only after that final observation. Report only UI identity and completion "
        "evidence; do not infer any engineering result value. Capture one PNG screenshot",
    )
    for step, prompt in EXPECTED_PROMPTS.items()
}


@pytest.mark.parametrize("step", list(OperatorStep))
def test_checkpoint_prompt_matches_frozen_golden_contract(step) -> None:
    assert build_checkpoint_prompt(command(step)) == EXPECTED_PROMPTS[step]


def test_coordination_prompt_does_not_invent_a_calculation_run() -> None:
    prompt = build_checkpoint_prompt(command(OperatorStep.COORDINATION)).lower()

    assert "run coordination" not in prompt
    assert "calculate coordination" not in prompt
    assert "main feeder" in prompt


@pytest.mark.parametrize("step", list(OperatorStep))
def test_prompt_contains_safety_and_evidence_contract(step) -> None:
    prompt = build_checkpoint_prompt(command(step)).lower()

    assert "do not edit the electrical model" in prompt
    assert "do not run any other study" in prompt
    assert "do not interpret, approve, or recommend" in prompt
    assert "stop and return failed" in prompt
    assert "png screenshot" in prompt
    assert "structured answer" in prompt


def checkpoint_result(**overrides) -> CheckpointResult:
    values = {
        "step": CheckpointStep.LOAD_FLOW,
        "status": CheckpointStatus.COMPLETED,
        "project": "EXAMPLE",
        "study": "Base Case",
        "timestamp": datetime(2026, 7, 12, 18, 0, tzinfo=UTC),
        "screenshot": "evidence/run/LOAD_FLOW/attempt-001/screenshot.png",
        "error": None,
    }
    values.update(overrides)
    return CheckpointResult(**values)


def test_run_layout_allocates_collision_resistant_ids_for_same_clock(tmp_path) -> None:
    now = datetime(2026, 7, 12, 18, 0, 0, 123456, tzinfo=UTC)

    first = RunLayout.create(tmp_path / "evidence", tmp_path / "reports", now)
    second = RunLayout.create(tmp_path / "evidence", tmp_path / "reports", now)

    assert first.run_id == "20260712T180000123456Z"
    assert second.run_id == "20260712T180000123456Z-001"
    assert first.run_id != second.run_id


def test_run_layout_normalizes_non_utc_clock_to_utc(tmp_path) -> None:
    from datetime import timedelta, timezone

    layout = RunLayout.create(
        tmp_path / "evidence",
        tmp_path / "reports",
        datetime(2026, 7, 12, 11, 0, tzinfo=timezone(timedelta(hours=-7))),
    )

    assert layout.run_id.startswith("20260712T180000")


def test_run_layout_rejects_naive_clock(tmp_path) -> None:
    with pytest.raises(ValueError, match="timezone"):
        RunLayout.create(
            tmp_path / "evidence", tmp_path / "reports", datetime(2026, 7, 12, 18, 0)
        )


@pytest.mark.parametrize("attempt", [0, -1, True, 1.5, "1"])
def test_attempt_identity_requires_positive_integer(tmp_path, attempt) -> None:
    layout = RunLayout.create(
        tmp_path / "evidence", tmp_path / "reports", datetime.now(UTC)
    )

    with pytest.raises(ValueError, match="positive integer"):
        layout.checkpoint_json(CheckpointStep.LOAD_FLOW, attempt)


@pytest.mark.parametrize("step", ["../escape", r"C:\escape", "load_flow", "UNKNOWN", ""])
def test_artifact_paths_reject_traversal_absolute_and_unknown_steps(tmp_path, step) -> None:
    layout = RunLayout.create(
        tmp_path / "evidence", tmp_path / "reports", datetime.now(UTC)
    )

    with pytest.raises(ValueError, match="approved MVP checkpoint"):
        layout.checkpoint_json(step, 1)


def test_all_layout_outputs_remain_under_configured_roots(tmp_path) -> None:
    layout = RunLayout.create(
        tmp_path / "evidence", tmp_path / "reports", datetime.now(UTC)
    )

    checkpoint = layout.checkpoint_json(CheckpointStep.LOAD_FLOW, 1)
    screenshot = layout.screenshot_png(CheckpointStep.LOAD_FLOW, 1)
    report = layout.report_path()

    assert checkpoint.is_relative_to((tmp_path / "evidence").resolve())
    assert screenshot.is_relative_to((tmp_path / "evidence").resolve())
    assert report.is_relative_to((tmp_path / "reports").resolve())


def test_symlink_escape_from_attempt_directory_is_rejected(tmp_path) -> None:
    evidence = tmp_path / "evidence"
    layout = RunLayout.create(evidence, tmp_path / "reports", datetime.now(UTC))
    step_dir = evidence / layout.run_id / "LOAD_FLOW"
    step_dir.mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    link = step_dir / "attempt-001"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"symlink creation unavailable: {exc}")

    with pytest.raises(PathSafetyError, match="escapes"):
        layout.checkpoint_json(CheckpointStep.LOAD_FLOW, 1)


def test_retry_uses_new_attempt_paths_without_mutating_prior_evidence(tmp_path) -> None:
    layout = RunLayout.create(
        tmp_path / "evidence", tmp_path / "reports", datetime.now(UTC)
    )
    prior = layout.screenshot_png(CheckpointStep.LOAD_FLOW, 1)
    prior.write_bytes(b"immutable prior evidence")
    before = hashlib.sha256(prior.read_bytes()).hexdigest()

    retry = layout.screenshot_png(CheckpointStep.LOAD_FLOW, 2)
    retry.write_bytes(b"retry evidence")

    assert retry != prior
    assert hashlib.sha256(prior.read_bytes()).hexdigest() == before


def test_atomic_checkpoint_round_trip_and_collision_rejection(tmp_path) -> None:
    layout = RunLayout.create(
        tmp_path / "evidence", tmp_path / "reports", datetime.now(UTC)
    )
    destination = layout.checkpoint_json(CheckpointStep.LOAD_FLOW, 1)
    expected = checkpoint_result()

    assert write_checkpoint_atomic(expected, destination) == destination
    assert read_checkpoint(destination) == expected
    with pytest.raises(FileExistsError):
        write_checkpoint_atomic(expected, destination)


@pytest.mark.parametrize("content", ["", "{", "[]", '{"step":"LOAD_FLOW"}'])
def test_partial_or_schema_invalid_json_is_never_accepted(tmp_path, content) -> None:
    path = tmp_path / "checkpoint.json"
    path.write_text(content, encoding="utf-8")

    with pytest.raises(PersistenceError):
        read_checkpoint(path)


def test_replace_failure_leaves_no_final_or_temporary_record(tmp_path, monkeypatch) -> None:
    layout = RunLayout.create(
        tmp_path / "evidence", tmp_path / "reports", datetime.now(UTC)
    )
    destination = layout.checkpoint_json(CheckpointStep.LOAD_FLOW, 1)

    def fail_replace(source, target):
        raise OSError("injected replace failure")

    monkeypatch.setattr("etap_automation.persistence.os.replace", fail_replace)
    with pytest.raises(PersistenceError, match="write failed"):
        write_checkpoint_atomic(checkpoint_result(), destination)

    assert not destination.exists()
    assert list(destination.parent.glob(".checkpoint.json.*.tmp")) == []


def test_restart_read_produces_same_checkpoint_state(tmp_path) -> None:
    layout = RunLayout.create(
        tmp_path / "evidence", tmp_path / "reports", datetime.now(UTC)
    )
    destination = layout.checkpoint_json(CheckpointStep.LOAD_FLOW, 1)
    expected = checkpoint_result()
    write_checkpoint_atomic(expected, destination)

    first_process = read_checkpoint(destination)
    restarted_process = read_checkpoint(Path(str(destination)))

    assert restarted_process == first_process == expected
