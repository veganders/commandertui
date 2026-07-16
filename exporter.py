"""Abstract base class for deck exporters."""

from __future__ import annotations

from abc import ABC, abstractmethod

from models import Deck


class DeckExporter(ABC):
    @property
    @abstractmethod
    def name(self) -> str: ...

    @abstractmethod
    def export(self, deck: Deck) -> None: ...
