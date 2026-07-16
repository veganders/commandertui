"""Color Scout — explore card availability by color identity before building."""

from __future__ import annotations

from typing import Optional

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal
from textual.screen import Screen
from textual.widgets import Input, Label, ListItem, ListView

from db import CardDB, parse_query
from models import Deck, Group
from search import MODE_GROUP, SearchScreen
from settings import Settings
from widgets import FilterSuggestions, QueryInput

_COLOR_ORDER = "WUBRG"

# All 32 color identity combinations (power set of WUBRG + colorless) with names.
# Four-color identities are named by what they exclude.
_ALL_IDENTITIES: list[tuple[frozenset[str], str]] = [
    (frozenset(),                        "Colorless"),
    (frozenset("W"),                     "White"),
    (frozenset("U"),                     "Blue"),
    (frozenset("B"),                     "Black"),
    (frozenset("R"),                     "Red"),
    (frozenset("G"),                     "Green"),
    (frozenset("WU"),                    "Azorius"),
    (frozenset("WB"),                    "Orzhov"),
    (frozenset("WR"),                    "Boros"),
    (frozenset("WG"),                    "Selesnya"),
    (frozenset("UB"),                    "Dimir"),
    (frozenset("UR"),                    "Izzet"),
    (frozenset("UG"),                    "Simic"),
    (frozenset("BR"),                    "Rakdos"),
    (frozenset("BG"),                    "Golgari"),
    (frozenset("RG"),                    "Gruul"),
    (frozenset("WUB"),                   "Esper"),
    (frozenset("WUR"),                   "Jeskai"),
    (frozenset("WUG"),                   "Bant"),
    (frozenset("WBR"),                   "Mardu"),
    (frozenset("WBG"),                   "Abzan"),
    (frozenset("WRG"),                   "Naya"),
    (frozenset("UBR"),                   "Grixis"),
    (frozenset("UBG"),                   "Sultai"),
    (frozenset("URG"),                   "Temur"),
    (frozenset("BRG"),                   "Jund"),
    (frozenset("WUBR"),                  "Non-Green"),
    (frozenset("WUBG"),                  "Non-Red"),
    (frozenset("WURG"),                  "Non-Black"),
    (frozenset("WBRG"),                  "Non-Blue"),
    (frozenset("UBRG"),                  "Non-White"),
    (frozenset("WUBRG"),                 "Five-Color"),
]


def _identity_str(identity: frozenset[str]) -> str:
    """Return WUBRG-ordered string, e.g. frozenset('BG') → 'BG'. Empty → 'C'."""
    s = "".join(c for c in _COLOR_ORDER if c in identity)
    return s or "C"


class ColorScoutScreen(Screen[None]):
    BINDINGS = [
        Binding("escape", "dismiss_screen", "Close"),
    ]

    CSS = """
    ColorScoutScreen { layout: vertical; background: $background; }
    #cs-bar {
        height: 3;
        padding: 0 1;
        background: $surface;
        border-bottom: solid $primary;
        align: left middle;
    }
    #cs-input { width: 1fr; }
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
        self._results: list[tuple[frozenset[str], str, int]] = []
        self._suggestions = FilterSuggestions(self, "#cs-input", "#cs-suggest", self._filter_candidates)

    def compose(self) -> ComposeResult:
        with Horizontal(id="cs-bar"):
            yield QueryInput(
                placeholder="Search — results show card count per color identity",
                id="cs-input",
                select_on_focus=False,
                delay=1.0,
            )
        yield ListView(id="cs-suggest", classes="filter-suggest")
        yield ListView(id="cs-list")

    def on_mount(self) -> None:
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
                lv = self.query_one("#cs-list", ListView)
                lv.focus()
                if self._results:
                    lv.index = 0
                event.stop()

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        if self._suggestions.handle_list_selected(event, self._run_search):
            return
        if event.list_view.id == "cs-list":
            self._open_search_for_current()

    def _run_search(self, query: str) -> None:
        self._query = query
        cards = self._db.query(parse_query(query))
        card_identities = [frozenset(c.color_identity) for c in cards]
        self._results = sorted(
            [
                (identity, name, sum(1 for ci in card_identities if ci <= identity))
                for identity, name in _ALL_IDENTITIES
            ],
            key=lambda r: r[2],
            reverse=True,
        )
        self._rebuild_list()

    def _rebuild_list(self) -> None:
        lv = self.query_one("#cs-list", ListView)
        old_idx = lv.index
        lv.clear()
        for identity, name, count in self._results:
            id_str = _identity_str(identity)
            lv.append(ListItem(Label(f"{id_str:<6}  {name:<14}  {count}")))
        if old_idx is not None and 0 <= old_idx < len(self._results):
            lv.index = old_idx

    def _open_search_for_current(self) -> None:
        lv = self.query_one("#cs-list", ListView)
        idx = lv.index
        if idx is None or not (0 <= idx < len(self._results)):
            return
        identity, _name, _count = self._results[idx]
        id_str = _identity_str(identity)
        query = self._query.strip()
        full_query = f"id:{id_str} ({query})" if query else f"id:{id_str}"
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
