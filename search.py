"""Card search screen."""

from __future__ import annotations

from typing import Callable, Optional

from textual import events
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal
from textual.screen import Screen
from textual.widgets import Input, Label, ListItem, ListView

from rich.text import Text
from db import And, Atom, Card, CardDB, Not, parse_query
from models import CardEntry, CardRole, Deck, Group
from partner import partner_mode, partner_filter
from settings import Settings
from widgets import CardDetail

MODE_COMMANDER = "commander"
MODE_PARTNER = "partner"
MODE_GROUP = "group"


class _SmartInput(Input):
    """Input that auto-pairs quotes and jumps over existing closing quotes.

    Calls event.prevent_default() to break Textual's MRO dispatch loop so
    Input._on_key doesn't run a second time and double-insert the character.
    """

    async def _on_key(self, event: events.Key) -> None:
        if event.character != '"':
            await super()._on_key(event)
            return
        self._restart_blink()
        pos = self.cursor_position
        val = self.value
        if pos < len(val) and val[pos] == '"':
            self.cursor_position = pos + 1          # jump over existing closing quote
        else:
            self.insert_text_at_cursor('""')        # insert paired quotes
            self.cursor_position = pos + 1          # cursor between them
        event.prevent_default()  # stops Input._on_key from running via MRO dispatch
        event.stop()


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
        self._all_tags: list[str] = []
        self._suggest_matches: list[str] = []
        self._suggest_token_start: int = 0
        self._suggest_token_end: int = 0

    def compose(self) -> ComposeResult:
        with Horizontal(id="srch-bar"):
            yield _SmartInput(placeholder=self._placeholder(), id="srch-input", select_on_focus=False)
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
        return base + "  —  t:type  o:oracle  id:wubrg  c:rg  otag:ramp  mv>=3  eur<=1  -t:land"

    def on_mount(self) -> None:
        self._all_tags = sorted({t for tags in self._db.tags.values() for t in tags})
        self._run_search("")
        self.query_one("#srch-input", _SmartInput).focus()

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id != "srch-input":
            return
        inp = event.input
        # Read value and cursor together so they're consistent with each other.
        val = inp.value
        pos = inp.cursor_position
        self._update_suggestions(val, pos)
        if self._search_timer is not None:
            self._search_timer.stop()
        self._search_timer = self.set_timer(1.0, lambda: self._run_search(val))

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id != "srch-input":
            return
        self.query_one("#srch-suggest", ListView).display = False
        if self._search_timer is not None:
            self._search_timer.stop()
            self._search_timer = None
        self._run_search(event.value)

    def on_key(self, event) -> None:
        sugg = self.query_one("#srch-suggest", ListView)
        inp = self.query_one("#srch-input", _SmartInput)

        if sugg.display:
            if self.focused is inp:
                if event.key == "down":
                    sugg.focus()
                    sugg.index = 0
                    event.stop()
                    return
                if event.key == "escape":
                    sugg.display = False
                    event.stop()
                    return
            elif self.focused is sugg:
                if event.key == "escape":
                    sugg.display = False
                    inp.focus()
                    event.stop()
                    return
                if event.key == "up" and (sugg.index is None or sugg.index == 0):
                    inp.focus()
                    event.stop()
                    return

        # Down/tab from input moves to result list (only when suggestions are hidden)
        if event.key in ("down", "tab") and isinstance(self.focused, Input):
            if not sugg.display:
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
        if event.list_view.id != "srch-suggest":
            return
        idx = event.list_view.index
        if idx is not None and 0 <= idx < len(self._suggest_matches):
            self._apply_suggestion(self._suggest_matches[idx])
        event.stop()

    def on_card_detail_printing_selected(self, msg: CardDetail.PrintingSelected) -> None:
        entry = self._deck.get_entry_for_card(msg.oracle_id)
        if entry is not None:
            entry.printing_idx = msg.printing_idx
        else:
            self._deck.selected_printings[msg.oracle_id] = msg.printing_idx

    # ── autocomplete ───────────────────────────────────────────────────────────

    def _otag_context(self, value: str, pos: int) -> tuple[int, int, str] | None:
        """If cursor is inside an otag: token, return (token_start, token_end, partial).

        Handles: otag:ramp, -otag:ramp, otag:"card draw (mid-typing), otag:"card draw" (complete).
        Returns None if not in an otag token or the token already has a closing quote.
        """
        before = value[:pos]

        # Find start of current token — last unquoted space before cursor
        in_quote = False
        token_start = 0
        for i, ch in enumerate(before):
            if ch == '"':
                in_quote = not in_quote
            elif ch == ' ' and not in_quote:
                token_start = i + 1

        token = before[token_start:]
        stripped = token.lstrip('-')
        if not stripped.lower().startswith('otag:'):
            return None

        after_colon = stripped[5:]

        if after_colon.startswith('"'):
            inner = after_colon[1:]
            if '"' in inner:
                return None  # closing quote present — token is complete
            partial = inner
        else:
            partial = after_colon

        return token_start, pos, partial

    def _update_suggestions(self, value: str, pos: int) -> None:
        sugg = self.query_one("#srch-suggest", ListView)
        ctx = self._otag_context(value, pos)

        if ctx is None:
            if sugg.display:
                sugg.display = False
            return

        token_start, token_end, partial = ctx
        partial_lower = partial.lower()
        matches = [t for t in self._all_tags if partial_lower in t][:12]

        if not matches:
            if sugg.display:
                sugg.display = False
            return

        self._suggest_token_start = token_start
        self._suggest_token_end = token_end
        self._suggest_matches = matches

        sugg.clear()
        for tag in matches:
            sugg.append(ListItem(Label(tag)))

        # Align left edge with the 'otag:' token (+2: srch-bar padding + input padding)
        sugg.styles.margin = (0, 0, 0, min(token_start + 2, 24))
        sugg.display = True

    def _apply_suggestion(self, tag: str) -> None:
        inp = self.query_one("#srch-input", _SmartInput)
        sugg = self.query_one("#srch-suggest", ListView)

        val = inp.value
        pos = inp.cursor_position

        # Re-detect so token bounds are fresh (user may have kept typing)
        ctx = self._otag_context(val, pos)
        if ctx:
            token_start, token_end, _ = ctx
        else:
            token_start, token_end = self._suggest_token_start, self._suggest_token_end

        # inp.replace() uses exclusive end (Python slice semantics).
        # token_end is already past the partial — val[token_end:] is the tail to keep.
        replace_end = token_end
        if token_end < len(val) and val[token_end] == '"':
            replace_end = token_end + 1  # also consume the auto-paired closing '"'

        neg = val[token_start:token_start + 1] == '-'
        pfx = '-otag:' if neg else 'otag:'
        replacement = f'{pfx}"{tag}"' if ' ' in tag else f'{pfx}{tag}'

        inp.replace(replacement, token_start, replace_end)

        sugg.display = False
        inp.focus()
        self._run_search(inp.value)

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
            count = self._card_count(card)
            base = card.display_label(currency, self._deck.get_printing_idx(card, currency))
            if count:
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
            count = self._card_count(card)
            base = card.display_label(currency, self._deck.get_printing_idx(card, currency))
            item = items[i]
            label = item.query_one(Label)
            if count:
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

    def _route_groups(self, card: Card) -> list[Group]:
        """Return groups the card should auto-route into based on type/tags."""
        name_map = {g.name.lower(): g for g in self._deck.groups}
        tags = {t.lower() for t in self._db.get_tags(card.oracle_id)}
        targets: list[Group] = []

        if any("land" in face.lower() for face in card.type_line.split(" // ")):
            if "lands" in name_map:
                targets.append(name_map["lands"])
        if any("ramp" in t for t in tags) and "ramp" in name_map:
            targets.append(name_map["ramp"])
        if any("draw" in t for t in tags) and "draw" in name_map:
            targets.append(name_map["draw"])

        return targets

    # ── actions ────────────────────────────────────────────────────────────────

    def action_dismiss_screen(self) -> None:
        self.dismiss(None)

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
                new_cmd = CardEntry(card=card, role=CardRole.COMMANDER)
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
                self._deck.partner = CardEntry(card=card, role=CardRole.PARTNER)
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
