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

__all__ = [
    "FakeHClient",
    "FakeSessionScenario",
    "HDesktopAdapter",
    "HClientProtocol",
    "HOperatorError",
    "ScreenshotResource",
    "SessionBusyError",
    "SessionResult",
    "SessionState",
    "SessionTimeoutError",
]
