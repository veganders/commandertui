"""Persistent user settings."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path

_SETTINGS_PATH = Path.home() / ".config" / "deckbuilder" / "settings.json"

_SCALAR_KNOWN: set[str] = {
    "currency",
    "show_legal", "show_not_legal", "show_banned", "show_unreleased",
}


class RuleScope(str, Enum):
    GLOBAL = "global"
    LOCAL = "local"


@dataclass
class RoutingRule:
    query: str
    group: str
    enabled: bool = True
    scope: RuleScope = RuleScope.GLOBAL


_DEFAULT_ROUTES: list[RoutingRule] = [
    RoutingRule(query="t:land",       group="Lands"),
    RoutingRule(query="otag:ramp",    group="Ramp"),
    RoutingRule(query="otag:draw",    group="Draw"),
    RoutingRule(query="otag:removal", group="Interaction"),
]


@dataclass
class Settings:
    currency: str = "usd"
    show_legal: bool = True       # commander-legal, released cards
    show_not_legal: bool = False  # silver-border, Un sets, etc.
    show_banned: bool = False     # cards banned in Commander
    show_unreleased: bool = False # cards not yet released in paper
    routes: list[RoutingRule] = field(default_factory=lambda: list(_DEFAULT_ROUTES))

    @classmethod
    def load(cls) -> "Settings":
        if not _SETTINGS_PATH.exists():
            return cls()
        try:
            with open(_SETTINGS_PATH) as f:
                data = json.load(f)
            kwargs = {k: v for k, v in data.items() if k in _SCALAR_KNOWN}
            if "routes" not in data:
                # Old settings file — inject defaults
                kwargs["routes"] = list(_DEFAULT_ROUTES)
            else:
                kwargs["routes"] = [
                    RoutingRule(
                        query=r["query"], group=r["group"],
                        enabled=r.get("enabled", True),
                        scope=RuleScope(r.get("scope", RuleScope.GLOBAL)),
                    )
                    for r in data["routes"]
                ]
            return cls(**kwargs)
        except (json.JSONDecodeError, TypeError):
            return cls()

    def save(self) -> None:
        _SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(_SETTINGS_PATH, "w") as f:
            json.dump(asdict(self), f, indent=2)
