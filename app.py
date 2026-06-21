"""Deckbuilder TUI — main application."""

from __future__ import annotations

from typing import Callable, Optional

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal
from textual.widgets import Footer, Tree

from db import Card, CardDB, load_db
from models import Deck, Group
from partner import partner_mode, partner_filter
from search import MODE_COMMANDER, MODE_GROUP, MODE_PARTNER, SearchScreen
from settings import Settings
from widgets import CardDetail, TopBar


class DeckbuilderApp(App):
    CSS = """
    TopBar {
        height: 5;
        padding: 0 2;
        background: $surface;
        border-bottom: solid $primary;
    }
    #tb-info { width: 1fr; height: 100%; }
    #tb-right { width: 36; height: 100%; }
    #bottom { height: 1fr; }
    #groups { width: 1fr; border-right: solid $primary; }
    CardDetail { width: 1fr; padding: 1 2; }
    #cd-printing-label { margin-top: 1; color: $text-muted; }
    """

    BINDINGS = [
        Binding("q", "quit", "Quit"),
        Binding("s", "search_cards", "Search"),
        Binding("c", "search_commander", "Commander"),
        Binding("p", "search_partner", "Partner"),
    ]

    def __init__(self, db: CardDB, deck: Deck, settings: Settings) -> None:
        super().__init__()
        self._db = db
        self._deck = deck
        self._settings = settings
        self._current_card: Optional[Card] = None

    def compose(self) -> ComposeResult:
        yield TopBar(self._deck, self._settings)
        with Horizontal(id="bottom"):
            yield Tree("Groups", id="groups")
            yield CardDetail()
        yield Footer()

    def on_mount(self) -> None:
        self._rebuild_tree()

    # ── tree ───────────────────────────────────────────────────────────────────

    def _rebuild_tree(self) -> None:
        tree = self.query_one("#groups", Tree)
        tree.clear()
        for group in self._deck.groups:
            node = tree.root.add(f"{group.name}  ({group.total_count()})", expand=True, data=group)
            for entry in group.cards:
                label = f"[{entry.count}] {entry.card.name}" if entry.count > 1 else entry.card.name
                node.add_leaf(label, data=entry.card)
        tree.root.expand()

    def _group_for_cursor(self) -> Optional[Group]:
        node = self.query_one("#groups", Tree).cursor_node
        if node is None:
            return None
        if isinstance(node.data, Group):
            return node.data
        if isinstance(node.data, Card) and node.parent is not None:
            d = node.parent.data
            return d if isinstance(d, Group) else None
        return None

    # ── event handlers ─────────────────────────────────────────────────────────

    def on_tree_node_highlighted(self, event: Tree.NodeHighlighted) -> None:
        card = event.node.data if isinstance(event.node.data, Card) else None
        self._current_card = card
        self.query_one(CardDetail).show_card(card, self._db, self._deck, self._settings)

    def on_top_bar_currency_changed(self, msg: TopBar.CurrencyChanged) -> None:
        self._settings.currency = msg.currency
        self._settings.save()
        self.query_one(TopBar).refresh_display()
        if self._current_card:
            self.query_one(CardDetail).show_card(
                self._current_card, self._db, self._deck, self._settings
            )

    def on_card_detail_printing_selected(self, msg: CardDetail.PrintingSelected) -> None:
        self._deck.selected_printings[msg.oracle_id] = msg.printing_idx
        self.query_one(TopBar).refresh_display()

    # ── search ─────────────────────────────────────────────────────────────────

    def _push_search(
        self,
        mode: str,
        group: Optional[Group] = None,
        post_filter: Optional[Callable[[Card], bool]] = None,
        title: Optional[str] = None,
    ) -> None:
        def on_done(_) -> None:
            self._rebuild_tree()
            self.query_one(TopBar).refresh_display()

        self.push_screen(
            SearchScreen(
                self._db, self._deck, self._settings, mode,
                group=group, post_filter=post_filter, title=title,
            ),
            callback=on_done,
        )

    def action_search_cards(self) -> None:
        group = self._group_for_cursor()
        if group is None and self._deck.groups:
            group = self._deck.groups[0]
        if group is None:
            return
        self._push_search(MODE_GROUP, group=group)

    def action_search_commander(self) -> None:
        self._push_search(MODE_COMMANDER)

    def check_action(self, action: str, parameters: tuple) -> bool:
        if action == "search_partner":
            return (
                self._deck.commander is not None
                and partner_mode(self._deck.commander) is not None
            )
        return True

    def action_search_partner(self) -> None:
        commander = self._deck.commander
        if commander is None:
            return
        info = partner_mode(commander)
        if info is None:
            return

        if self._deck.partner is not None:
            self._deck.partner = None
            self.query_one(TopBar).refresh_display()
            return

        if info["type"] == "partner_with":
            name = info.get("name") or ""
            results = self._db.search(name=name)
            card = next((c for c in results if c.name == name), None)
            if card:
                self._deck.partner = card
                self.query_one(TopBar).refresh_display()
            else:
                self.notify(f"Partner not found in database: {name}", severity="warning")
            return

        titles = {
            "partner": "Search for a partner (generic Partner)",
            "partner_variant": f"Search for a Partner—{info.get('mechanic', '')}",
            "doctors_companion": (
                "Search for a Doctor's companion"
                if info.get("role") == "doctor"
                else "Search for a Doctor (Time Lord)"
            ),
            "background": "Search for a background",
        }
        self._push_search(
            MODE_PARTNER,
            post_filter=partner_filter(info),
            title=titles.get(info["type"]),
        )


if __name__ == "__main__":
    db = load_db()
    settings = Settings.load()

    ramp = Group("Ramp")
    draw = Group("Draw")
    removal = Group("Removal")

    for name, group in [
        ("Sol Ring", ramp), ("Arcane Signet", ramp), ("Cultivate", ramp),
        ("Rhystic Study", draw), ("Mystic Remora", draw), ("Brainstorm", draw),
        ("Swords to Plowshares", removal), ("Counterspell", removal),
    ]:
        results = db.search(name=name)
        if results:
            group.add(results[0])

    commanders = db.search(name="Atraxa, Praetors' Voice")
    deck = Deck(
        commander=commanders[0] if commanders else None,
        groups=[ramp, draw, removal],
    )

    DeckbuilderApp(db, deck, settings).run()
