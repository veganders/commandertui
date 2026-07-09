"""Deck data model — CardRole, CardEntry, Group, Deck."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum, auto
from pathlib import Path
from typing import Optional

from db import Card

MAYBEBOARD = "Maybeboard"


class CardRole(Enum):
    MAIN = auto()
    COMMANDER = auto()
    PARTNER = auto()


@dataclass
class CardEntry:
    card: Card
    count: int = 1
    groups: set[str] = field(default_factory=set)
    printing_idx: int = 0
    role: CardRole = CardRole.MAIN
    color_identity_override: Optional[list[str]] = None

    @property
    def color_identity(self) -> list[str]:
        if self.color_identity_override is not None:
            return self.color_identity_override
        return self.card.color_identity

    def in_group(self, name: str) -> bool:
        return name in self.groups

    def join_group(self, name: str) -> None:
        self.groups.add(name)

    def leave_group(self, name: str) -> None:
        self.groups.discard(name)

    def is_maybe(self) -> bool:
        return MAYBEBOARD in self.groups

    def price(self, currency: str) -> float | None:
        if not self.card.printings or not (0 <= self.printing_idx < len(self.card.printings)):
            return None
        return self.card.printings[self.printing_idx].prices.get(currency)


@dataclass
class Group:
    name: str
    permanent: bool = False


@dataclass
class Deck:
    commander: Optional[CardEntry] = None
    partner: Optional[CardEntry] = None
    groups: list[Group] = field(default_factory=list)
    entries: dict[str, CardEntry] = field(default_factory=dict)  # oracle_id → CardEntry
    selected_printings: dict[str, int] = field(default_factory=dict)  # cache for non-deck cards
    name: Optional[str] = None
    save_path: Optional[Path] = None

    def get_entry_for_card(self, oracle_id: str) -> Optional[CardEntry]:
        """Find any entry (commander, partner, or main) by oracle_id."""
        if self.commander and self.commander.card.oracle_id == oracle_id:
            return self.commander
        if self.partner and self.partner.card.oracle_id == oracle_id:
            return self.partner
        return self.entries.get(oracle_id)

    def get_entry(self, oracle_id: str) -> Optional[CardEntry]:
        return self.entries.get(oracle_id)

    def count_of(self, oracle_id: str) -> int:
        entry = self.entries.get(oracle_id)
        return entry.count if entry else 0

    def make_entry(self, card: Card, role: CardRole = CardRole.MAIN) -> CardEntry:
        """Create a CardEntry, consuming any cached printing selection for this card."""
        printing_idx = self.selected_printings.pop(card.oracle_id, 0)
        return CardEntry(card=card, role=role, printing_idx=printing_idx)

    def add(self, card: Card) -> None:
        if card.oracle_id in self.entries:
            self.entries[card.oracle_id].count += 1
        else:
            self.entries[card.oracle_id] = self.make_entry(card)

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

    def all_entries(self) -> list[CardEntry]:
        """Commander + partner (count 1 each) + all deck entries, deduped by oracle_id."""
        result: list[CardEntry] = []
        seen: set[str] = set()
        for entry in (self.commander, self.partner):
            if entry and entry.card.oracle_id not in seen:
                result.append(entry)
                seen.add(entry.card.oracle_id)
        for entry in self.entries.values():
            if entry.card.oracle_id not in seen and not entry.is_maybe():
                result.append(entry)
                seen.add(entry.card.oracle_id)
        return result

    def card_count(self) -> int:
        return sum(e.count for e in self.all_entries())

    def mana_curve(self) -> list[int]:
        buckets = [0] * 7
        for entry in self.all_entries():
            card = entry.card
            if all("Land" in face for face in card.type_line.split(" // ")):
                continue
            buckets[min(int(card.cmc), 6)] += entry.count
        return buckets

    def get_printing_idx(self, card: Card, currency: str = "") -> int:
        """Return stored printing_idx for deck cards; cheapest printing for non-deck cards."""
        entry = self.get_entry_for_card(card.oracle_id)
        if entry is not None:
            return entry.printing_idx
        if card.oracle_id in self.selected_printings:
            idx = self.selected_printings[card.oracle_id]
            if 0 <= idx < len(card.printings):
                return idx
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
        for entry in self.all_entries():
            all_count += entry.count
            p = entry.price(currency)
            if p is not None:
                total += p * entry.count
                priced += entry.count
        return total, priced, all_count
