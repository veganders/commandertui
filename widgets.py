"""Shared TUI widgets: TopBar and CardDetail."""

from __future__ import annotations

from typing import Optional

from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.message import Message
from textual.widget import Widget
from textual.widgets import Select, Static

from db import Card, CardDB
from models import Deck
from settings import Settings

# ── Display constants ──────────────────────────────────────────────────────────

CURRENCIES: list[tuple[str, str]] = [
    ("USD ($)", "usd"),
    ("EUR (€)", "eur"),
    ("MTGO (tix)", "tix"),
]
CURRENCY_SYMBOLS = {"usd": "$", "eur": "€", "tix": ""}

_BARS = " ▁▂▃▄▅▆▇█"
_CURVE_LABELS = ["0", "1", "2", "3", "4", "5", "6+"]


def bar(value: int, max_val: int) -> str:
    if max_val == 0:
        return _BARS[0]
    return _BARS[round(value / max_val * (len(_BARS) - 1))]


def fmt_price(price: Optional[float], currency: str) -> str:
    if price is None:
        return "N/A"
    sym = CURRENCY_SYMBOLS.get(currency, "")
    return f"{sym}{price:.2f}"


# ── TopBar ─────────────────────────────────────────────────────────────────────

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
                    CURRENCIES,
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
            t.append(bar(val, max_val))
            t.append(str(val))
            if i < len(_CURVE_LABELS) - 1:
                t.append("  ")
        self.query_one("#tb-stats", Static).update(t)

        total, priced, count = deck.total_cost(settings.currency)
        t = Text()
        t.append("Total: ", style="bold")
        t.append(fmt_price(total if priced else None, settings.currency))
        if 0 < priced < count:
            t.append(f" ({priced}/{count} cards)", style="dim")
        self.query_one("#tb-total", Static).update(t)

    def on_select_changed(self, event: Select.Changed) -> None:
        if event.select.id == "currency-select" and event.value is not Select.BLANK:
            self.post_message(self.CurrencyChanged(str(event.value)))


# ── CardDetail ─────────────────────────────────────────────────────────────────

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
                    f" — {fmt_price(p.prices.get(currency), currency)}",
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
