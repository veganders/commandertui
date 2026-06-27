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
from widgets import CardDetail, CardGroupEditorScreen, GroupNameModal, TopBar


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

    Input {
        border: none;
        height: 1;
        padding: 0 1;
        background: $surface;
    }
    Input:focus {
        border: none;
        background: $panel;
    }
    SelectCurrent {
        border: none;
        height: 1;
        padding: 0 1;
        background: $surface;
    }
    SelectCurrent:focus {
        border: none;
        background: $panel;
    }
    """

    BINDINGS = [
        Binding("q", "quit", "Quit"),
        Binding("s", "search_cards", "Search"),
        Binding("c", "search_commander", "Commander"),
        Binding("p", "search_partner", "Partner"),
        Binding("g", "create_group", "New group"),
        Binding("d", "delete_node", "Delete"),
        Binding("e", "edit_card_groups", "Edit groups"),
        Binding("+", "increment_card", "+1"),
        Binding("-", "decrement_card", "-1"),
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
        currency = self._settings.currency
        for group in self._deck.groups:
            entries = self._deck.entries_for_group(group.name)
            total = sum(e.count for e in entries)
            node = tree.root.add(f"{group.name}  ({total})", expand=True, data=group)
            for entry in entries:
                base = entry.card.display_label(currency, self._deck.get_printing_idx(entry.card, currency))
                label = f"[{entry.count}] {base}" if entry.count > 1 else base
                node.add_leaf(label, data=entry.card)
        uncategorized = self._deck.uncategorized_entries()
        if uncategorized:
            total = sum(e.count for e in uncategorized)
            node = tree.root.add(f"Uncategorized  ({total})", expand=True, data=None)
            for entry in uncategorized:
                base = entry.card.display_label(currency, self._deck.get_printing_idx(entry.card, currency))
                label = f"[{entry.count}] {base}" if entry.count > 1 else base
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
        self._push_search(MODE_GROUP, group=self._group_for_cursor())

    def action_search_commander(self) -> None:
        self._push_search(MODE_COMMANDER)

    def action_create_group(self) -> None:
        def on_name(name: Optional[str]) -> None:
            if name:
                self._deck.groups.append(Group(name=name))
                self._rebuild_tree()
        self.push_screen(GroupNameModal(), callback=on_name)

    def action_edit_card_groups(self) -> None:
        node = self.query_one("#groups", Tree).cursor_node
        if node is None or not isinstance(node.data, Card):
            return

        def on_done(_) -> None:
            self._rebuild_tree()
            self.query_one(TopBar).refresh_display()

        self.push_screen(CardGroupEditorScreen(node.data, self._deck), callback=on_done)

    def action_delete_node(self) -> None:
        node = self.query_one("#groups", Tree).cursor_node
        if node is None:
            return
        if isinstance(node.data, Card):
            self._deck.remove_all(node.data.oracle_id)
        elif isinstance(node.data, Group):
            group = node.data
            for entry in self._deck.entries_for_group(group.name):
                entry.leave_group(group.name)
            if not group.permanent:
                self._deck.groups.remove(group)
        else:
            return
        self._rebuild_tree()
        self.query_one(TopBar).refresh_display()

    def action_increment_card(self) -> None:
        node = self.query_one("#groups", Tree).cursor_node
        if node is None or not isinstance(node.data, Card):
            return
        if not node.data.allows_multiple():
            return
        self._deck.add(node.data)
        self._rebuild_tree()
        self.query_one(TopBar).refresh_display()

    def action_decrement_card(self) -> None:
        node = self.query_one("#groups", Tree).cursor_node
        if node is None or not isinstance(node.data, Card):
            return
        self._deck.remove_one(node.data.oracle_id)
        self._rebuild_tree()
        self.query_one(TopBar).refresh_display()

    def check_action(self, action: str, parameters: tuple) -> bool:
        if action == "search_partner":
            return (
                self._deck.commander is not None
                and partner_mode(self._deck.commander) is not None
            )
        if action == "edit_card_groups":
            node = self.query_one("#groups", Tree).cursor_node
            return node is not None and isinstance(node.data, Card)
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

    deck = Deck(
        groups=[
            Group("Ramp", permanent=True),
            Group("Draw", permanent=True),
            Group("Interaction", permanent=True),
            Group("Lands", permanent=True),
        ],
    )

    DeckbuilderApp(db, deck, settings).run()
