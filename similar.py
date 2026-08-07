"""Find-similar: tag-vector dot-product similarity search."""

from __future__ import annotations

from typing import Optional

from textual.app import ComposeResult
from textual.binding import Binding
from textual.widgets import Label, ListView

from rich.text import Text
from db import Card, CardDB
from models import Deck
from search import CardListScreen
from settings import Settings


def find_similar(
    card: Card,
    db: CardDB,
    color_identity: Optional[set[str]] = None,
    top_n: int = 50,
) -> list[tuple[Card, int]]:
    """Return the top_n cards most similar to card by leaf-tag dot product.

    Each card is a binary vector over its directly-assigned (leaf) oracle tags.
    Similarity = count of shared leaf tags.  Cards with zero overlap are excluded.
    If color_identity is given, only cards whose color_identity is a subset of it
    are considered (same semantics as the id: search filter).
    """
    target_tags = frozenset(db.get_leaf_tags(card.oracle_id))
    if not target_tags:
        return []

    results: list[tuple[Card, int]] = []
    for c in db.cards.values():
        if c.oracle_id == card.oracle_id:
            continue
        if color_identity is not None and not set(c.color_identity).issubset(color_identity):
            continue
        score = len(target_tags & frozenset(db.get_leaf_tags(c.oracle_id)))
        if score > 0:
            results.append((c, score))

    results.sort(key=lambda x: -x[1])
    return results[:top_n]


class SimilarCardsScreen(CardListScreen):
    """Show the top-N cards most similar to a given card by shared oracle leaf tags.

    Inherits all card-list rendering and deck-manipulation actions from
    CardListScreen.  Only overrides the score prefix hook and the layout.
    """

    BINDINGS = [
        Binding("escape", "dismiss", "Close"),
    ]

    CSS = CardListScreen.CSS + """
    SimilarCardsScreen { layout: vertical; background: $background; }
    #sim-header {
        height: 2;
        padding: 0 2;
        background: $surface;
        border-bottom: solid $primary;
        align: left middle;
        color: $text-muted;
    }
    """

    def __init__(
        self,
        source_card: Card,
        db: CardDB,
        deck: Deck,
        settings: Settings,
    ) -> None:
        super().__init__(db, deck, settings)
        self._source = source_card
        self._scores: dict[str, int] = {}  # oracle_id → similarity score

    def _compose_header(self) -> ComposeResult:
        yield Label(f"Similar to: {self._source.name}", id="sim-header")

    def on_mount(self) -> None:
        ci = self._deck_color_identity()
        pairs = find_similar(self._source, self._db, ci if ci else None)
        self._scores = {card.oracle_id: score for card, score in pairs}
        self._results = [card for card, _ in pairs]

        self._rebuild_list()
        lv = self.query_one("#card-list", ListView)
        lv.focus()
        if self._results:
            lv.index = 0

    def _card_extra_prefix(self, card: Card) -> Text:
        score = self._scores.get(card.oracle_id, 0)
        return Text(f"({score}) ", style="dim")
