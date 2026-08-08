"""Deck tree grouping strategies — parallel to sorting.py."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Callable, Optional

from models import MAYBEBOARD, CardEntry, Deck, Group
from sorting import CardSorter


class CardGrouper(ABC):
    """Return a sequence of (display_name, entries, group_data) for the tree.

    group_data is the Group object for real groups (so the d-key can act on it)
    or None for synthetic groups (d-key is a no-op on those nodes).
    """

    @property
    @abstractmethod
    def label(self) -> str: ...

    @abstractmethod
    def groups(
        self,
        deck: Deck,
        passes: Callable[[CardEntry], bool],
        sorter: CardSorter,
        has_filter: bool = False,
    ) -> list[tuple[str, list[CardEntry], Optional[Group]]]:
        ...


def _maybeboard_rows(
    deck: Deck,
    passes: Callable[[CardEntry], bool],
    sorter: CardSorter,
) -> list[tuple[str, list[CardEntry], Optional[Group]]]:
    """Return a single Maybeboard row if it exists and has entries, else []."""
    mb_group = next((g for g in deck.groups if g.name == MAYBEBOARD), None)
    if mb_group is None:
        return []
    entries = sorted(
        [e for e in deck.entries_for_group(MAYBEBOARD) if passes(e)],
        key=sorter.key,
    )
    return [(mb_group.name, entries, mb_group)] if entries else []


class NamedGrouper(CardGrouper):
    """User-defined groups in deck order, followed by Uncategorized."""

    @property
    def label(self) -> str: return "Named"

    def groups(self, deck, passes, sorter, has_filter=False):
        result = []
        for group in deck.groups:
            raw = deck.entries_for_group(group.name)
            entries = sorted(
                [e for e in raw if passes(e) and (group.name == MAYBEBOARD or not e.is_maybe())],
                key=sorter.key,
            )
            if not entries and has_filter:
                continue
            result.append((group.name, entries, group))

        uncategorized = sorted(
            [e for e in deck.uncategorized_entries() if not e.is_maybe() and passes(e)],
            key=sorter.key,
        )
        if uncategorized:
            result.append(("Uncategorized", uncategorized, None))

        return result


_TYPE_ORDER = [
    "creature", "instant", "sorcery", "artifact",
    "enchantment", "planeswalker", "battle", "land",
]


def _type_bucket(card) -> str:
    for t in _TYPE_ORDER:
        if card.has_type(t):
            return t
    return "other"


class TypeGrouper(CardGrouper):
    """One group per primary card type, Maybeboard appended last."""

    @property
    def label(self) -> str: return "Type"

    def groups(self, deck, passes, sorter, has_filter=False):
        buckets: dict[str, list] = {t: [] for t in _TYPE_ORDER + ["other"]}
        for entry in deck.entries.values():
            if not entry.is_maybe() and passes(entry):
                buckets[_type_bucket(entry.card)].append(entry)

        result = []
        for t in _TYPE_ORDER + ["other"]:
            entries = sorted(buckets[t], key=sorter.key)
            if entries:
                result.append((t.capitalize(), entries, None))

        result.extend(_maybeboard_rows(deck, passes, sorter))
        return result


class MVGrouper(CardGrouper):
    """One group per exact mana value, lands separate, Maybeboard appended last."""

    @property
    def label(self) -> str: return "MV"

    def groups(self, deck, passes, sorter, has_filter=False):
        mv_buckets: dict[int, list] = {}
        land_bucket: list = []

        for entry in deck.entries.values():
            if not entry.is_maybe() and passes(entry):
                if all("land" in f.type_line for f in entry.card.faces):
                    land_bucket.append(entry)
                else:
                    mv_buckets.setdefault(int(entry.card.cmc), []).append(entry)

        result = []
        for mv in sorted(mv_buckets):
            entries = sorted(mv_buckets[mv], key=sorter.key)
            result.append((f"MV {mv}", entries, None))

        if land_bucket:
            result.append(("Land", sorted(land_bucket, key=sorter.key), None))

        result.extend(_maybeboard_rows(deck, passes, sorter))
        return result
