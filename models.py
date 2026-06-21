"""Deck data model — CardEntry, Group, Deck."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from db import Card


@dataclass
class CardEntry:
    card: Card
    count: int = 1


@dataclass
class Group:
    name: str
    cards: list[CardEntry] = field(default_factory=list)

    def find(self, oracle_id: str) -> Optional[CardEntry]:
        return next((e for e in self.cards if e.card.oracle_id == oracle_id), None)

    def count_of(self, oracle_id: str) -> int:
        entry = self.find(oracle_id)
        return entry.count if entry else 0

    def total_count(self) -> int:
        return sum(e.count for e in self.cards)

    def add(self, card: Card) -> None:
        entry = self.find(card.oracle_id)
        if entry is not None:
            entry.count += 1
        else:
            self.cards.append(CardEntry(card=card))

    def remove_one(self, oracle_id: str) -> None:
        entry = self.find(oracle_id)
        if entry is None:
            return
        entry.count -= 1
        if entry.count <= 0:
            self.cards.remove(entry)

    def remove_all(self, oracle_id: str) -> None:
        self.cards = [e for e in self.cards if e.card.oracle_id != oracle_id]


@dataclass
class Deck:
    commander: Optional[Card] = None
    partner: Optional[Card] = None
    groups: list[Group] = field(default_factory=list)
    selected_printings: dict[str, int] = field(default_factory=dict)

    def _group_entries(self) -> list[tuple[Card, int]]:
        """Total count per card across all groups (deduped by oracle_id)."""
        totals: dict[str, tuple[Card, int]] = {}
        for g in self.groups:
            for entry in g.cards:
                oid = entry.card.oracle_id
                if oid in totals:
                    totals[oid] = (entry.card, totals[oid][1] + entry.count)
                else:
                    totals[oid] = (entry.card, entry.count)
        return list(totals.values())

    def all_entries(self) -> list[tuple[Card, int]]:
        """Commander + partner (count 1 each) + all group cards, deduped."""
        result: list[tuple[Card, int]] = []
        seen: set[str] = set()
        for card in (self.commander, self.partner):
            if card and card.oracle_id not in seen:
                result.append((card, 1))
                seen.add(card.oracle_id)
        for card, count in self._group_entries():
            if card.oracle_id not in seen:
                result.append((card, count))
                seen.add(card.oracle_id)
        return result

    def card_count(self) -> int:
        return sum(count for _, count in self.all_entries())

    def mana_curve(self) -> list[int]:
        buckets = [0] * 7
        for card, count in self.all_entries():
            buckets[min(int(card.cmc), 6)] += count
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
        all_count = 0
        for card, count in self.all_entries():
            all_count += count
            if not card.printings:
                continue
            idx = self.get_printing_idx(card, currency)
            price = card.printings[idx].prices.get(currency)
            if price is not None:
                total += price * count
                priced += count
        return total, priced, all_count
