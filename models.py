"""Deck data model — CardEntry, Group, Deck."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from db import Card


@dataclass
class CardEntry:
    card: Card
    count: int = 1
    groups: set[str] = field(default_factory=set)

    def in_group(self, name: str) -> bool:
        return name in self.groups

    def join_group(self, name: str) -> None:
        self.groups.add(name)

    def leave_group(self, name: str) -> None:
        self.groups.discard(name)


@dataclass
class Group:
    name: str
    permanent: bool = False


@dataclass
class Deck:
    commander: Optional[Card] = None
    partner: Optional[Card] = None
    groups: list[Group] = field(default_factory=list)
    entries: dict[str, CardEntry] = field(default_factory=dict)  # oracle_id -> CardEntry
    selected_printings: dict[str, int] = field(default_factory=dict)

    def get_entry(self, oracle_id: str) -> Optional[CardEntry]:
        return self.entries.get(oracle_id)

    def count_of(self, oracle_id: str) -> int:
        entry = self.entries.get(oracle_id)
        return entry.count if entry else 0

    def add(self, card: Card) -> None:
        if card.oracle_id in self.entries:
            self.entries[card.oracle_id].count += 1
        else:
            self.entries[card.oracle_id] = CardEntry(card=card)

    def remove_one(self, oracle_id: str) -> None:
        """Decrement count; removes entry (and all group memberships) when count hits 0."""
        entry = self.entries.get(oracle_id)
        if entry is None:
            return
        entry.count -= 1
        if entry.count <= 0:
            del self.entries[oracle_id]

    def remove_all(self, oracle_id: str) -> None:
        self.entries.pop(oracle_id, None)

    def entries_for_group(self, group_name: str) -> list[CardEntry]:
        return [e for e in self.entries.values() if group_name in e.groups]

    def uncategorized_entries(self) -> list[CardEntry]:
        return [e for e in self.entries.values() if not e.groups]

    def all_entries(self) -> list[tuple[Card, int]]:
        """Commander + partner (count 1 each) + all deck entries."""
        result: list[tuple[Card, int]] = []
        seen: set[str] = set()
        for card in (self.commander, self.partner):
            if card and card.oracle_id not in seen:
                result.append((card, 1))
                seen.add(card.oracle_id)
        for entry in self.entries.values():
            if entry.card.oracle_id not in seen:
                result.append((entry.card, entry.count))
                seen.add(entry.card.oracle_id)
        return result

    def card_count(self) -> int:
        return sum(count for _, count in self.all_entries())

    def mana_curve(self) -> list[int]:
        buckets = [0] * 7
        for card, count in self.all_entries():
            if all("Land" in face for face in card.type_line.split(" // ")):
                continue
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
