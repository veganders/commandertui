"""Save and load decks to/from JSON."""

from __future__ import annotations

import json
import re
from pathlib import Path

from db import CardDB
from models import CardEntry, CardRole, Deck, Group

DECKS_DIR = Path(__file__).parent / "data" / "decks"


def _slug(name: str) -> str:
    return re.sub(r"[^\w]+", "_", name).strip("_") or "deck"


def _unique_path(name: str) -> Path:
    DECKS_DIR.mkdir(parents=True, exist_ok=True)
    base = _slug(name)
    path = DECKS_DIR / f"{base}.json"
    if not path.exists():
        return path
    counter = 2
    while True:
        path = DECKS_DIR / f"{base}_{counter}.json"
        if not path.exists():
            return path
        counter += 1


def _printing_dict(entry: CardEntry) -> dict | None:
    idx = entry.printing_idx
    if 0 <= idx < len(entry.card.printings):
        p = entry.card.printings[idx]
        return {"set_code": p.set_code, "collector_number": p.collector_number, "finish": p.finish}
    return None


def save_deck(deck: Deck) -> Path:
    path = deck.save_path or _unique_path(deck.name or "deck")
    DECKS_DIR.mkdir(parents=True, exist_ok=True)

    data: dict = {
        "name": deck.name,
        "groups": [{"name": g.name, "permanent": g.permanent} for g in deck.groups],
        "commander": None,
        "partner": None,
        "cards": [],
    }
    if deck.commander:
        data["commander"] = {
            "oracle_id": deck.commander.card.oracle_id,
            "printing": _printing_dict(deck.commander),
        }
    if deck.partner:
        data["partner"] = {
            "oracle_id": deck.partner.card.oracle_id,
            "printing": _printing_dict(deck.partner),
        }
    for entry in deck.entries.values():
        data["cards"].append({
            "oracle_id": entry.card.oracle_id,
            "printing": _printing_dict(entry),
            "count": entry.count,
            "groups": sorted(entry.groups),
        })

    path.write_text(json.dumps(data, indent=2))
    return path


def _find_printing_idx(card, printing_data: dict | None) -> int:
    if not printing_data or not card.printings:
        return 0
    for i, p in enumerate(card.printings):
        if (
            p.set_code == printing_data.get("set_code")
            and p.collector_number == printing_data.get("collector_number")
            and p.finish == printing_data.get("finish")
        ):
            return i
    return 0


def load_deck(path: Path, db: CardDB) -> Deck:
    data = json.loads(path.read_text())

    groups = [
        Group(name=g["name"], permanent=g.get("permanent", False))
        for g in data.get("groups", [])
    ]
    deck = Deck(name=data.get("name"), save_path=path, groups=groups)

    cmd_data = data.get("commander")
    if cmd_data:
        card = db.cards.get(cmd_data["oracle_id"])
        if card:
            printing_idx = _find_printing_idx(card, cmd_data.get("printing"))
            deck.commander = CardEntry(card=card, role=CardRole.COMMANDER, printing_idx=printing_idx)

    partner_data = data.get("partner")
    if partner_data:
        card = db.cards.get(partner_data["oracle_id"])
        if card:
            printing_idx = _find_printing_idx(card, partner_data.get("printing"))
            deck.partner = CardEntry(card=card, role=CardRole.PARTNER, printing_idx=printing_idx)

    for card_data in data.get("cards", []):
        card = db.cards.get(card_data["oracle_id"])
        if card is None:
            continue
        printing_idx = _find_printing_idx(card, card_data.get("printing"))
        entry = CardEntry(card=card, count=card_data.get("count", 1), printing_idx=printing_idx)
        entry.groups = set(card_data.get("groups", []))
        deck.entries[card.oracle_id] = entry

    return deck


def list_decks() -> list[Path]:
    if not DECKS_DIR.exists():
        return []
    return sorted(DECKS_DIR.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)


def deck_display_name(path: Path) -> str:
    try:
        data = json.loads(path.read_text())
        name = data.get("name")
        if name:
            return name
    except Exception:
        pass
    return path.stem
