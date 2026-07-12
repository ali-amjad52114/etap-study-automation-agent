from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping


class Secret:
    """A small secret wrapper that cannot leak through repr or str."""

    def __init__(self, value: str) -> None:
        if not value:
            raise ValueError("HAI_API_KEY is required")
        self._value = value

    def reveal(self) -> str:
        return self._value

    def __repr__(self) -> str:
        return "Secret('**********')"

    __str__ = __repr__


@dataclass(frozen=True)
class Settings:
    hai_api_key: Secret
    hai_region: str
    study_plan_path: Path
    evidence_dir: Path


def _read_env_file(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    result: dict[str, str] = {}
    for line_number, raw_line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise ValueError(f"invalid environment entry on line {line_number}")
        key, value = line.split("=", 1)
        key = key.strip()
        if not key or not key.replace("_", "").isalnum():
            raise ValueError(f"invalid environment key on line {line_number}")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        result[key] = value
    return result


def load_settings(
    *, env: Mapping[str, str] | None = None, env_file: Path | None = Path(".env")
) -> Settings:
    values = _read_env_file(env_file) if env_file is not None else {}
    values.update(os.environ if env is None else env)

    region = values.get("HAI_REGION", "eu").lower()
    if region not in {"eu", "us"}:
        raise ValueError("HAI_REGION must be 'eu' or 'us'")

    return Settings(
        hai_api_key=Secret(values.get("HAI_API_KEY", "")),
        hai_region=region,
        study_plan_path=Path(values.get("STUDY_PLAN_PATH", "config/study_plan.json")),
        evidence_dir=Path(values.get("EVIDENCE_DIR", "evidence")),
    )

