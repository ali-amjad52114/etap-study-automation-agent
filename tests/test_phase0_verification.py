import json
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest

from etap_automation.models import (
    CheckpointResult,
    CheckpointStatus,
    CheckpointStep,
    StudyPlan,
)
from etap_automation.settings import load_settings
from h_operator.adapter import (
    HDesktopAdapter,
    HOperatorError,
    ScreenshotResource,
    SessionBusyError,
    SessionState,
    SessionTimeoutError,
)
from h_operator.fake_client import FAKE_PNG, FakeHClient, FakeSessionScenario


PLAN_PATH = Path("config/study_plan.json")


def canonical_plan() -> dict[str, object]:
    return json.loads(PLAN_PATH.read_text(encoding="utf-8"))


def test_canonical_plan_freezes_the_readme_mvp() -> None:
    plan = StudyPlan.from_json_file(PLAN_PATH)

    assert plan.project == "EXAMPLE"
    assert plan.project_file == r"C:\ETAP Demo\Example-ANSI\EXAMPLE.OTI"
    assert [(item.type, item.study_case, item.view) for item in plan.studies] == [
        ("load_flow", "Base Case", None),
        ("coordination", None, "Main Feeder"),
        ("arc_flash", "Normal Operation", None),
    ]


@pytest.mark.parametrize(
    "mutate",
    [
        lambda value: value.update(project="AnotherProject"),
        lambda value: value.update(project_file=r"C:\Other\OTHER.OTI"),
        lambda value: value["studies"].reverse(),
        lambda value: value["studies"].pop(),
        lambda value: value["studies"].append(value["studies"][0].copy()),
        lambda value: value["studies"].__setitem__(1, value["studies"][0].copy()),
        lambda value: value["studies"][0].update(study_case="Not Base Case"),
        lambda value: value["studies"][1].update(view="Not Main Feeder"),
        lambda value: value["studies"][2].update(study_case="Not Normal Operation"),
    ],
    ids=[
        "wrong-project",
        "wrong-project-file",
        "reordered",
        "missing-study",
        "extra-study",
        "duplicate-study",
        "wrong-load-flow-case",
        "wrong-coordination-view",
        "wrong-arc-flash-case",
    ],
)
def test_plan_rejects_deviations_from_the_approved_values(mutate) -> None:
    value = canonical_plan()
    mutate(value)

    with pytest.raises(ValueError):
        StudyPlan.from_dict(value)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda value: value.update(extra="feature"),
        lambda value: value["studies"][0].update(dynamic=True),
        lambda value: value["studies"][1].update(study_case="invented"),
        lambda value: value["studies"][2].update(view="invented"),
    ],
)
def test_plan_rejects_unknown_or_dynamic_fields(mutate) -> None:
    value = canonical_plan()
    mutate(value)

    with pytest.raises(ValueError):
        StudyPlan.from_dict(value)


@pytest.mark.parametrize("project", ["", "   ", None, 7])
def test_plan_rejects_invalid_project_names(project) -> None:
    value = canonical_plan()
    value["project"] = project

    with pytest.raises(ValueError):
        StudyPlan.from_dict(value)


@pytest.mark.parametrize("project_file", ["", "demo.txt", None, 7])
def test_plan_rejects_non_etap_project_files(project_file) -> None:
    value = canonical_plan()
    value["project_file"] = project_file

    with pytest.raises(ValueError):
        StudyPlan.from_dict(value)


def result(**overrides) -> CheckpointResult:
    values = {
        "step": CheckpointStep.LOAD_FLOW,
        "status": CheckpointStatus.COMPLETED,
        "project": "EXAMPLE",
        "study": "Base Case",
        "timestamp": datetime(2026, 7, 12, 12, 30, tzinfo=UTC),
        "screenshot": "evidence/20260712T123000Z/load-flow.png",
        "error": None,
    }
    values.update(overrides)
    return CheckpointResult(**values)


def test_completed_checkpoint_serializes_all_required_fields() -> None:
    serialized = result().to_dict()

    assert serialized == {
        "step": "LOAD_FLOW",
        "status": "completed",
        "project": "EXAMPLE",
        "study": "Base Case",
        "timestamp": "2026-07-12T12:30:00+00:00",
        "screenshot": "evidence/20260712T123000Z/load-flow.png",
        "error": None,
    }


def test_failed_checkpoint_requires_error_and_allows_no_screenshot() -> None:
    checkpoint = result(
        status=CheckpointStatus.FAILED,
        screenshot=None,
        error="ETAP study did not complete",
    )

    assert checkpoint.to_dict()["error"] == "ETAP study did not complete"


@pytest.mark.parametrize("screenshot", [None, ""])
def test_completed_checkpoint_requires_screenshot_path(screenshot) -> None:
    with pytest.raises(ValueError):
        result(screenshot=screenshot)


def test_completed_checkpoint_cannot_contain_error() -> None:
    with pytest.raises(ValueError):
        result(error="contradictory failure")


@pytest.mark.parametrize("error", [None, ""])
def test_failed_checkpoint_requires_nonempty_error(error) -> None:
    with pytest.raises(ValueError):
        result(status=CheckpointStatus.FAILED, screenshot=None, error=error)


@pytest.mark.parametrize("field", ["project", "study"])
@pytest.mark.parametrize("value", ["", "   "])
def test_checkpoint_requires_project_and_study_names(field, value) -> None:
    with pytest.raises(ValueError):
        result(**{field: value})


def test_checkpoint_rejects_naive_timestamp() -> None:
    with pytest.raises(ValueError):
        result(timestamp=datetime(2026, 7, 12, 12, 30))


def test_checkpoint_accepts_timezone_aware_timestamp() -> None:
    checkpoint = result(
        timestamp=datetime(2026, 7, 12, 5, 30, tzinfo=timezone(timedelta(hours=-7)))
    )

    assert checkpoint.timestamp.utcoffset() == timedelta(hours=-7)


def test_settings_defaults_to_fixed_local_paths() -> None:
    settings = load_settings(env={"HAI_API_KEY": "test-key"}, env_file=None)

    assert settings.study_plan_path == Path("config/study_plan.json")
    assert settings.evidence_dir == Path("evidence")


def test_process_environment_overrides_env_file_without_leaking_secret() -> None:
    with TemporaryDirectory() as directory:
        env_file = Path(directory) / ".env"
        env_file.write_text(
            "HAI_API_KEY=file-secret\nHAI_REGION=eu\nEVIDENCE_DIR=file-evidence\n",
            encoding="utf-8",
        )
        settings = load_settings(
            env={
                "HAI_API_KEY": "process-secret",
                "HAI_REGION": "us",
                "EVIDENCE_DIR": "process-evidence",
            },
            env_file=env_file,
        )

    assert settings.hai_api_key.reveal() == "process-secret"
    assert settings.hai_region == "us"
    assert settings.evidence_dir == Path("process-evidence")
    assert "process-secret" not in repr(settings)
    assert "process-secret" not in str(settings.hai_api_key)


@pytest.mark.parametrize("region", ["", "asia", "US-west"])
def test_settings_rejects_unknown_h_regions(region) -> None:
    with pytest.raises(ValueError):
        load_settings(env={"HAI_API_KEY": "test-key", "HAI_REGION": region}, env_file=None)


def test_checked_in_text_files_do_not_contain_a_plausible_hai_secret() -> None:
    checked_files = [
        Path("config/study_plan.json"),
        Path(".env.example"),
        *Path("tests").glob("*.json"),
    ]
    contents = "\n".join(path.read_text(encoding="utf-8") for path in checked_files)

    assert "file-secret" not in contents
    assert "process-secret" not in contents
    assert "HAI_API_KEY=hai-" not in contents


def test_adapter_starts_only_a_bounded_user_device_desktop_agent() -> None:
    client = FakeHClient()
    adapter = HDesktopAdapter(client)
    try:
        session_id = adapter.start("Open the approved project")
        created = client.created[0]

        assert created["id"] == session_id
        assert created["messages"] == "Open the approved project"
        assert created["agent"]["environments"] == [
            {"id": "etap-desktop", "kind": "desktop", "host": "user_device"}
        ]
        assert created["agent"]["answer_format"]["additionalProperties"] is False
    finally:
        adapter.cancel()


@pytest.mark.parametrize("instruction", ["", "   "])
def test_adapter_rejects_empty_instructions_before_session_creation(instruction) -> None:
    client = FakeHClient()

    with pytest.raises(ValueError):
        HDesktopAdapter(client).start(instruction)

    assert client.created == []


@pytest.mark.parametrize(
    "vendor_state",
    ["queued", "pending", "running", "paused", "idle", "awaiting_tool_results"],
)
def test_nonterminal_vendor_lifecycle_states_advance_to_completion(vendor_state) -> None:
    client = FakeHClient(FakeSessionScenario(statuses=(vendor_state, "completed")))
    adapter = HDesktopAdapter(client)
    adapter.start("bounded task")

    completed = adapter.wait(timeout_seconds=1, poll_seconds=0)

    assert completed.state is SessionState.COMPLETED
    assert completed.answer is not None


@pytest.mark.parametrize("vendor_state", ["failed", "timed_out", "interrupted"])
def test_terminal_vendor_failure_states_are_preserved(vendor_state) -> None:
    client = FakeHClient(
        FakeSessionScenario(
            statuses=(vendor_state,),
            answer=None,
            error="vendor failure",
            error_code="failure-code",
            outcome="failure",
        )
    )
    adapter = HDesktopAdapter(client)
    adapter.start("bounded task")

    terminal = adapter.wait(timeout_seconds=1, poll_seconds=0)

    assert terminal.state is SessionState(vendor_state)
    assert terminal.answer is None
    assert terminal.error == "vendor failure"
    assert terminal.error_code == "failure-code"
    assert terminal.outcome == "failure"


def test_completed_session_requires_schema_valid_structured_answer() -> None:
    client = FakeHClient(
        FakeSessionScenario(statuses=("completed",), answer={"unexpected": True})
    )
    adapter = HDesktopAdapter(client)
    adapter.start("bounded task")

    with pytest.raises(HOperatorError, match="unexpected fields"):
        adapter.wait(timeout_seconds=1, poll_seconds=0)


def test_timeout_cancels_vendor_session_and_releases_desktop_lock() -> None:
    client = FakeHClient(FakeSessionScenario(statuses=("running",)))
    adapter = HDesktopAdapter(client)
    session_id = adapter.start("bounded task")

    with pytest.raises(SessionTimeoutError):
        adapter.wait(timeout_seconds=0.001, poll_seconds=0)

    assert client.cancelled == [session_id]
    replacement = HDesktopAdapter(FakeHClient())
    replacement.start("next task")
    replacement.cancel()


def test_manual_cancel_stops_active_session_and_releases_lock() -> None:
    client = FakeHClient()
    adapter = HDesktopAdapter(client)
    session_id = adapter.start("bounded task")

    adapter.cancel()

    assert client.cancelled == [session_id]
    replacement = HDesktopAdapter(FakeHClient())
    replacement.start("next task")
    replacement.cancel()


def test_second_concurrent_desktop_session_is_rejected_before_vendor_call() -> None:
    first_client = FakeHClient()
    second_client = FakeHClient()
    first = HDesktopAdapter(first_client)
    second = HDesktopAdapter(second_client)
    first.start("first task")
    try:
        with pytest.raises(SessionBusyError):
            second.start("second task")
        assert second_client.created == []
    finally:
        first.cancel()


def test_screenshot_resource_is_retrieved_from_screenshot_bucket() -> None:
    png = FAKE_PNG
    client = FakeHClient(
        FakeSessionScenario(
            answer={
                "step": "OPEN_PROJECT",
                "status": "completed",
                "screenshot_key": "proof.png",
                "error": None,
                "observed_identity": "EXAMPLE",
                "visible_confirmation": True,
            },
            resources={("screenshots", "proof.png"): png},
        )
    )
    with TemporaryDirectory() as directory:
        evidence_root = Path(directory) / "evidence"
        adapter = HDesktopAdapter(client, evidence_root=evidence_root)
        session_id = adapter.start("capture proof")
        try:
            adapter.wait(timeout_seconds=1, poll_seconds=0)
            destination = evidence_root / "run-1" / "proof.png"
            saved = adapter.save_screenshot(
                ScreenshotResource(session_id, "proof.png"), destination
            )
            assert saved == destination.resolve()
            assert destination.read_bytes() == png
        finally:
            adapter.cancel()


def test_non_screenshot_resource_bucket_is_rejected() -> None:
    client = FakeHClient()
    adapter = HDesktopAdapter(client)

    with TemporaryDirectory() as directory, pytest.raises(HOperatorError):
        adapter.save_screenshot(
            ScreenshotResource("session", "secret.txt", bucket="files"),
            Path(directory) / "secret.txt",
        )


def test_screenshot_destination_outside_evidence_root_is_rejected() -> None:
    png = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR" + (b"\x00" * 17)
    client = FakeHClient(
        FakeSessionScenario(resources={("screenshots", "proof.png"): png})
    )
    with TemporaryDirectory() as directory:
        root = Path(directory)
        evidence_root = root / "evidence"
        adapter = HDesktopAdapter(client, evidence_root=evidence_root)
        session_id = adapter.start("capture proof")
        try:
            with pytest.raises(HOperatorError, match="outside the evidence root"):
                adapter.save_screenshot(
                    ScreenshotResource(session_id, "proof.png"), root / "escaped.png"
                )
        finally:
            adapter.cancel()


def test_screenshot_path_traversal_outside_evidence_root_is_rejected() -> None:
    client = FakeHClient()
    with TemporaryDirectory() as directory:
        evidence_root = Path(directory) / "evidence"
        adapter = HDesktopAdapter(client, evidence_root=evidence_root)
        session_id = adapter.start("capture proof")
        try:
            with pytest.raises(HOperatorError, match="outside the evidence root"):
                adapter.save_screenshot(
                    ScreenshotResource(session_id, "proof.png"),
                    evidence_root / ".." / "escaped.png",
                )
        finally:
            adapter.cancel()


def test_screenshot_requires_png_destination_extension() -> None:
    client = FakeHClient()
    with TemporaryDirectory() as directory:
        evidence_root = Path(directory) / "evidence"
        adapter = HDesktopAdapter(client, evidence_root=evidence_root)
        session_id = adapter.start("capture proof")
        try:
            with pytest.raises(HOperatorError, match=".png extension"):
                adapter.save_screenshot(
                    ScreenshotResource(session_id, "proof.png"), evidence_root / "proof.jpg"
                )
        finally:
            adapter.cancel()


@pytest.mark.parametrize(
    "payload",
    [
        b"",
        b"not-an-image",
        b"\x89PNG\r\n\x1a\n" + (b"\x00" * 25),
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rNOPE" + (b"\x00" * 17),
    ],
    ids=["empty", "wrong-signature", "missing-ihdr", "wrong-first-chunk"],
)
def test_empty_or_unreadable_screenshot_is_rejected_without_writing(payload) -> None:
    client = FakeHClient(
        FakeSessionScenario(
            answer={
                "step": "OPEN_PROJECT",
                "status": "completed",
                "screenshot_key": "proof.png",
                "error": None,
                "observed_identity": "EXAMPLE",
                "visible_confirmation": True,
            },
            resources={("screenshots", "proof.png"): payload},
        )
    )
    with TemporaryDirectory() as directory:
        evidence_root = Path(directory) / "evidence"
        destination = evidence_root / "proof.png"
        adapter = HDesktopAdapter(client, evidence_root=evidence_root)
        session_id = adapter.start("capture proof")
        try:
            adapter.wait(timeout_seconds=1, poll_seconds=0)
            with pytest.raises(HOperatorError, match="PNG|empty"):
                adapter.save_screenshot(
                    ScreenshotResource(session_id, "proof.png"), destination
                )

            assert not destination.exists()
        finally:
            adapter.cancel()
