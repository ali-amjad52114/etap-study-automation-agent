from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from h_operator.adapter import HDesktopAdapter, HOperatorError, _HaiSdkClient
from h_operator.checkpoints import HCheckpointRunner
from h_operator.contracts import CheckpointCommand, OperatorStep
from h_operator.fake_client import FAKE_PNG, FakeHClient, FakeSessionScenario


SESSION = "session-123"
HOST = "agp.eu.hcompany.ai"
PATH = f"/api/v1/trajectories/{SESSION}/resources/screenshots/{SESSION}/actual-final.png"


def observation(source: str, *, media_type="image/png", image_type="url"):
    return {
        "type": "AgentEvent",
        "data": {
            "kind": "observation_event",
            "image": {"type": image_type, "media_type": media_type, "source": source},
        },
    }


class SessionsSurface:
    def __init__(self, events):
        self.events = events
        self.resource_calls = []

    def create_session(self, **kwargs):
        return SimpleNamespace(id=SESSION)

    def get_session_status(self, session_id):
        return SimpleNamespace(status="completed")

    def get_session(self, session_id):
        return SimpleNamespace(latest_answer=None)

    def get_session_resource(self, session_id, bucket, key):
        self.resource_calls.append((session_id, bucket, key))
        return FAKE_PNG

    def list_session_events(self, session_id, **kwargs):
        assert session_id == SESSION
        assert kwargs == {"size": 200, "sort": ["-timestamp"], "type": "AgentEvent"}
        return SimpleNamespace(items=self.events)

    def cancel_session(self, session_id):
        pass


def sdk(events):
    sessions = SessionsSurface(events)
    return _HaiSdkClient(SimpleNamespace(sessions=sessions)), sessions


def test_sdk_resource_iterator_is_consumed_as_bounded_bytes() -> None:
    client, sessions = sdk([])
    sessions.get_session_resource = lambda session_id, bucket, key: iter(
        [b"first-", b"second"]
    )

    assert client.get_resource(SESSION, "screenshots", "proof.png") == b"first-second"


def test_latest_safe_observation_exact_host_session_path_and_png_is_accepted() -> None:
    older = observation(f"https://{HOST}{PATH.replace('actual-final', 'older')}")
    client, _ = sdk([observation(f"https://{HOST}{PATH}"), older])

    assert client.discover_latest_screenshot(SESSION) == (
        "screenshots",
        f"{SESSION}/actual-final.png",
    )


@pytest.mark.parametrize(
    "source",
    [
        f"https://evil.example{PATH}",
        f"https://{HOST}{PATH.replace(SESSION, 'foreign-session')}",
        f"https://{HOST}/api/v1/trajectories/{SESSION}/resources/../{SESSION}/actual-final.png",
        f"https://{HOST}/api/v1/trajectories/{SESSION}/resources/screenshots/{SESSION}/actual-final.jpg",
        f"https://{HOST}/api/v1/trajectories/{SESSION}/resources/screenshots/{SESSION}/../actual-final.png",
        f"https://{HOST}{PATH}?redirect=evil",
        f"http://{HOST}{PATH}",
    ],
    ids=["foreign-host", "foreign-session", "bucket-escape", "non-png", "traversal", "query", "non-https"],
)
def test_unsafe_observation_resource_urls_are_rejected(source) -> None:
    client, _ = sdk([observation(source)])

    with pytest.raises(HOperatorError, match="no safe PNG observation"):
        client.discover_latest_screenshot(SESSION)


def test_foreign_bucket_is_rejected_before_fetch(tmp_path) -> None:
    key = f"{SESSION}/actual-final.png"
    fake = FakeHClient(
        FakeSessionScenario(
            statuses=("completed",),
            screenshot_resource=("files", key),
            resources={("files", key): FAKE_PNG},
        )
    )
    adapter = HDesktopAdapter(fake, evidence_root=tmp_path)
    adapter.start("checkpoint", expected_step="OPEN_PROJECT")
    result = adapter.wait(timeout_seconds=1, poll_seconds=0)

    with pytest.raises(HOperatorError, match="only screenshot resources"):
        adapter.save_screenshot(result.screenshot, tmp_path / "proof.png")


def test_semantic_screenshot_label_cannot_select_resource_and_derived_path_is_fetched(tmp_path) -> None:
    actual_key = f"{SESSION}/actual-final.png"
    scenario = FakeSessionScenario(
        statuses=("completed",),
        answer={
            "step": "OPEN_PROJECT",
            "status": "completed",
            "screenshot_key": "semantic-label-not-a-resource.png",
            "error": None,
            "observed_identity": "EXAMPLE",
            "visible_confirmation": True,
        },
        screenshot_resource=("screenshots", actual_key),
        resources={("screenshots", actual_key): FAKE_PNG},
    )
    fake = FakeHClient(scenario)
    adapter = HDesktopAdapter(fake, evidence_root=tmp_path)
    runner = HCheckpointRunner(adapter)
    command = CheckpointCommand(
        OperatorStep.OPEN_PROJECT,
        "EXAMPLE",
        Path(r"C:\ETAP Demo\Example-ANSI\EXAMPLE.OTI"),
    )

    outcome = runner.execute(command, tmp_path / "proof.png")

    assert outcome.status == "completed"
    assert outcome.evidence.key == actual_key
    assert outcome.evidence.session_id == fake.created[0]["id"]
    assert outcome.evidence.size == len(FAKE_PNG)
    assert fake.resources_requested == [
        (fake.created[0]["id"], "screenshots", actual_key)
    ]
    assert ("screenshots", "semantic-label-not-a-resource.png") not in scenario.resources
    assert (tmp_path / "proof.png").read_bytes() == FAKE_PNG


def test_no_observation_fails_completed_session() -> None:
    client, _ = sdk([])

    with pytest.raises(HOperatorError, match="no safe PNG observation"):
        client.discover_latest_screenshot(SESSION)


def test_non_observation_and_non_png_events_are_skipped_for_latest_valid_one() -> None:
    events = [
        {"type": "AgentEvent", "data": {"kind": "action_event"}},
        observation(f"https://{HOST}{PATH}", media_type="image/jpeg"),
        observation(f"https://{HOST}{PATH}"),
    ]
    client, _ = sdk(events)

    assert client.discover_latest_screenshot(SESSION)[1] == f"{SESSION}/actual-final.png"
