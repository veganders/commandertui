"""Tag histogram screen."""

from __future__ import annotations

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import VerticalScroll
from textual.screen import Screen
from textual.widgets import Footer, Static

from db import CardDB
from models import Deck


class TagHistogramScreen(Screen[None]):
    BINDINGS = [
        Binding("escape", "dismiss_screen", "Close"),
        Binding("t", "toggle_mode", "Leaf / All tags"),
    ]

    CSS = """
    TagHistogramScreen { layout: vertical; background: $background; }
    #hist-header {
        height: 3;
        padding: 0 2;
        background: $surface;
        border-bottom: solid $primary;
        align: left middle;
    }
    #hist-scroll { height: 1fr; padding: 1 2; }
    """

    def __init__(self, db: CardDB, deck: Deck) -> None:
        super().__init__()
        self._db = db
        self._deck = deck
        self._leaf_only = False

    def compose(self) -> ComposeResult:
        yield Static("", id="hist-header")
        with VerticalScroll(id="hist-scroll"):
            yield Static("", id="hist-content")
        yield Footer()

    def on_mount(self) -> None:
        self._refresh()

    def _refresh(self) -> None:
        mode = "leaf tags" if self._leaf_only else "all tags (incl. ancestors)"
        self.query_one("#hist-header", Static).update(f"Tag Histogram — {mode}")
        counts = self._compute_counts()
        self.query_one("#hist-content", Static).update(self._build_content(counts))

    def _compute_counts(self) -> dict[str, int]:
        tag_source = self._db.leaf_tags if self._leaf_only else self._db.tags
        counts: dict[str, int] = {}
        oracle_ids: set[str] = set()
        for entry in (self._deck.commander, self._deck.partner):
            if entry:
                oracle_ids.add(entry.card.oracle_id)
        for entry in self._deck.entries.values():
            oracle_ids.add(entry.card.oracle_id)
        for oid in oracle_ids:
            for tag in tag_source.get(oid, []):
                counts[tag] = counts.get(tag, 0) + 1
        return counts

    def _build_content(self, counts: dict[str, int]) -> Text:
        if not counts:
            return Text("No tags found in current deck.")
        sorted_items = sorted(counts.items(), key=lambda x: (-x[1], x[0]))
        max_count = sorted_items[0][1]
        name_width = min(max(len(n) for n, _ in sorted_items), 36)
        count_width = len(str(max_count))
        t = Text()
        for name, count in sorted_items:
            t.append(f"{name:<{name_width}}  ", style="bold")
            t.append(f"{count:>{count_width}}\n", style="dim")
        return t

    def action_dismiss_screen(self) -> None:
        self.dismiss(None)

    def action_toggle_mode(self) -> None:
        self._leaf_only = not self._leaf_only
        self._refresh()
