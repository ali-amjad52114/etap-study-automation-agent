from __future__ import annotations

from pathlib import Path


DRAFT_NOTICE = "Draft - engineering review required"


def approved_screenshot(path_text: str | None, evidence_root: Path) -> tuple[Path | None, str]:
    if not path_text:
        return None, "Missing evidence: no screenshot was recorded."
    path = Path(path_text)
    if not path.is_absolute():
        path = Path.cwd() / path
    resolved = path.resolve(strict=False)
    root = evidence_root.resolve(strict=True)
    if not resolved.is_relative_to(root) or path.is_symlink():
        return None, "Rejected evidence: screenshot is outside the approved evidence root."
    try:
        payload = resolved.read_bytes()
    except OSError:
        return None, "Missing evidence: screenshot is unreadable or absent."
    if not payload.startswith(b"\x89PNG\r\n\x1a\n"):
        return None, "Rejected evidence: screenshot is not a readable PNG."
    return resolved, f"Available evidence: {resolved.name}"

