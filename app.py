"""Deckbuilder TUI."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Callable, Optional

from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.message import Message
from textual.screen import Screen
from textual.widget import Widget
from textual.widgets import Footer, Input, Label, ListItem, ListView, Select, Static, Tree

from db import And, Atom, Card, CardDB, QueryNode, load_db, parse_query
from settings import Settings

# ── Constants ─────────────────────────────────────────────────────────────────

_CURRENCIES: list[tuple[str, str]] = [
    ("USD ($)", "usd"),
    ("EUR (€)", "eur"),
    ("MTGO (tix)", "tix"),
]
_CURRENCY_SYMBOLS = {"usd": "$", "eur": "€", "tix": ""}
_BARS = " ▁▂▃▄▅▆▇█"
_CURVE_LABELS = ["0", "1", "2", "3", "4", "5", "6+"]


def _bar(value: int, max_val: int) -> str:
    if max_val == 0:
        return _BARS[0]
    return _BARS[round(value / max_val * (len(_BARS) - 1))]


def _fmt_price(price: Optional[float], currency: str) -> str:
    if price is None:
        return "N/A"
    sym = _CURRENCY_SYMBOLS.get(currency, "")
    return f"{sym}{price:.2f}"


# ── Partner logic ─────────────────────────────────────────────────────────────

def partner_mode(card: Card) -> Optional[dict]:
    """Return a dict describing what second commander this card supports, or None.

    Detection priority per CLAUDE.md:
      partner_with      → "Partner with" keyword (one specific named card)
      doctors_companion → "Doctor's companion" keyword or "Time Lord Doctor" type
      background        → "Choose a background" keyword
      partner_variant   → "Partner—X" in oracle text (Friends forever, Character select, …)
      partner           → generic "Partner" keyword
    """
    kws = card.keywords
    oracle = card.oracle_text

    if "Partner with" in kws:
        m = re.search(r"Partner with ([^(\n]+)", oracle)
        return {"type": "partner_with", "name": m.group(1).strip() if m else None}

    if "Doctor's companion" in kws:
        return {"type": "doctors_companion", "role": "companion"}

    if "Time Lord Doctor" in card.type_line:
        return {"type": "doctors_companion", "role": "doctor"}

    if "Choose a background" in kws:
        return {"type": "background"}

    m = re.search(r"Partner—([^(]+?)\s*\(", oracle)
    if m:
        return {"type": "partner_variant", "mechanic": m.group(1).strip()}

    if "Partner" in kws:
        return {"type": "partner"}

    return None


def _partner_filter(info: dict) -> Callable[[Card], bool]:
    """Return a Card predicate matching valid partners for the given partner_mode dict."""
    t = info["type"]
    if t == "partner":
        return lambda c: (
            "Partner" in c.keywords
            and "Partner with" not in c.keywords
            and not re.search(r"Partner—", c.oracle_text)
        )
    if t == "partner_with":
        name = info.get("name") or ""
        return lambda c, _n=name: c.name == _n
    if t == "partner_variant":
        tag = "Partner—" + info["mechanic"]
        return lambda c, _t=tag: _t in c.oracle_text
    if t == "doctors_companion":
        if info.get("role") == "doctor":
            return lambda c: "Doctor's companion" in c.keywords
        return lambda c: "Time Lord Doctor" in c.type_line
    if t == "background":
        return lambda c: "Background" in c.type_line
    return lambda c: True


# ── Data model ─────────────────────────────────────────────────────────────────

@dataclass
class Group:
    name: str
    cards: list[Card] = field(default_factory=list)


@dataclass
class Deck:
    commander: Optional[Card] = None
    partner: Optional[Card] = None
    groups: list[Group] = field(default_factory=list)
    selected_printings: dict[str, int] = field(default_factory=dict)

    def unique_cards(self) -> list[Card]:
        """Unique cards across all groups, excluding commander/partner."""
        seen: set[str] = set()
        result = []
        for g in self.groups:
            for c in g.cards:
                if c.oracle_id not in seen:
                    result.append(c)
                    seen.add(c.oracle_id)
        return result

    def all_cards(self) -> list[Card]:
        """Commander + partner + unique group cards, deduped."""
        seen: set[str] = set()
        result = []
        for card in (self.commander, self.partner):
            if card and card.oracle_id not in seen:
                result.append(card)
                seen.add(card.oracle_id)
        for c in self.unique_cards():
            if c.oracle_id not in seen:
                result.append(c)
                seen.add(c.oracle_id)
        return result

    def card_count(self) -> int:
        return len(self.all_cards())

    def mana_curve(self) -> list[int]:
        buckets = [0] * 7
        for c in self.all_cards():
            buckets[min(int(c.cmc), 6)] += 1
        return buckets

    def get_printing_idx(self, card: Card, currency: str) -> int:
        if card.oracle_id in self.selected_printings:
            return self.selected_printings[card.oracle_id]
        best_idx, best_price = 0, float("inf")
        for i, p in enumerate(card.printings):
            price = p.prices.get(currency, float("inf"))
            if price < best_price:
                best_price, best_idx = price, i
        return best_idx

    def total_cost(self, currency: str) -> tuple[float, int, int]:
        total = 0.0
        priced = 0
        cards = self.all_cards()
        for card in cards:
            if not card.printings:
                continue
            idx = self.get_printing_idx(card, currency)
            price = card.printings[idx].prices.get(currency)
            if price is not None:
                total += price
                priced += 1
        return total, priced, len(cards)


# ── Widgets ────────────────────────────────────────────────────────────────────

class TopBar(Widget):
    class CurrencyChanged(Message):
        def __init__(self, currency: str) -> None:
            super().__init__()
            self.currency = currency

    def __init__(self, deck: Deck, settings: Settings) -> None:
        super().__init__()
        self._deck = deck
        self._settings = settings

    def compose(self) -> ComposeResult:
        with Horizontal():
            with Vertical(id="tb-info"):
                yield Static(id="tb-commander")
                yield Static(id="tb-stats")
            with Vertical(id="tb-right"):
                yield Select(
                    _CURRENCIES,
                    value=self._settings.currency,
                    allow_blank=False,
                    id="currency-select",
                )
                yield Static(id="tb-total")

    def on_mount(self) -> None:
        self.refresh_display()

    def refresh_display(self) -> None:
        deck = self._deck
        settings = self._settings

        t = Text()
        t.append("Commander: ", style="bold")
        t.append(deck.commander.name if deck.commander else "─ no commander ─")
        if deck.partner:
            t.append("   Partner: ", style="bold")
            t.append(deck.partner.name)
        self.query_one("#tb-commander", Static).update(t)

        curve = deck.mana_curve()
        max_val = max(curve) if any(curve) else 1
        t = Text()
        t.append(f"Cards: {deck.card_count()}/100", style="bold")
        t.append("   Curve: ", style="bold")
        for i, (label, val) in enumerate(zip(_CURVE_LABELS, curve)):
            t.append(label, style="dim")
            t.append(_bar(val, max_val))
            t.append(str(val))
            if i < len(_CURVE_LABELS) - 1:
                t.append("  ")
        self.query_one("#tb-stats", Static).update(t)

        total, priced, count = deck.total_cost(settings.currency)
        t = Text()
        t.append("Total: ", style="bold")
        t.append(_fmt_price(total if priced else None, settings.currency))
        if 0 < priced < count:
            t.append(f" ({priced}/{count} cards)", style="dim")
        self.query_one("#tb-total", Static).update(t)

    def on_select_changed(self, event: Select.Changed) -> None:
        if event.select.id == "currency-select" and event.value is not Select.BLANK:
            self.post_message(self.CurrencyChanged(str(event.value)))


class CardDetail(VerticalScroll):
    class PrintingSelected(Message):
        def __init__(self, oracle_id: str, printing_idx: int) -> None:
            super().__init__()
            self.oracle_id = oracle_id
            self.printing_idx = printing_idx

    def __init__(self) -> None:
        super().__init__()
        self._current_oracle_id: Optional[str] = None
        self._updating = False

    def compose(self) -> ComposeResult:
        yield Static(id="cd-text")
        yield Static("Printing", id="cd-printing-label")
        yield Select([], allow_blank=True, id="cd-printing-select")

    def on_mount(self) -> None:
        self.query_one("#cd-printing-label").display = False
        self.query_one("#cd-printing-select").display = False

    def show_card(
        self,
        card: Optional[Card],
        db: CardDB,
        deck: Deck,
        settings: Settings,
    ) -> None:
        self._current_oracle_id = card.oracle_id if card else None
        text_w = self.query_one("#cd-text", Static)
        label_w = self.query_one("#cd-printing-label", Static)
        select_w = self.query_one("#cd-printing-select", Select)

        if card is None:
            text_w.update("")
            label_w.display = False
            select_w.display = False
            return

        text_w.update(self._format(card, db))

        if card.printings:
            currency = settings.currency
            options = [
                (
                    f"{p.set_name} #{p.collector_number} {p.finish}"
                    f" — {_fmt_price(p.prices.get(currency), currency)}",
                    i,
                )
                for i, p in enumerate(card.printings)
            ]
            self._updating = True
            select_w.set_options(options)
            select_w.value = deck.get_printing_idx(card, currency)
            self._updating = False
            label_w.display = True
            select_w.display = True
        else:
            label_w.display = False
            select_w.display = False

    def _format(self, card: Card, db: CardDB) -> Text:
        t = Text()
        t.append(card.name + "\n", style="bold")
        if card.mana_cost:
            t.append(card.mana_cost + "\n", style="yellow")
        t.append(card.type_line + "\n", style="italic")
        if card.power is not None:
            t.append(f"{card.power}/{card.toughness}\n")
        if card.loyalty is not None:
            t.append(f"Loyalty: {card.loyalty}\n")
        t.append("\n")
        if card.oracle_text:
            t.append(card.oracle_text + "\n")
        tags = db.get_tags(card.oracle_id)
        if tags:
            t.append("\nTags\n", style="bold")
            t.append(", ".join(tags) + "\n", style="dim")
        rulings = db.get_rulings(card.oracle_id)
        if rulings:
            t.append("\nRulings\n", style="bold")
            for r in rulings:
                t.append(f"• {r}\n")
        return t

    def on_select_changed(self, event: Select.Changed) -> None:
        if (
            not self._updating
            and event.select.id == "cd-printing-select"
            and self._current_oracle_id is not None
            and isinstance(event.value, int)
        ):
            self.post_message(
                self.PrintingSelected(self._current_oracle_id, event.value)
            )


# ── Search screen ──────────────────────────────────────────────────────────────

# Modes for SearchScreen
_MODE_COMMANDER = "commander"
_MODE_PARTNER = "partner"
_MODE_GROUP = "group"


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
                _MODE_COMMANDER: "Search for a commander",
                _MODE_PARTNER: "Search for a partner",
                _MODE_GROUP: f"Add cards to '{self._group.name}'" if self._group else "Search cards",
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

    # ── search logic ────────────────────────────────────────────────────────────

    def _run_search(self, query: str) -> None:
        node = parse_query(query)
        implied = self._implied_node()
        if implied is not None:
            node = And([implied] + (node.children if isinstance(node, And) else [node]))

        results = self._db.query(node)

        if self._mode == _MODE_COMMANDER:
            results = [
                c for c in results
                if (
                    "Legendary" in c.type_line
                    and ("Creature" in c.type_line or "Planeswalker" in c.type_line)
                )
                or "can be your commander" in c.oracle_text.lower()
            ]
        elif self._mode == _MODE_PARTNER and self._post_filter is not None:
            results = [c for c in results if self._post_filter(c)]

        self._results = results[:300]
        self._rebuild_list(restore_index=False)

    def _implied_node(self) -> Optional[Atom]:
        if self._mode == _MODE_GROUP:
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

    def _card_count(self, card: Card) -> int:
        if self._mode == _MODE_COMMANDER:
            return 1 if self._deck.commander and self._deck.commander.oracle_id == card.oracle_id else 0
        if self._mode == _MODE_PARTNER:
            return 1 if self._deck.partner and self._deck.partner.oracle_id == card.oracle_id else 0
        if self._mode == _MODE_GROUP and self._group is not None:
            return sum(1 for c in self._group.cards if c.oracle_id == card.oracle_id)
        return 0

    # ── actions ─────────────────────────────────────────────────────────────────

    def action_dismiss_screen(self) -> None:
        self.dismiss(None)

    def action_toggle_card(self) -> None:
        if isinstance(self.focused, Input) or self._current_card is None:
            return
        card = self._current_card
        if self._mode == _MODE_COMMANDER:
            new_cmd = (
                None if self._deck.commander and self._deck.commander.oracle_id == card.oracle_id
                else card
            )
            self._deck.commander = new_cmd
            # Clear partner if new commander doesn't support one, or the existing
            # partner is no longer valid for the new commander's partner type.
            if self._deck.partner is not None:
                info = partner_mode(new_cmd) if new_cmd else None
                if info is None or not _partner_filter(info)(self._deck.partner):
                    self._deck.partner = None
        elif self._mode == _MODE_PARTNER:
            self._deck.partner = (
                None if self._deck.partner and self._deck.partner.oracle_id == card.oracle_id
                else card
            )
        elif self._mode == _MODE_GROUP and self._group is not None:
            idx = next(
                (i for i, c in enumerate(self._group.cards) if c.oracle_id == card.oracle_id),
                None,
            )
            if idx is not None:
                del self._group.cards[idx]
            else:
                self._group.cards.append(card)
        self._rebuild_list(restore_index=True)

    def action_increment_card(self) -> None:
        if isinstance(self.focused, Input) or self._current_card is None:
            return
        card = self._current_card
        if self._mode == _MODE_GROUP and self._group is not None and _allows_multiple(card):
            self._group.cards.append(card)
            self._rebuild_list(restore_index=True)

    def action_decrement_card(self) -> None:
        if isinstance(self.focused, Input) or self._current_card is None:
            return
        card = self._current_card
        if self._mode == _MODE_GROUP and self._group is not None:
            idx = next(
                (i for i, c in enumerate(self._group.cards) if c.oracle_id == card.oracle_id),
                None,
            )
            if idx is not None:
                del self._group.cards[idx]
                self._rebuild_list(restore_index=True)


# ── App ────────────────────────────────────────────────────────────────────────

class DeckbuilderApp(App):
    CSS = """
    TopBar {
        height: 5;
        padding: 0 2;
        background: $surface;
        border-bottom: solid $primary;
    }
    #tb-info {
        width: 1fr;
        height: 100%;
    }
    #tb-right {
        width: 36;
        height: 100%;
    }
    #bottom {
        height: 1fr;
    }
    #groups {
        width: 1fr;
        border-right: solid $primary;
    }
    CardDetail {
        width: 1fr;
        padding: 1 2;
    }
    #cd-printing-label {
        margin-top: 1;
        color: $text-muted;
    }
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

    def _rebuild_tree(self) -> None:
        tree = self.query_one("#groups", Tree)
        tree.clear()
        for group in self._deck.groups:
            node = tree.root.add(f"{group.name}  ({len(group.cards)})", expand=True, data=group)
            for card in group.cards:
                node.add_leaf(card.name, data=card)
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
        self._push_search(_MODE_GROUP, group=group)

    def action_search_commander(self) -> None:
        self._push_search(_MODE_COMMANDER)

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

        # Toggle off if partner already set
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
            _MODE_PARTNER,
            post_filter=_partner_filter(info),
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
            group.cards.append(results[0])

    commanders = db.search(name="Atraxa, Praetors' Voice")
    deck = Deck(
        commander=commanders[0] if commanders else None,
        groups=[ramp, draw, removal],
    )

    DeckbuilderApp(db, deck, settings).run()
