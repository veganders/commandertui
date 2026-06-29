"""Shared TUI widgets: TopBar and CardDetail."""

from __future__ import annotations

import json
from pathlib import Path
from typing import NamedTuple, Optional

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.message import Message
from textual.screen import ModalScreen
from textual.widget import Widget
from textual.widgets import Input, Label, ListItem, ListView, Select, Static

from db import Card, CardDB
from models import Deck, Group
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


# ── GroupNameModal ─────────────────────────────────────────────────────────────

class GroupNameModal(ModalScreen[Optional[str]]):
    BINDINGS = [Binding("escape", "cancel", "Cancel")]

    CSS = """
    GroupNameModal {
        align: center middle;
        background: $background 60%;
    }
    #modal-box {
        width: 44;
        height: auto;
        background: $surface;
        border: solid $primary;
        padding: 1 2;
    }
    """

    def compose(self) -> ComposeResult:
        with Vertical(id="modal-box"):
            yield Label("New group name:")
            yield Input(id="name-input", placeholder="e.g. Removal")

    def on_mount(self) -> None:
        self.query_one("#name-input", Input).focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        val = event.value.strip()
        self.dismiss(val if val else None)

    def action_cancel(self) -> None:
        self.dismiss(None)


# ── DeckNameModal ──────────────────────────────────────────────────────────────

class DeckNameModal(ModalScreen[Optional[str]]):
    BINDINGS = [Binding("escape", "cancel", "Cancel")]

    CSS = """
    DeckNameModal {
        align: center middle;
        background: $background 60%;
    }
    #dn-box {
        width: 52;
        height: auto;
        background: $surface;
        border: solid $primary;
        padding: 1 2;
    }
    """

    def __init__(self, current_name: Optional[str] = None) -> None:
        super().__init__()
        self._current = current_name or ""

    def compose(self) -> ComposeResult:
        with Vertical(id="dn-box"):
            yield Label("Deck name:")
            yield Input(id="dn-input", placeholder="e.g. Lathril Elves", value=self._current)

    def on_mount(self) -> None:
        inp = self.query_one("#dn-input", Input)
        inp.focus()
        inp.cursor_position = len(inp.value)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        val = event.value.strip()
        self.dismiss(val if val else None)

    def action_cancel(self) -> None:
        self.dismiss(None)


# ── OpenDeckScreen ─────────────────────────────────────────────────────────────

class OpenDeckScreen(ModalScreen[Optional[Path]]):
    BINDINGS = [
        Binding("escape", "cancel", "Cancel"),
        Binding("enter", "select_deck", "Open"),
    ]

    CSS = """
    OpenDeckScreen {
        align: center middle;
        background: $background 60%;
    }
    #od-box {
        width: 60;
        height: auto;
        max-height: 26;
        background: $surface;
        border: solid $primary;
        padding: 1 2;
    }
    #od-list {
        height: auto;
        max-height: 20;
        border: none;
    }
    """

    def __init__(self, paths: list[Path]) -> None:
        super().__init__()
        self._paths = paths

    def compose(self) -> ComposeResult:
        with Vertical(id="od-box"):
            yield Label("Open deck:")
            yield ListView(id="od-list")

    def on_mount(self) -> None:
        lv = self.query_one("#od-list", ListView)
        for path in self._paths:
            try:
                data = json.loads(path.read_text())
                name = data.get("name") or path.stem
            except Exception:
                name = path.stem
            lv.append(ListItem(Label(name)))
        lv.focus()
        if self._paths:
            lv.index = 0

    def action_cancel(self) -> None:
        self.dismiss(None)

    def action_select_deck(self) -> None:
        lv = self.query_one("#od-list", ListView)
        idx = lv.index
        if idx is not None and 0 <= idx < len(self._paths):
            self.dismiss(self._paths[idx])
        else:
            self.dismiss(None)

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        idx = event.list_view.index
        if idx is not None and 0 <= idx < len(self._paths):
            self.dismiss(self._paths[idx])


# ── CardGroupEditorScreen ──────────────────────────────────────────────────────

class CardGroupEditorScreen(ModalScreen[None]):
    BINDINGS = [
        Binding("escape", "dismiss_screen", "Close"),
        Binding("space", "toggle_group", "Add/Remove"),
        Binding("+", "increment_group", "+1"),
        Binding("-", "decrement_group", "-1"),
    ]

    CSS = """
    CardGroupEditorScreen {
        align: center middle;
        background: $background 60%;
    }
    #cge-box {
        width: 52;
        height: auto;
        background: $surface;
        border: solid $primary;
        padding: 1 2;
    }
    #cge-title { margin-bottom: 1; }
    #cge-list {
        height: auto;
        max-height: 15;
        border: none;
    }
    #cge-new-input { margin-top: 1; }
    .group-member { color: $success; }
    """

    def __init__(self, card: Card, deck: Deck) -> None:
        super().__init__()
        self._card = card
        self._deck = deck

    def compose(self) -> ComposeResult:
        with Vertical(id="cge-box"):
            yield Label(f"Groups — {self._card.name}", id="cge-title")
            yield ListView(id="cge-list")
            yield Input(placeholder="New group name…", id="cge-new-input")

    def on_mount(self) -> None:
        self._rebuild_list()
        self.query_one("#cge-list", ListView).focus()

    def _rebuild_list(self) -> None:
        entry = self._deck.get_entry(self._card.oracle_id)
        count = entry.count if entry else 0
        count_str = f" ×{count}" if count > 1 else ""
        self.query_one("#cge-title", Label).update(f"Groups — {self._card.name}{count_str}")

        lv = self.query_one("#cge-list", ListView)
        old_idx = lv.index
        lv.clear()
        for group in self._deck.groups:
            if entry is not None and entry.in_group(group.name):
                item = ListItem(Label(f" [+] {group.name}"), classes="group-member")
            else:
                item = ListItem(Label(f"     {group.name}"))
            lv.append(item)
        if old_idx is not None and 0 <= old_idx < len(self._deck.groups):
            lv.index = old_idx

    def _current_group(self) -> Optional[Group]:
        lv = self.query_one("#cge-list", ListView)
        idx = lv.index
        if idx is None or idx < 0 or idx >= len(self._deck.groups):
            return None
        return self._deck.groups[idx]

    def on_key(self, event) -> None:
        if event.key in ("down", "tab") and isinstance(self.focused, Input):
            lv = self.query_one("#cge-list", ListView)
            lv.focus()
            if self._deck.groups:
                lv.index = 0
            event.stop()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        name = event.value.strip()
        if not name:
            return
        existing = {g.name.lower() for g in self._deck.groups}
        if name.lower() not in existing:
            self._deck.groups.append(Group(name=name))
            entry = self._deck.get_entry(self._card.oracle_id)
            if entry is not None:
                entry.join_group(name)
        event.input.value = ""
        self._rebuild_list()
        lv = self.query_one("#cge-list", ListView)
        lv.focus()
        if name.lower() not in existing:
            lv.index = len(self._deck.groups) - 1

    def action_dismiss_screen(self) -> None:
        self.dismiss(None)

    def action_toggle_group(self) -> None:
        if isinstance(self.focused, Input):
            return
        group = self._current_group()
        if group is None:
            return
        entry = self._deck.get_entry(self._card.oracle_id)
        if entry is None:
            return
        if entry.in_group(group.name):
            entry.leave_group(group.name)
        else:
            entry.join_group(group.name)
        self._rebuild_list()

    def action_increment_group(self) -> None:
        if isinstance(self.focused, Input) or not self._card.allows_multiple():
            return
        if self._deck.get_entry(self._card.oracle_id) is not None:
            self._deck.add(self._card)
            self._rebuild_list()

    def action_decrement_group(self) -> None:
        if isinstance(self.focused, Input):
            return
        if self._deck.count_of(self._card.oracle_id) > 0:
            self._deck.remove_one(self._card.oracle_id)
            if self._deck.count_of(self._card.oracle_id) == 0:
                self.dismiss(None)
            else:
                self._rebuild_list()


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
        if deck.name:
            t.append(deck.name, style="bold")
            t.append("   ")
        t.append("Commander: ", style="bold")
        t.append(deck.commander.name if deck.commander else "─ none ─")
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

class _PrintingKey(NamedTuple):
    oracle_id: str
    idx: int


class CardDetail(VerticalScroll):
    class PrintingSelected(Message):
        def __init__(self, oracle_id: str, printing_idx: int) -> None:
            super().__init__()
            self.oracle_id = oracle_id
            self.printing_idx = printing_idx

    def __init__(self) -> None:
        super().__init__()

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
                    _PrintingKey(card.oracle_id, i),
                )
                for i, p in enumerate(card.printings)
            ]
            select_w.set_options(options)
            select_w.value = _PrintingKey(card.oracle_id, deck.get_printing_idx(card, currency))
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
        if event.select.id == "cd-printing-select" and isinstance(event.value, _PrintingKey):
            self.post_message(self.PrintingSelected(event.value.oracle_id, event.value.idx))
