"""Clipboard exporter — copies a text decklist to the system clipboard."""

from __future__ import annotations

import pyperclip

from exporter import DeckExporter
from models import Deck, MAYBEBOARD


class ClipboardExporter(DeckExporter):
    @property
    def name(self) -> str:
        return "Copy to clipboard"

    def export(self, deck: Deck) -> None:
        lines: list[str] = []

        if deck.commander or deck.partner:
            lines.append("Commander")
            for entry in (deck.commander, deck.partner):
                if entry:
                    lines.append(f"1 {entry.card.name}")
            lines.append("")

        main = [e for e in deck.entries.values() if not e.is_maybe()]
        if main:
            lines.append("Deck")
            for entry in sorted(main, key=lambda e: e.card.name):
                lines.append(f"{entry.count} {entry.card.name}")
            lines.append("")

        maybe = [e for e in deck.entries.values() if e.is_maybe()]
        if maybe:
            lines.append("Maybeboard")
            for entry in sorted(maybe, key=lambda e: e.card.name):
                lines.append(f"{entry.count} {entry.card.name}")

        pyperclip.copy("\n".join(lines).strip())
