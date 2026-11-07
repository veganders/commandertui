"""Histogram — explore card counts by color identity, subtype, or oracle tag."""

from __future__ import annotations

from collections import Counter
from typing import Optional

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal
from textual.screen import Screen
from textual.widgets import DataTable, Footer, Input, Label, ListItem, ListView

from db import CardDB, parse_query
from tag_filter import get_noise_tags
from models import Deck, Group
from search import MODE_GROUP, SearchScreen
from settings import Settings
from widgets import FilterSuggestions, QueryInput

_COLOR_ORDER = "WUBRG"

# All 32 color identity combinations (power set of WUBRG + colorless) with names.
# Four-color identities are named by what they exclude.
_ALL_IDENTITIES: list[tuple[frozenset[str], str]] = [
    (frozenset(),        "Colorless"),
    (frozenset("W"),     "White"),
    (frozenset("U"),     "Blue"),
    (frozenset("B"),     "Black"),
    (frozenset("R"),     "Red"),
    (frozenset("G"),     "Green"),
    (frozenset("WU"),    "Azorius"),
    (frozenset("WB"),    "Orzhov"),
    (frozenset("WR"),    "Boros"),
    (frozenset("WG"),    "Selesnya"),
    (frozenset("UB"),    "Dimir"),
    (frozenset("UR"),    "Izzet"),
    (frozenset("UG"),    "Simic"),
    (frozenset("BR"),    "Rakdos"),
    (frozenset("BG"),    "Golgari"),
    (frozenset("RG"),    "Gruul"),
    (frozenset("WUB"),   "Esper"),
    (frozenset("WUR"),   "Jeskai"),
    (frozenset("WUG"),   "Bant"),
    (frozenset("WBR"),   "Mardu"),
    (frozenset("WBG"),   "Abzan"),
    (frozenset("WRG"),   "Naya"),
    (frozenset("UBR"),   "Grixis"),
    (frozenset("UBG"),   "Sultai"),
    (frozenset("URG"),   "Temur"),
    (frozenset("BRG"),   "Jund"),
    (frozenset("WUBR"),  "Non-Green"),
    (frozenset("WUBG"),  "Non-Red"),
    (frozenset("WURG"),  "Non-Black"),
    (frozenset("WBRG"),  "Non-Blue"),
    (frozenset("UBRG"),  "Non-White"),
    (frozenset("WUBRG"), "Five-Color"),
]

_MODE_COLOR   = "color"
_MODE_SUBTYPE = "subtype"
_MODE_TAG     = "tag"
_MODE_SEQUENCE = [_MODE_COLOR, _MODE_SUBTYPE, _MODE_TAG]
_MODE_LABELS = {
    _MODE_COLOR:   "Color Identity",
    _MODE_SUBTYPE: "Subtype",
    _MODE_TAG:     "Oracle Tag",
}


def _identity_str(identity: frozenset[str]) -> str:
    """Return WUBRG-ordered string, e.g. frozenset('BG') → 'BG'. Empty → 'C'."""
    s = "".join(c for c in _COLOR_ORDER if c in identity)
    return s or "C"


def _extract_subtypes(type_line: str) -> list[str]:
    """Return subtypes (everything after '—') from a type line."""
    if "—" not in type_line:
        return []
    return type_line.split("—", 1)[1].split()


class HistogramScreen(Screen[None]):
    BINDINGS = [
        Binding("escape", "dismiss_screen", "Close"),
        Binding("g", "cycle_mode", "Group by", priority=True),
    ]

    CSS = """
    HistogramScreen { layout: vertical; background: $background; }
    #cs-bar {
        height: 3;
        padding: 0 1;
        background: $surface;
        border-bottom: solid $primary;
        align: left middle;
    }
    #cs-input { width: 1fr; }
    #cs-mode { width: auto; padding: 0 1; color: $text-muted; }
    QueryInput.query-error { background: $error 25%; }
    QueryInput.query-error:focus { background: $error 35%; }
    #cs-list { width: 1fr; }
    """

    def __init__(
        self,
        db: CardDB,
        deck: Deck,
        settings: Settings,
        filter_candidates: Optional[dict] = None,
        group: Optional[Group] = None,
    ) -> None:
        super().__init__()
        self._db = db
        self._deck = deck
        self._settings = settings
        self._filter_candidates = filter_candidates or {}
        self._group = group
        self._query = ""
        self._mode = _MODE_COLOR
        self._cached_cards: list = []
        # Each row: (query_filter, display_label, count)
        self._results: list[tuple[str, str, int]] = []
        self._suggestions = FilterSuggestions(
            self, "#cs-input", "#cs-suggest", self._filter_candidates
        )

    def compose(self) -> ComposeResult:
        with Horizontal(id="cs-bar"):
            yield QueryInput(
                placeholder="Search — press g to change grouping",
                id="cs-input",
                select_on_focus=False,
                delay=1.0,
            )
            yield Label(_MODE_LABELS[self._mode], id="cs-mode")
        yield ListView(id="cs-suggest", classes="filter-suggest")
        yield DataTable(id="cs-list", cursor_type="row")
        yield Footer()

    def on_mount(self) -> None:
        table = self.query_one("#cs-list", DataTable)
        table.add_column("Name", key="name")
        table.add_column("Count", key="count")
        self._run_search("")
        self.query_one("#cs-input", QueryInput).focus()

    def on_input_changed(self, event: Input.Changed) -> None:
        self._suggestions.handle_input_changed(event)

    def on_query_input_debounced(self, event: QueryInput.Debounced) -> None:
        self._suggestions.handle_debounced(event, self._run_search)

    def on_key(self, event) -> None:
        if self._suggestions.handle_key(event, self._run_search):
            return
        if event.key in ("down", "tab") and isinstance(self.focused, Input):
            if not self._suggestions.visible:
                table = self.query_one("#cs-list", DataTable)
                table.focus()
                table.move_cursor(row=0)
                event.stop()

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        self._suggestions.handle_list_selected(event, self._run_search)

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        self._open_search_for_current()

    def action_cycle_mode(self) -> None:
        idx = _MODE_SEQUENCE.index(self._mode)
        self._mode = _MODE_SEQUENCE[(idx + 1) % len(_MODE_SEQUENCE)]
        self.query_one("#cs-mode", Label).update(_MODE_LABELS[self._mode])
        self._recompute(self._cached_cards)

    def _run_search(self, query: str) -> None:
        self._query = query
        self._cached_cards = self._db.query(parse_query(query))
        self._recompute(self._cached_cards)

    def _recompute(self, cards: list) -> None:
        if self._mode == _MODE_COLOR:
            card_identities = [frozenset(c.color_identity) for c in cards]
            self._results = sorted(
                [
                    (
                        f"id:{_identity_str(identity)}",
                        f"{_identity_str(identity):<7}{name}",
                        sum(1 for ci in card_identities if ci <= identity),
                    )
                    for identity, name in _ALL_IDENTITIES
                ],
                key=lambda r: r[2],
                reverse=True,
            )

        elif self._mode == _MODE_SUBTYPE:
            counts: Counter[str] = Counter()
            for card in cards:
                seen: set[str] = set()
                for face in card.faces:
                    for st in _extract_subtypes(face.type_line):
                        if st not in seen:
                            counts[st] += 1
                            seen.add(st)
            self._results = [
                (f"t:{st}", st.title(), count)
                for st, count in counts.most_common()
            ]

        elif self._mode == _MODE_TAG:
            noise = get_noise_tags(self._db)
            counts2: Counter[str] = Counter()
            for card in cards:
                for tag in self._db.get_leaf_tags(card.oracle_id):
                    if tag not in noise:
                        counts2[tag] += 1
            self._results = [
                (
                    f'otag:"{tag}"' if " " in tag else f"otag:{tag}",
                    tag,
                    count,
                )
                for tag, count in counts2.most_common()
            ]

        self._rebuild_list()

    def _rebuild_list(self) -> None:
        table = self.query_one("#cs-list", DataTable)
        old_row = table.cursor_row
        table.clear()
        for _filter, label, count in self._results:
            table.add_row(label, count)
        if self._results:
            table.move_cursor(row=min(old_row, len(self._results) - 1))

    def _open_search_for_current(self) -> None:
        table = self.query_one("#cs-list", DataTable)
        idx = table.cursor_row
        if not (0 <= idx < len(self._results)):
            return
        query_filter, _label, _count = self._results[idx]
        query = self._query.strip()
        full_query = f"{query_filter} ({query})" if query else query_filter
        self.app.push_screen(
            SearchScreen(
                self._db, self._deck, self._settings, MODE_GROUP,
                group=self._group,
                initial_query=full_query,
                filter_candidates=self._filter_candidates,
            )
        )

    def action_dismiss_screen(self) -> None:
        self.dismiss(None)
