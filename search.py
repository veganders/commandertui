"""Card search screen."""

from __future__ import annotations

from typing import Callable, Optional

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal
from textual.screen import Screen
from textual.widgets import Input, Label, ListItem, ListView

from db import And, Atom, Card, CardDB, parse_query
from models import Deck, Group
from partner import partner_mode, partner_filter
from settings import Settings
from widgets import CardDetail

MODE_COMMANDER = "commander"
MODE_PARTNER = "partner"
MODE_GROUP = "group"


def _allows_multiple(card: Card) -> bool:
    return (
        "Basic" in card.type_line
        or "any number of cards named" in card.oracle_text.lower()
    )


class SearchScreen(Screen[None]):
    BINDINGS = [
        Binding("escape", "dismiss_screen", "Close"),
        Binding("space", "toggle_card", "Add/Remove"),
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
    ) -> None:
        super().__init__()
        self._db = db
        self._deck = deck
        self._settings = settings
        self._mode = mode
        self._group = group
        self._post_filter = post_filter
        self._title = title
        self._results: list[Card] = []
        self._current_card: Optional[Card] = None
        self._search_timer = None

    def compose(self) -> ComposeResult:
        with Horizontal(id="srch-bar"):
            yield Input(placeholder=self._placeholder(), id="srch-input")
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
        return base + "  —  t:type  o:oracle  ci:wubrg  tag:ramp  cmc>=3"

    def on_mount(self) -> None:
        self._run_search("")
        self.query_one("#srch-input", Input).focus()

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id != "srch-input":
            return
        if self._search_timer is not None:
            self._search_timer.stop()
        value = event.value
        self._search_timer = self.set_timer(1.0, lambda: self._run_search(value))

    def on_key(self, event) -> None:
        if event.key == "down" and isinstance(self.focused, Input):
            self.query_one("#srch-list", ListView).focus()
            event.stop()

    def on_list_view_highlighted(self, event: ListView.Highlighted) -> None:
        if event.list_view.id != "srch-list":
            return
        idx = event.list_view.index
        card = self._results[idx] if idx is not None and 0 <= idx < len(self._results) else None
        self._current_card = card
        self.query_one(CardDetail).show_card(card, self._db, self._deck, self._settings)

    def on_card_detail_printing_selected(self, msg: CardDetail.PrintingSelected) -> None:
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
                ci.update(self._deck.commander.color_identity)
            if self._deck.partner:
                ci.update(self._deck.partner.color_identity)
            if ci:
                return Atom(key="ci", value="".join(sorted(ci)))
        return None

    def _rebuild_list(self, restore_index: bool = True) -> None:
        lv = self.query_one("#srch-list", ListView)
        old_idx = lv.index if restore_index else None
        lv.clear()
        for card in self._results:
            count = self._card_count(card)
            if count:
                pfx = f"[{count}]" if count > 1 else " [+]"
                item = ListItem(Label(f"{pfx} {card.name}"), classes="result-selected")
            else:
                item = ListItem(Label(f"     {card.name}"))
            lv.append(item)
        if old_idx is not None and 0 <= old_idx < len(self._results):
            lv.index = old_idx

    def _refresh_results_for(self, *oracle_ids: str) -> None:
        """Update only the list items whose oracle_id is in oracle_ids.

        Much cheaper than _rebuild_list when only one or two items change —
        avoids tearing down and recreating all 300 widgets.
        """
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
        for i, card in changed.items():
            if i >= len(items):
                continue
            count = self._card_count(card)
            item = items[i]
            label = item.query_one(Label)
            if count:
                pfx = f"[{count}]" if count > 1 else " [+]"
                label.update(f"{pfx} {card.name}")
                item.add_class("result-selected")
            else:
                label.update(f"     {card.name}")
                item.remove_class("result-selected")

    def _card_count(self, card: Card) -> int:
        if self._mode == MODE_COMMANDER:
            return 1 if self._deck.commander and self._deck.commander.oracle_id == card.oracle_id else 0
        if self._mode == MODE_PARTNER:
            return 1 if self._deck.partner and self._deck.partner.oracle_id == card.oracle_id else 0
        if self._mode == MODE_GROUP and self._group is not None:
            return self._group.count_of(card.oracle_id)
        return 0

    # ── actions ────────────────────────────────────────────────────────────────

    def action_dismiss_screen(self) -> None:
        self.dismiss(None)

    def action_toggle_card(self) -> None:
        if isinstance(self.focused, Input) or self._current_card is None:
            return
        card = self._current_card
        # Track oracle_ids whose display may change (current + any previously selected)
        changed: set[str] = {card.oracle_id}
        if self._mode == MODE_COMMANDER:
            if self._deck.commander:
                changed.add(self._deck.commander.oracle_id)
            new_cmd = (
                None if self._deck.commander and self._deck.commander.oracle_id == card.oracle_id
                else card
            )
            self._deck.commander = new_cmd
            if self._deck.partner is not None:
                info = partner_mode(new_cmd) if new_cmd else None
                if info is None or not partner_filter(info)(self._deck.partner):
                    self._deck.partner = None
        elif self._mode == MODE_PARTNER:
            if self._deck.partner:
                changed.add(self._deck.partner.oracle_id)
            self._deck.partner = (
                None if self._deck.partner and self._deck.partner.oracle_id == card.oracle_id
                else card
            )
        elif self._mode == MODE_GROUP and self._group is not None:
            if self._group.count_of(card.oracle_id) > 0:
                self._group.remove_all(card.oracle_id)
            else:
                self._group.add(card)
        self._refresh_results_for(*changed)

    def action_increment_card(self) -> None:
        if isinstance(self.focused, Input) or self._current_card is None:
            return
        card = self._current_card
        if self._mode == MODE_GROUP and self._group is not None and _allows_multiple(card):
            self._group.add(card)
            self._refresh_results_for(card.oracle_id)

    def action_decrement_card(self) -> None:
        if isinstance(self.focused, Input) or self._current_card is None:
            return
        card = self._current_card
        if self._mode == MODE_GROUP and self._group is not None:
            if self._group.count_of(card.oracle_id) > 0:
                self._group.remove_one(card.oracle_id)
                self._refresh_results_for(card.oracle_id)
