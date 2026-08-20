"""Abstract base class for deck importers."""

from __future__ import annotations

from abc import ABC, abstractmethod

from db import CardDB
from models import Deck


class DeckImporter(ABC):
    """Base class for all deck import backends.

    Each importer is responsible for one source site.  ``can_handle`` is called
    first to decide routing; ``import_deck`` performs the actual fetch and parse.
    """

    @abstractmethod
    def can_handle(self, url: str) -> bool:
        """Return True if this importer knows how to handle *url*."""

    @abstractmethod
    async def import_deck(self, url: str, db: CardDB) -> tuple[Deck, list[str]]:
        """Import a deck from *url* and return ``(deck, skipped_card_names)``."""
