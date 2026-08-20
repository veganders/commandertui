"""Import a deck from Archidekt by URL or deck ID."""

from __future__ import annotations

import re
from typing import Optional

import httpx

from db import Card, CardDB
from deck_io import _find_printing_idx
from importer import DeckImporter
from models import CardEntry, CardRole, Deck, Group, MAYBEBOARD

_API_URL = "https://archidekt.com/api/decks/{}/"
_DECK_ID_RE = re.compile(r'(?:archidekt\.com/decks/)?(\d+)')
_COUNT_SUFFIX_RE = re.compile(r'\s*\(\d+\s+Cards?\)\s*$', re.IGNORECASE)
_REQUEST_TIMEOUT_S = 10
_MODIFIER_TO_FINISH: dict[str, str] = {
    "Normal": "nonfoil",
    "Foil": "foil",
    "Etched": "etched",
}


def _extract_deck_id(url: str) -> Optional[str]:
    """Extract the numeric Archidekt deck ID from a full URL or bare integer.

    Accepts:
      - https://archidekt.com/decks/12345
      - https://archidekt.com/decks/12345/my-deck-slug
      - archidekt.com/decks/12345
      - 12345
    Returns None if no numeric ID can be found.
    """
    m = _DECK_ID_RE.search(url.strip())
    return m.group(1) if m else None


def _clean_category_name(name: str) -> str:
    """Strip Archidekt's trailing card-count suffix, e.g. 'Creatures (28 Cards)' → 'Creatures'."""
    return _COUNT_SUFFIX_RE.sub("", name).strip()


async def _fetch_deck_json(deck_id: str) -> dict:
    """Fetch the raw Archidekt deck JSON for the given numeric ID."""
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            _API_URL.format(deck_id),
            timeout=_REQUEST_TIMEOUT_S,
            follow_redirects=True,
        )
        resp.raise_for_status()
        return resp.json()


def _build_name_index(db: CardDB) -> tuple[dict[str, Card], dict[str, Card]]:
    """Build two name→Card lookup dicts from the DB.

    Returns (full_name_index, front_face_name_index).  Look up in full_name_index
    first (handles normal cards and DFCs with compound names like 'A // B');
    fall back to front_face_name_index for DFCs that Archidekt stores by front
    face name only.
    """
    full: dict[str, Card] = {}
    front: dict[str, Card] = {}
    for card in db.cards.values():
        full[card.name] = card
        if card.faces:
            front.setdefault(card.faces[0].name, card)
    return full, front


def _parse_archidekt_deck(data: dict, db: CardDB) -> tuple[Deck, list[str]]:
    """Parse a raw Archidekt API response into a Deck.

    Returns (deck, skipped_card_names) where skipped_card_names is the list of
    card names from Archidekt that could not be matched in the local DB.
    The returned deck has no save_path; caller should call
    _ensure_permanent_groups() after assigning it to the app.
    """
    full_index, front_index = _build_name_index(db)

    # Classify categories: premier (commander slot) and excluded-from-deck (maybeboard).
    premier_name: str = "Commander"
    maybeboard_cat_names: set[str] = set()
    for cat in data.get("categories", []):
        if cat.get("isPremier"):
            premier_name = cat["name"]
        if not cat.get("includedInDeck", True):
            maybeboard_cat_names.add(cat["name"])

    deck = Deck(name=data.get("name", "Imported deck"))
    skipped: list[str] = []

    # Accumulate group names seen on main-deck cards so we can create Group objects.
    seen_group_names: list[str] = []
    seen_group_name_set: set[str] = set()

    # Commanders arrive in order; first → commander, second → partner.
    commander_entries: list[CardEntry] = []

    for raw in data.get("cards", []):
        card_name: str = raw["card"]["oracleCard"]["name"]
        card = full_index.get(card_name) or front_index.get(card_name)
        if card is None:
            skipped.append(card_name)
            continue

        quantity: int = raw.get("quantity", 1)
        card_cats: list[str] = raw.get("categories", [])

        # Resolve printing.
        edition = raw["card"].get("edition", {})
        printing_data = {
            "set_code": edition.get("editioncode", ""),
            "collector_number": str(raw["card"].get("collectorNumber", "")),
            "finish": _MODIFIER_TO_FINISH.get(raw.get("modifier", "Normal"), "nonfoil"),
        }
        printing_idx = _find_printing_idx(card, printing_data)

        if premier_name in card_cats:
            # Commander or partner slot.
            role = CardRole.COMMANDER if not commander_entries else CardRole.PARTNER
            entry = CardEntry(card=card, count=quantity, role=role, printing_idx=printing_idx)
            commander_entries.append(entry)

        elif maybeboard_cat_names & set(card_cats):
            # Card is in a maybeboard-like category.
            entry = CardEntry(card=card, count=quantity, printing_idx=printing_idx)
            entry.groups = {MAYBEBOARD}
            deck.entries[card.oracle_id] = entry

        else:
            # Regular main-deck card.  Collect cleaned non-premier category names as groups.
            groups: set[str] = set()
            for cat in card_cats:
                if cat == premier_name or cat in maybeboard_cat_names:
                    continue
                clean = _clean_category_name(cat)
                if clean:
                    groups.add(clean)
                    if clean not in seen_group_name_set:
                        seen_group_name_set.add(clean)
                        seen_group_names.append(clean)
            entry = CardEntry(card=card, count=quantity, printing_idx=printing_idx)
            entry.groups = groups
            deck.entries[card.oracle_id] = entry

    # Assign commanders.
    if commander_entries:
        deck.commander = commander_entries[0]
    if len(commander_entries) > 1:
        deck.partner = commander_entries[1]

    # Build group list (insertion order = first appearance in card list).
    deck.groups = [Group(name=n, permanent=False) for n in seen_group_names]

    return deck, skipped


async def import_from_url(url: str, db: CardDB) -> tuple[Deck, list[str]]:
    """Import a deck from an Archidekt deck URL or bare deck ID.

    Raises ValueError if no deck ID can be extracted from url.
    Raises httpx.HTTPStatusError on HTTP errors (404 = private or non-existent deck).
    """
    deck_id = _extract_deck_id(url)
    if deck_id is None:
        raise ValueError(f"Could not find an Archidekt deck ID in: {url!r}")
    data = await _fetch_deck_json(deck_id)
    return _parse_archidekt_deck(data, db)


class ArchidektImporter(DeckImporter):
    """Importer for Archidekt deck URLs and bare numeric deck IDs."""

    def can_handle(self, url: str) -> bool:
        return "archidekt.com" in url

    async def import_deck(self, url: str, db: CardDB) -> tuple[Deck, list[str]]:
        return await import_from_url(url, db)
