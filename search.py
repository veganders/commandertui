"""Card search screen."""

from __future__ import annotations

from typing import Callable, Optional

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal
from textual.screen import Screen
from textual.widgets import Input, Label, ListItem, ListView

from rich.text import Text
from db import And, Atom, Card, CardDB, Not, parse_query, validate_query
from models import MAYBEBOARD, CardEntry, CardRole, Deck, Group
from partner import partner_mode, partner_filter
from settings import Settings
from widgets import CardDetail, FilterSuggestions, QueryInput

MODE_COMMANDER = "commander"
MODE_PARTNER = "partner"
MODE_GROUP = "group"



class SearchScreen(Screen[str]):
    BINDINGS = [
        Binding("escape", "dismiss_screen", "Close"),
        Binding("space", "toggle_card", "Add/Remove"),
        Binding("m", "toggle_maybeboard", "Maybeboard"),
        Binding("+", "increment_card", "+1"),
        Binding("-", "decrement_card", "-1"),
    ]

    CSS = """
    SearchScreen { layout: vertical; background: $background; }
    #srch-bar {
        height: 3;
        padding: 0 1;
        background: $surface;
        border-bottom: solid $primary;
        align: left middle;
    }
    #srch-input { width: 1fr; }
    QueryInput.query-error { background: $error 25%; }
    QueryInput.query-error:focus { background: $error 35%; }
    #srch-suggest {
        display: none;
        height: auto;
        max-height: 10;
        width: 44;
        background: $surface;
        border: solid $primary;
    }
    #srch-bottom { height: 1fr; }
    #srch-list { width: 1fr; border-right: solid $primary; }
    SearchScreen CardDetail { width: 1fr; padding: 1 2; }
    .result-selected { color: $success; }
    """

    def __init__(
        self,
        db: CardDB,
        deck: Deck,
        settings: Settings,
        mode: str,
        group: Optional[Group] = None,
        post_filter: Optional[Callable[[Card], bool]] = None,
        title: Optional[str] = None,
        initial_query: str = "",
        filter_candidates: Optional[dict] = None,
    ) -> None:
        super().__init__()
        self._db = db
        self._deck = deck
        self._settings = settings
        self._mode = mode
        self._group = group
        self._post_filter = post_filter
        self._title = title
        self._initial_query = initial_query
        self._results: list[Card] = []
        self._current_card: Optional[Card] = None
        self._suggestions = FilterSuggestions(self, "#srch-input", "#srch-suggest", filter_candidates or {})

    def compose(self) -> ComposeResult:
        with Horizontal(id="srch-bar"):
            yield QueryInput(placeholder=self._placeholder(), id="srch-input", select_on_focus=False, delay=1.0)
        yield ListView(id="srch-suggest")
        with Horizontal(id="srch-bottom"):
            yield ListView(id="srch-list")
            yield CardDetail()

    def _placeholder(self) -> str:
        if self._title:
            base = self._title
        else:
            base = {
                MODE_COMMANDER: "Search for a commander",
                MODE_PARTNER: "Search for a partner",
                MODE_GROUP: f"Add cards to '{self._group.name}'" if self._group else "Search cards",
            }.get(self._mode, "Search cards")
        return base + "  —  t:type  o:oracle  kw:partner  id:wubrg  c:rg  otag:ramp  mv>=3  eur<=1  -t:land"

    def on_mount(self) -> None:
        inp = self.query_one("#srch-input", QueryInput)
        if self._initial_query:
            inp.value = self._initial_query
            inp.cursor_position = len(self._initial_query)
        self._run_search(self._initial_query)
        inp.focus()

    def on_input_changed(self, event: Input.Changed) -> None:
        self._suggestions.handle_input_changed(event)

    def on_query_input_debounced(self, event: QueryInput.Debounced) -> None:
        self._suggestions.handle_debounced(event, self._run_search)

    def on_key(self, event) -> None:
        if self._suggestions.handle_key(event, self._run_search):
            return
        if event.key in ("down", "tab") and isinstance(self.focused, Input):
            if not self._suggestions.visible:
                lv = self.query_one("#srch-list", ListView)
                lv.focus()
                if self._results:
                    lv.index = 0
                event.stop()

    def on_list_view_highlighted(self, event: ListView.Highlighted) -> None:
        if event.list_view.id != "srch-list":
            return
        idx = event.list_view.index
        card = self._results[idx] if idx is not None and 0 <= idx < len(self._results) else None
        self._current_card = card
        self.query_one(CardDetail).show_card(card, self._db, self._deck, self._settings)

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        self._suggestions.handle_list_selected(event, self._run_search)

    def on_card_detail_printing_selected(self, msg: CardDetail.PrintingSelected) -> None:
        entry = self._deck.get_entry_for_card(msg.oracle_id)
        if entry is not None:
            entry.printing_idx = msg.printing_idx
        else:
            self._deck.selected_printings[msg.oracle_id] = msg.printing_idx

    # ── search ─────────────────────────────────────────────────────────────────

    def _run_search(self, query: str) -> None:
        node = parse_query(query)
        implied = self._implied_node()
        if implied is not None:
            node = And([implied] + (node.children if isinstance(node, And) else [node]))

        results = self._db.query(node)

        if self._mode == MODE_COMMANDER:
            results = [
                c for c in results
                if (
                    "Legendary" in c.type_line
                    and ("Creature" in c.type_line or "Planeswalker" in c.type_line)
                )
                or "can be your commander" in c.oracle_text.lower()
            ]
        elif self._mode == MODE_PARTNER and self._post_filter is not None:
            results = [c for c in results if self._post_filter(c)]

        self._results = results[:300]
        self._rebuild_list(restore_index=False)

    def _implied_node(self) -> Optional[Atom]:
        if self._mode == MODE_GROUP:
            ci: set[str] = set()
            if self._deck.commander:
                ci.update(self._deck.commander.card.color_identity)
            if self._deck.partner:
                ci.update(self._deck.partner.card.color_identity)
            if ci:
                return Atom(key="id", value="".join(sorted(ci)))
        return None

    def _rebuild_list(self, restore_index: bool = True) -> None:
        lv = self.query_one("#srch-list", ListView)
        old_idx = lv.index if restore_index else None
        lv.clear()
        currency = self._settings.currency
        for card in self._results:
            maybe = self._is_maybe(card)
            count = 0 if maybe else self._card_count(card)
            base = card.display_label(currency, self._deck.get_printing_idx(card, currency))
            if maybe:
                item = ListItem(Label(Text(" [M] ") + base), classes="result-selected")
            elif count:
                pfx = Text(f"[{count}] " if count > 1 else " [+] ")
                item = ListItem(Label(pfx + base), classes="result-selected")
            else:
                item = ListItem(Label(Text("     ") + base))
            lv.append(item)
        if old_idx is not None and 0 <= old_idx < len(self._results):
            lv.index = old_idx

    def _refresh_results_for(self, *oracle_ids: str) -> None:
        """Update only the list items whose oracle_id is in oracle_ids."""
        id_set = set(oracle_ids)
        changed = {
            i: card
            for i, card in enumerate(self._results)
            if card.oracle_id in id_set
        }
        if not changed:
            return
        lv = self.query_one("#srch-list", ListView)
        items = list(lv.children)
        currency = self._settings.currency
        for i, card in changed.items():
            if i >= len(items):
                continue
            maybe = self._is_maybe(card)
            count = 0 if maybe else self._card_count(card)
            base = card.display_label(currency, self._deck.get_printing_idx(card, currency))
            item = items[i]
            label = item.query_one(Label)
            if maybe:
                label.update(Text(" [M] ") + base)
                item.add_class("result-selected")
            elif count:
                pfx = Text(f"[{count}] " if count > 1 else " [+] ")
                label.update(pfx + base)
                item.add_class("result-selected")
            else:
                label.update(Text("     ") + base)
                item.remove_class("result-selected")

    def _card_count(self, card: Card) -> int:
        if self._mode == MODE_COMMANDER:
            return 1 if self._deck.commander and self._deck.commander.card.oracle_id == card.oracle_id else 0
        if self._mode == MODE_PARTNER:
            return 1 if self._deck.partner and self._deck.partner.card.oracle_id == card.oracle_id else 0
        if self._mode == MODE_GROUP:
            return self._deck.count_of(card.oracle_id)
        return 0

    def _is_maybe(self, card: Card) -> bool:
        entry = self._deck.get_entry(card.oracle_id)
        return entry is not None and entry.is_maybe()

    # tag → group name (case-insensitive); extend here to add more routes
    _TAG_ROUTES: list[tuple[str, str]] = [
        ("ramp", "ramp"),
        ("draw", "draw"),
        ("removal", "interaction"),
    ]

    def _route_groups(self, card: Card) -> list[Group]:
        """Return groups the card should auto-route into based on type/tags."""
        name_map = {g.name.lower(): g for g in self._deck.groups}
        tags = {t.lower() for t in self._db.get_tags(card.oracle_id)}
        targets: list[Group] = []

        if any("land" in face.lower() for face in card.type_line.split(" // ")):
            if "lands" in name_map:
                targets.append(name_map["lands"])

        for tag, group in self._TAG_ROUTES:
            if tag in tags and group in name_map:
                targets.append(name_map[group])

        return targets

    # ── actions ────────────────────────────────────────────────────────────────

    def action_dismiss_screen(self) -> None:
        self.dismiss(self.query_one("#srch-input", QueryInput).value)

    def action_toggle_card(self) -> None:
        if isinstance(self.focused, Input) or self._current_card is None:
            return
        card = self._current_card
        changed: set[str] = {card.oracle_id}
        if self._mode == MODE_COMMANDER:
            if self._deck.commander:
                changed.add(self._deck.commander.card.oracle_id)
            if self._deck.commander and self._deck.commander.card.oracle_id == card.oracle_id:
                new_cmd: CardEntry | None = None
            else:
                new_cmd = self._deck.make_entry(card, CardRole.COMMANDER)
            self._deck.commander = new_cmd
            if self._deck.partner is not None:
                info = partner_mode(new_cmd.card) if new_cmd else None
                if info is None or not partner_filter(info)(self._deck.partner.card):
                    self._deck.partner = None
        elif self._mode == MODE_PARTNER:
            if self._deck.partner:
                changed.add(self._deck.partner.card.oracle_id)
            if self._deck.partner and self._deck.partner.card.oracle_id == card.oracle_id:
                self._deck.partner = None
            else:
                self._deck.partner = self._deck.make_entry(card, CardRole.PARTNER)
        elif self._mode == MODE_GROUP:
            if self._deck.count_of(card.oracle_id) > 0:
                self._deck.remove_all(card.oracle_id)
            else:
                self._deck.add(card)
                entry = self._deck.get_entry(card.oracle_id)
                if entry is not None:
                    targets = self._route_groups(card)
                    if not targets and self._group is not None:
                        targets = [self._group]
                    for g in targets:
                        entry.join_group(g.name)
        self._refresh_results_for(*changed)

    def action_increment_card(self) -> None:
        if isinstance(self.focused, Input) or self._current_card is None:
            return
        card = self._current_card
        if self._mode != MODE_GROUP or not card.allows_multiple():
            return
        if self._deck.count_of(card.oracle_id) > 0:
            self._deck.add(card)
        else:
            self._deck.add(card)
            entry = self._deck.get_entry(card.oracle_id)
            if entry is not None:
                targets = self._route_groups(card)
                if not targets and self._group is not None:
                    targets = [self._group]
                for g in targets:
                    entry.join_group(g.name)
        self._refresh_results_for(card.oracle_id)

    def action_decrement_card(self) -> None:
        if isinstance(self.focused, Input) or self._current_card is None:
            return
        card = self._current_card
        if self._mode == MODE_GROUP:
            if self._deck.count_of(card.oracle_id) > 0:
                self._deck.remove_one(card.oracle_id)
                self._refresh_results_for(card.oracle_id)

    def action_toggle_maybeboard(self) -> None:
        if isinstance(self.focused, Input) or self._current_card is None:
            return
        if self._mode != MODE_GROUP:
            return
        card = self._current_card
        entry = self._deck.get_entry(card.oracle_id)
        if entry is None:
            # Card not in deck yet — add it directly to maybeboard
            self._deck.add(card)
            entry = self._deck.get_entry(card.oracle_id)
        if entry is not None:
            if entry.is_maybe():
                entry.leave_group(MAYBEBOARD)
            else:
                entry.join_group(MAYBEBOARD)
        self._refresh_results_for(card.oracle_id)
