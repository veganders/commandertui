"""Find-similar: tag-vector dot-product similarity search."""

from __future__ import annotations

from typing import Optional

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.widgets import Label, ListView  # Label used in _compose_header

from rich.text import Text
from db import Card, CardDB, parse_query
from models import Deck
from search import CardListScreen
from settings import Settings
from tag_filter import get_noise_tags, is_noise_tag
from widgets import AttributeChip, FilterSuggestions, QueryInput

# Flip to compare the two filtering strategies:
#   True  → filter candidate pool first, then score within it
#           (top-50 are the best matches *among* cards that pass the query)
#   False → score all cards first, then filter results, then cap
#           (top-50 are the best overall matches, minus cards that fail the query)
_FILTER_BEFORE_SCORE: bool = True
_TOP_N: int = 50


def _card_attrs(card: Card, db: CardDB) -> frozenset[str]:
    """Return a namespaced attribute set for this card.

    Combines oracle leaf tags ("tag:"), keywords ("kw:"), and type-line tokens
    ("type:") into a single frozenset used for similarity scoring and forced
    filtering.  Namespacing prevents collisions between the three sources.
    """
    return (
        {f"tag:{t}" for t in db.get_leaf_tags(card.oracle_id)}
        | {f"kw:{k}" for k in card.keywords}
        | {f"type:{t}" for t in db.get_type_tokens(card.oracle_id)}
    )


def find_similar(
    card: Card,
    db: CardDB,
    color_identity: Optional[set[str]] = None,
    top_n: Optional[int] = _TOP_N,
    candidate_ids: Optional[set[str]] = None,
    tracked_attrs: Optional[frozenset[str]] = None,
    forced_attrs: Optional[frozenset[str]] = None,
) -> list[tuple[Card, int]]:
    """Return the top_n cards most similar to card by attribute dot product.

    Each card is a binary vector over its attributes (oracle leaf tags, keywords,
    and type-line tokens, each namespaced).  Similarity = count of shared
    tracked attributes.  Cards with zero overlap are excluded unless forced_attrs
    is non-empty.

    If tracked_attrs is None (legacy call), the target set defaults to the source
    card's non-noise leaf oracle tags (prefixed "tag:"), reproducing the original
    behaviour.  If forced_attrs is given, candidates missing any of those
    attributes are skipped entirely.
    If color_identity is given, only cards whose color_identity ⊆ it are scored.
    If candidate_ids is given, only cards in that set are scored.
    If top_n is None, all matching cards are returned without a cap.
    """
    if tracked_attrs is None:
        # Legacy path: derive target from source card's non-noise leaf tags.
        noise = get_noise_tags(db)
        target_attrs: frozenset[str] = frozenset(
            f"tag:{t}" for t in db.get_leaf_tags(card.oracle_id)
            if not is_noise_tag(t, noise)
        )
    else:
        target_attrs = tracked_attrs

    if not target_attrs and not forced_attrs:
        return []

    results: list[tuple[Card, int]] = []
    for c in db.cards.values():
        if c.oracle_id == card.oracle_id:
            continue
        if color_identity is not None and not set(c.color_identity).issubset(color_identity):
            continue
        if candidate_ids is not None and c.oracle_id not in candidate_ids:
            continue
        c_attrs = _card_attrs(c, db)
        if forced_attrs and not forced_attrs.issubset(c_attrs):
            continue
        score = len(target_attrs & c_attrs)
        if score > 0 or forced_attrs:
            results.append((c, score))

    results.sort(key=lambda x: -x[1])
    return results if top_n is None else results[:top_n]


class SimilarCardsScreen(CardListScreen):
    """Show the top-N cards most similar to a given card by shared oracle leaf tags.

    Inherits all card-list rendering, deck-manipulation actions, and search-bar
    event handling from CardListScreen.  Only overrides the score prefix hook,
    the search logic, and the layout header.
    """

    BINDINGS = [
        Binding("escape", "dismiss", "Close"),
    ]

    CSS = CardListScreen.CSS + """
    SimilarCardsScreen { layout: vertical; background: $background; }
    #sim-bar {
        height: 3;
        padding: 0 1;
        background: $surface;
        border-bottom: solid $primary;
        align: left middle;
    }
    #sim-title { width: auto; padding: 0 1; color: $text-muted; }
    #sim-input { width: 1fr; }
    #attr-chips {
        height: auto;
        background: $panel;
        border-bottom: solid $primary;
    }
    .chip-row {
        height: auto;
        padding: 0 1;
        align: left middle;
    }
    AttributeChip.untracked { background: $surface; }
    AttributeChip.tracked   { background: $primary; }
    AttributeChip.locked    { background: $success; }
    QueryInput.query-error { background: $error 25%; }
    QueryInput.query-error:focus { background: $error 35%; }
    """

    def __init__(
        self,
        source_card: Card,
        db: CardDB,
        deck: Deck,
        settings: Settings,
        filter_candidates: Optional[dict] = None,
    ) -> None:
        super().__init__(db, deck, settings)
        self._source = source_card
        self._scores: dict[str, int] = {}   # oracle_id → similarity score
        self._chip_states: dict[str, str] = {}  # namespaced key → state
        self._last_query: str = ""
        self._suggestions = FilterSuggestions(
            self, "#sim-input", "#sim-suggest", filter_candidates or {}
        )

    def _compose_header(self) -> ComposeResult:
        with Horizontal(id="sim-bar"):
            yield Label(f"Similar to: {self._source.name}", id="sim-title")
            yield QueryInput(
                placeholder="Filter: eur<=1  t:creature  otag:ramp  …",
                id="sim-input",
                select_on_focus=False,
                delay=0.8,
            )
        yield ListView(id="sim-suggest", classes="filter-suggest")
        yield Vertical(id="attr-chips")

    def on_mount(self) -> None:
        # Build chip states before first search so _run_search sees them.
        noise = get_noise_tags(self._db)
        for tag in sorted(self._db.get_leaf_tags(self._source.oracle_id)):
            if not is_noise_tag(tag, noise):
                self._chip_states[f"tag:{tag}"] = "tracked"
        for kw in sorted(self._source.keywords):
            self._chip_states[f"kw:{kw}"] = "untracked"
        for tok in sorted(self._db.get_type_tokens(self._source.oracle_id)):
            self._chip_states[f"type:{tok}"] = "untracked"

        self._run_search("")

        # Mount chips into the panel, one Horizontal row per section.
        chips_panel = self.query_one("#attr-chips", Vertical)
        for prefix in ("tag:", "kw:", "type:"):
            section = [(k, v) for k, v in self._chip_states.items() if k.startswith(prefix)]
            if not section:
                continue
            row = Horizontal(classes="chip-row")
            chips_panel.mount(row)
            for key, state in section:
                row.mount(AttributeChip(
                    key=key,
                    label=key[len(prefix):],
                    state=state,
                ))

        lv = self.query_one("#card-list", ListView)
        lv.focus()
        if self._results:
            lv.index = 0

    def on_attribute_chip_state_changed(self, event: AttributeChip.StateChanged) -> None:
        self._chip_states[event.chip.key] = event.chip.state
        self._run_search(self._last_query)

    def _run_search(self, query: str) -> None:
        self._last_query = query
        candidate_ids = None
        if query.strip():
            candidate_ids = {c.oracle_id for c in self._db.query(parse_query(query))}
        ci = self._deck_color_identity()

        tracked = frozenset(k for k, s in self._chip_states.items() if s in ("tracked", "locked"))
        forced = frozenset(k for k, s in self._chip_states.items() if s == "locked")

        if _FILTER_BEFORE_SCORE or candidate_ids is None:
            pairs = find_similar(
                self._source, self._db, ci if ci else None,
                candidate_ids=candidate_ids,
                tracked_attrs=tracked,
                forced_attrs=forced if forced else None,
            )
        else:
            all_pairs = find_similar(
                self._source, self._db, ci if ci else None, top_n=None,
                tracked_attrs=tracked,
                forced_attrs=forced if forced else None,
            )
            pairs = [(c, s) for c, s in all_pairs if c.oracle_id in candidate_ids][:_TOP_N]

        self._scores = {c.oracle_id: s for c, s in pairs}
        self._results = [c for c, _ in pairs]
        self._rebuild_list()

    def _card_extra_prefix(self, card: Card) -> Text:
        score = self._scores.get(card.oracle_id, 0)
        return Text(f"({score}) ", style="dim")
