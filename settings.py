"""Persistent user settings."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path

_SETTINGS_PATH = Path.home() / ".config" / "deckbuilder" / "settings.json"

_KNOWN: set[str] = {"currency"}


@dataclass
class Settings:
    currency: str = "usd"

    @classmethod
    def load(cls) -> "Settings":
        if not _SETTINGS_PATH.exists():
            return cls()
        try:
            with open(_SETTINGS_PATH) as f:
                data = json.load(f)
            return cls(**{k: v for k, v in data.items() if k in _KNOWN})
        except (json.JSONDecodeError, TypeError):
            return cls()

    def save(self) -> None:
        _SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(_SETTINGS_PATH, "w") as f:
            json.dump(asdict(self), f, indent=2)
