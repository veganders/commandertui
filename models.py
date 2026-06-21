"""Deck data model — Group and Deck dataclasses."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from db import Card


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
