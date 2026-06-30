"""Deckbuilder TUI — main application."""

from __future__ import annotations

from typing import Callable, Optional

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal
from textual.widgets import Footer, Tree

from rich.text import Text
from db import Card, CardDB, load_db
from deck_io import list_decks, load_deck, save_deck
from histogram import TagHistogramScreen
from models import CardEntry, CardRole, Deck, Group
from partner import partner_mode, partner_filter
from search import MODE_COMMANDER, MODE_GROUP, MODE_PARTNER, SearchScreen
from settings import Settings
from sorting import CardSorter, MVSorter, NameSorter, PriceSorter
from widgets import CardDetail, CardGroupEditorScreen, DeckNameModal, GroupNameModal, OpenDeckScreen, TopBar


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
        Binding("h", "show_histogram", "Tag histogram"),
        Binding("o", "cycle_sort", "Sort"),
        Binding("ctrl+n", "new_deck", "New"),
        Binding("ctrl+s", "save_deck", "Save"),
        Binding("ctrl+o", "open_deck", "Open"),
        Binding("+", "increment_card", "+1"),
        Binding("-", "decrement_card", "-1"),
    ]

    def __init__(self, db: CardDB, deck: Deck, settings: Settings) -> None:
        super().__init__()
        self._db = db
        self._deck = deck
        self._settings = settings
        self._current_card: Optional[Card] = None
        self._sort_idx: int = 0

    def _sorters(self) -> list[CardSorter]:
        return [NameSorter(), MVSorter(), PriceSorter(self._settings.currency)]

    def _current_sorter(self) -> CardSorter:
        sorters = self._sorters()
        return sorters[self._sort_idx % len(sorters)]

    def compose(self) -> ComposeResult:
        yield TopBar(self._deck, self._settings)
        with Horizontal(id="bottom"):
            yield Tree("Groups", id="groups")
            yield CardDetail()
        yield Footer()

    def on_mount(self) -> None:
        self._rebuild_tree()
        self.query_one("#groups", Tree).focus()

    # ── tree ───────────────────────────────────────────────────────────────────

    def _rebuild_tree(self) -> None:
        tree = self.query_one("#groups", Tree)
        tree.clear()
        currency = self._settings.currency
        sorter = self._current_sorter()

        if self._deck.commander or self._deck.partner:
            section_label = "Commander / Partner" if self._deck.partner else "Commander"
            cmd_node = tree.root.add(section_label, expand=True, data=None)
            for entry in (self._deck.commander, self._deck.partner):
                if entry:
                    base = entry.card.display_label(currency, entry.printing_idx)
                    cmd_node.add_leaf(base, data=entry.card)

        for group in self._deck.groups:
            entries = sorted(self._deck.entries_for_group(group.name), key=sorter.key)
            total = sum(e.count for e in entries)
            node = tree.root.add(f"{group.name}  ({total})", expand=True, data=group)
            for entry in entries:
                base = entry.card.display_label(currency, entry.printing_idx)
                label = Text(f"[{entry.count}] ") + base if entry.count > 1 else base
                node.add_leaf(label, data=entry.card)

        uncategorized = sorted(self._deck.uncategorized_entries(), key=sorter.key)
        if uncategorized:
            total = sum(e.count for e in uncategorized)
            node = tree.root.add(f"Uncategorized  ({total})", expand=True, data=None)
            for entry in uncategorized:
                base = entry.card.display_label(currency, entry.printing_idx)
                label = Text(f"[{entry.count}] ") + base if entry.count > 1 else base
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
        self._rebuild_tree()
        self.query_one(TopBar).refresh_display()
        if self._current_card:
            self.query_one(CardDetail).show_card(
                self._current_card, self._db, self._deck, self._settings
            )

    def on_card_detail_printing_selected(self, msg: CardDetail.PrintingSelected) -> None:
        entry = self._deck.get_entry_for_card(msg.oracle_id)
        if entry is not None:
            entry.printing_idx = msg.printing_idx
        else:
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

    def action_show_histogram(self) -> None:
        self.push_screen(TagHistogramScreen(self._db, self._deck))

    def action_cycle_sort(self) -> None:
        self._sort_idx = (self._sort_idx + 1) % len(self._sorters())
        self._rebuild_tree()
        self.notify(f"Sort: {self._current_sorter().label}")

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
                and partner_mode(self._deck.commander.card) is not None
            )
        if action == "edit_card_groups":
            node = self.query_one("#groups", Tree).cursor_node
            return node is not None and isinstance(node.data, Card)
        return True

    def action_new_deck(self) -> None:
        self._deck.__dict__.update(_fresh_deck().__dict__)
        self._current_card = None
        self._rebuild_tree()
        self.query_one(TopBar).refresh_display()
        self.query_one(CardDetail).show_card(None, self._db, self._deck, self._settings)

    def action_save_deck(self) -> None:
        if self._deck.name:
            path = save_deck(self._deck)
            self._deck.save_path = path
            self.notify(f"Saved: {path.name}")
        else:
            def on_name(name: str | None) -> None:
                if not name:
                    return
                self._deck.name = name
                path = save_deck(self._deck)
                self._deck.save_path = path
                self.query_one(TopBar).refresh_display()
                self.notify(f"Saved: {path.name}")
            self.push_screen(DeckNameModal(), callback=on_name)

    def action_open_deck(self) -> None:
        paths = list_decks()
        if not paths:
            self.notify("No saved decks found.", severity="warning")
            return

        def on_path(path) -> None:
            if path is None:
                return
            self._deck.__dict__.update(load_deck(path, self._db).__dict__)
            self._rebuild_tree()
            self.query_one(TopBar).refresh_display()
            self.notify(f"Opened: {self._deck.name or path.stem}")

        self.push_screen(OpenDeckScreen(paths), callback=on_path)

    def action_search_partner(self) -> None:
        commander = self._deck.commander
        if commander is None:
            return
        info = partner_mode(commander.card)
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
                self._deck.partner = CardEntry(card=card, role=CardRole.PARTNER)
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


def _fresh_deck() -> Deck:
    return Deck(
        groups=[
            Group("Ramp", permanent=True),
            Group("Draw", permanent=True),
            Group("Interaction", permanent=True),
            Group("Lands", permanent=True),
        ],
    )


if __name__ == "__main__":
    db = load_db()
    settings = Settings.load()
    DeckbuilderApp(db, _fresh_deck(), settings).run()
