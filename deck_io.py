"""Save and load decks to/from JSON."""

from __future__ import annotations

import json
import re
from pathlib import Path

from db import CardDB
from models import CardEntry, Deck, Group

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


def _printing_dict(card, deck: Deck) -> dict | None:
    idx = deck.get_printing_idx(card, "usd")
    if 0 <= idx < len(card.printings):
        p = card.printings[idx]
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
            "oracle_id": deck.commander.oracle_id,
            "printing": _printing_dict(deck.commander, deck),
        }
    if deck.partner:
        data["partner"] = {
            "oracle_id": deck.partner.oracle_id,
            "printing": _printing_dict(deck.partner, deck),
        }
    for entry in deck.entries.values():
        data["cards"].append({
            "oracle_id": entry.card.oracle_id,
            "printing": _printing_dict(entry.card, deck),
            "count": entry.count,
            "groups": sorted(entry.groups),
        })

    path.write_text(json.dumps(data, indent=2))
    return path


def _resolve_printing(card, printing_data: dict | None, deck: Deck) -> None:
    if not printing_data or not card.printings:
        return
    for i, p in enumerate(card.printings):
        if (
            p.set_code == printing_data.get("set_code")
            and p.collector_number == printing_data.get("collector_number")
            and p.finish == printing_data.get("finish")
        ):
            deck.selected_printings[card.oracle_id] = i
            return


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
            deck.commander = card
            _resolve_printing(card, cmd_data.get("printing"), deck)

    partner_data = data.get("partner")
    if partner_data:
        card = db.cards.get(partner_data["oracle_id"])
        if card:
            deck.partner = card
            _resolve_printing(card, partner_data.get("printing"), deck)

    for card_data in data.get("cards", []):
        card = db.cards.get(card_data["oracle_id"])
        if card is None:
            continue
        entry = CardEntry(card=card, count=card_data.get("count", 1))
        entry.groups = set(card_data.get("groups", []))
        deck.entries[card.oracle_id] = entry
        _resolve_printing(card, card_data.get("printing"), deck)

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
