"""H local-desktop integration boundary for the ETAP MVP."""

from .adapter import (
    HDesktopAdapter,
    HClientProtocol,
    HOperatorError,
    ScreenshotResource,
    SessionBusyError,
    SessionResult,
    SessionState,
    SessionTimeoutError,
)
from .fake_client import FakeHClient, FakeSessionScenario
from .checkpoints import CheckpointRunner, HCheckpointRunner
from .contracts import CheckpointCommand, EvidenceMetadata, OperatorOutcome, OperatorStep
from .prompts import build_checkpoint_prompt

__all__ = [
    "FakeHClient",
    "FakeSessionScenario",
    "CheckpointCommand",
    "CheckpointRunner",
    "EvidenceMetadata",
    "HDesktopAdapter",
    "HClientProtocol",
    "HCheckpointRunner",
    "HOperatorError",
    "OperatorOutcome",
    "OperatorStep",
    "ScreenshotResource",
    "SessionBusyError",
    "SessionResult",
    "SessionState",
    "SessionTimeoutError",
    "build_checkpoint_prompt",
]
