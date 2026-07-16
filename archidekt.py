"""Archidekt sandbox exporter — opens the deck in the browser."""

from __future__ import annotations

import json
import urllib.parse
import webbrowser

from exporter import DeckExporter
from models import CardEntry, Deck, MAYBEBOARD

_SANDBOX_URL = "https://archidekt.com/sandbox?deck="


def _entry_dict(entry: CardEntry, category: str) -> dict | None:
    if not entry.card.printings:
        return None
    printing = entry.card.printings[entry.printing_idx]
    if not printing.scryfall_id:
        return None
    return {
        "c": category,
        "f": 0 if printing.finish == "nonfoil" else 1,
        "q": entry.count,
        "u": printing.scryfall_id,
    }


class ArchidektExporter(DeckExporter):
    @property
    def name(self) -> str:
        return "Archidekt (open in browser)"

    def export(self, deck: Deck) -> None:
        entries = []

        for role_entry in (deck.commander, deck.partner):
            if role_entry:
                d = _entry_dict(role_entry, "c")
                if d:
                    entries.append(d)

        for entry in deck.entries.values():
            category = "s" if entry.is_maybe() else "m"
            d = _entry_dict(entry, category)
            if d:
                entries.append(d)

        payload = json.dumps(entries, separators=(",", ":"))
        url = _SANDBOX_URL + urllib.parse.quote(payload)
        webbrowser.open(url)
