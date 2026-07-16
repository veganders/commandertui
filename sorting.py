"""Card sort orders for tree display."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from models import CardEntry


class CardSorter(ABC):
    label: str = ""

    @abstractmethod
    def key(self, entry: "CardEntry") -> Any: ...


class NameSorter(CardSorter):
    label = "Name"

    def key(self, entry: "CardEntry") -> str:
        return entry.card.name.lower()


class MVSorter(CardSorter):
    label = "MV"

    def key(self, entry: "CardEntry") -> float:
        return entry.card.cmc


class PriceSorter(CardSorter):
    label = "Price"

    def __init__(self, currency: str) -> None:
        self._currency = currency

    def key(self, entry: "CardEntry") -> float:
        price = entry.price(self._currency)
        return price if price is not None else float("inf")
