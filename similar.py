"""Find-similar: tag-vector dot-product similarity search."""

from __future__ import annotations

from typing import Optional

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal
from textual.widgets import Label, ListView

from rich.text import Text
from db import Card, CardDB, parse_query
from models import Deck
from search import CardListScreen
from settings import Settings
from widgets import FilterSuggestions, QueryInput

# Flip to compare the two filtering strategies:
#   True  → filter candidate pool first, then score within it
#           (top-50 are the best matches *among* cards that pass the query)
#   False → score all cards first, then filter results, then cap
#           (top-50 are the best overall matches, minus cards that fail the query)
_FILTER_BEFORE_SCORE: bool = True
_TOP_N: int = 50

# Leaf tags excluded from similarity scoring because they describe card name or
# formatting rather than gameplay function. Extend as needed.
_TAG_BLACKLIST: frozenset[str] = frozenset([
    "alliteration",              # 12.4% — card name happens to have alliterative words
    "namesake spell",            # 4.7%  — spell named after an existing character
    "single english word name",  # 3.9%  — card name is a single word
    "unique type line",          # 6.0%  — has a type line no other card shares
])


def _is_noise_tag(tag: str) -> bool:
    """True for tags that should be excluded from similarity scoring."""
    t = tag.lower()
    return tag in _TAG_BLACKLIST or "errata" in t or t.startswith("cycle-")


def find_similar(
    card: Card,
    db: CardDB,
    color_identity: Optional[set[str]] = None,
    top_n: Optional[int] = _TOP_N,
    candidate_ids: Optional[set[str]] = None,
) -> list[tuple[Card, int]]:
    """Return the top_n cards most similar to card by leaf-tag dot product.

    Each card is a binary vector over its directly-assigned (leaf) oracle tags.
    Similarity = count of shared leaf tags.  Cards with zero overlap are excluded.
    If color_identity is given, only cards whose color_identity is a subset of it
    are considered (same semantics as the id: search filter).
    If candidate_ids is given, only cards in that set are scored.
    If top_n is None, all matching cards are returned without a cap.
    """
    target_tags = frozenset(t for t in db.get_leaf_tags(card.oracle_id) if not _is_noise_tag(t))
    if not target_tags:
        return []

    results: list[tuple[Card, int]] = []
    for c in db.cards.values():
        if c.oracle_id == card.oracle_id:
            continue
        if color_identity is not None and not set(c.color_identity).issubset(color_identity):
            continue
        if candidate_ids is not None and c.oracle_id not in candidate_ids:
            continue
        score = len(target_tags & frozenset(db.get_leaf_tags(c.oracle_id)))
        if score > 0:
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
        self._scores: dict[str, int] = {}  # oracle_id → similarity score
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

    def on_mount(self) -> None:
        self._run_search("")
        lv = self.query_one("#card-list", ListView)
        lv.focus()
        if self._results:
            lv.index = 0

    def _run_search(self, query: str) -> None:
        candidate_ids = None
        if query.strip():
            candidate_ids = {c.oracle_id for c in self._db.query(parse_query(query))}
        ci = self._deck_color_identity()

        if _FILTER_BEFORE_SCORE or candidate_ids is None:
            pairs = find_similar(
                self._source, self._db, ci if ci else None,
                candidate_ids=candidate_ids,
            )
        else:
            all_pairs = find_similar(
                self._source, self._db, ci if ci else None, top_n=None,
            )
            pairs = [(c, s) for c, s in all_pairs if c.oracle_id in candidate_ids][:_TOP_N]

        self._scores = {c.oracle_id: s for c, s in pairs}
        self._results = [c for c, _ in pairs]
        self._rebuild_list()

    def _card_extra_prefix(self, card: Card) -> Text:
        score = self._scores.get(card.oracle_id, 0)
        return Text(f"({score}) ", style="dim")
