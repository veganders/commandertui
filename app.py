"""Deckbuilder TUI."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from rich.text import Text
from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.message import Message
from textual.widget import Widget
from textual.widgets import Footer, Select, Static, Tree

from db import Card, CardDB, load_db
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
        seen: set[str] = set()
        result = []
        for g in self.groups:
            for c in g.cards:
                if c.oracle_id not in seen:
                    result.append(c)
                    seen.add(c.oracle_id)
        return result

    def card_count(self) -> int:
        return len(self.unique_cards())

    def mana_curve(self) -> list[int]:
        buckets = [0] * 7
        for c in self.unique_cards():
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
        cards = self.unique_cards()
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

    BINDINGS = [("q", "quit", "Quit")]

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
            node = tree.root.add(f"{group.name}  ({len(group.cards)})", expand=True)
            for card in group.cards:
                node.add_leaf(card.name, data=card)
        tree.root.expand()

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
