"""Tests for archidekt_import.py — pure logic, no network calls."""

import pytest
from archidekt_import import ArchidektImporter, _clean_category_name, _extract_deck_id, _parse_archidekt_deck
from models import CardRole, MAYBEBOARD


# ── _extract_deck_id ──────────────────────────────────────────────────────────

class TestExtractDeckId:
    def test_full_url(self):
        assert _extract_deck_id("https://archidekt.com/decks/12345") == "12345"

    def test_full_url_with_slug(self):
        assert _extract_deck_id("https://archidekt.com/decks/12345/my-cool-deck") == "12345"

    def test_url_without_protocol(self):
        assert _extract_deck_id("archidekt.com/decks/99999") == "99999"

    def test_bare_integer(self):
        assert _extract_deck_id("12345") == "12345"

    def test_bare_integer_with_whitespace(self):
        assert _extract_deck_id("  12345  ") == "12345"

    def test_invalid_returns_none(self):
        assert _extract_deck_id("not-a-url") is None

    def test_empty_string_returns_none(self):
        assert _extract_deck_id("") is None


# ── _clean_category_name ──────────────────────────────────────────────────────

class TestCleanCategoryName:
    def test_no_suffix(self):
        assert _clean_category_name("Ramp") == "Ramp"

    def test_plural_cards_suffix(self):
        assert _clean_category_name("Creatures (28 Cards)") == "Creatures"

    def test_singular_card_suffix(self):
        assert _clean_category_name("Commander (1 Card)") == "Commander"

    def test_mixed_case_suffix(self):
        assert _clean_category_name("Lands (37 CARDS)") == "Lands"

    def test_trailing_whitespace_stripped(self):
        assert _clean_category_name("Draw  ") == "Draw"


# ── _parse_archidekt_deck ─────────────────────────────────────────────────────

def _make_api_response(cards: list[dict], categories: list[dict] | None = None) -> dict:
    """Minimal Archidekt API response fixture."""
    return {
        "name": "Test Deck",
        "categories": categories or [
            {"name": "Commander", "isPremier": True,  "includedInDeck": True},
            {"name": "Mainboard", "isPremier": False, "includedInDeck": True},
            {"name": "Maybeboard","isPremier": False, "includedInDeck": False},
        ],
        "cards": cards,
    }


def _make_card_entry(name: str, categories: list[str], quantity: int = 1,
                     set_code: str = "lea", collector: str = "1",
                     modifier: str = "Normal") -> dict:
    return {
        "quantity": quantity,
        "categories": categories,
        "modifier": modifier,
        "card": {
            "collectorNumber": collector,
            "modifier": modifier,
            "edition": {"editioncode": set_code},
            "oracleCard": {"name": name},
        },
    }


class TestParseArchidektDeck(object):
    def test_deck_name(self, db):
        data = _make_api_response([])
        deck, _ = _parse_archidekt_deck(data, db)
        assert deck.name == "Test Deck"

    def test_commander_identified(self, db):
        data = _make_api_response([
            _make_card_entry("Sol Ring", ["Mainboard"]),
            _make_card_entry("Atraxa, Praetors' Voice", ["Commander", "Mainboard"]),
        ])
        deck, skipped = _parse_archidekt_deck(data, db)
        assert deck.commander is not None
        assert deck.commander.card.name == "Atraxa, Praetors' Voice"
        assert deck.commander.role == CardRole.COMMANDER
        assert "Sol Ring" not in [e.card.name for e in skipped or []]

    def test_partner_commanders(self, db):
        data = _make_api_response([
            _make_card_entry("Tymna the Weaver", ["Commander"]),
            _make_card_entry("Thrasios, Triton Hero", ["Commander"]),
        ])
        deck, _ = _parse_archidekt_deck(data, db)
        assert deck.commander is not None
        assert deck.partner is not None
        assert deck.commander.role == CardRole.COMMANDER
        assert deck.partner.role == CardRole.PARTNER

    def test_maybeboard_card(self, db):
        data = _make_api_response([
            _make_card_entry("Sol Ring", ["Maybeboard"]),
        ])
        deck, _ = _parse_archidekt_deck(data, db)
        # Commander and partner are none; sol ring is in entries as maybeboard
        sol = deck.entries.get(next(iter(deck.entries)))
        assert sol is not None
        assert MAYBEBOARD in sol.groups

    def test_unknown_card_skipped(self, db):
        data = _make_api_response([
            _make_card_entry("Totally Fake Card XYZZY", ["Mainboard"]),
        ])
        deck, skipped = _parse_archidekt_deck(data, db)
        assert "Totally Fake Card XYZZY" in skipped
        assert len(deck.entries) == 0

    def test_category_name_cleaned(self, db):
        data = _make_api_response([
            _make_card_entry("Sol Ring", ["Rocks (10 Cards)"]),
        ])
        deck, _ = _parse_archidekt_deck(data, db)
        entry = deck.entries.get(next(iter(deck.entries)))
        assert entry is not None
        assert "Rocks" in entry.groups
        assert any(g.name == "Rocks" for g in deck.groups)

    def test_group_objects_created(self, db):
        data = _make_api_response([
            _make_card_entry("Sol Ring", ["Ramp"]),
            _make_card_entry("Swamp", ["Lands"]),
        ])
        deck, _ = _parse_archidekt_deck(data, db)
        group_names = {g.name for g in deck.groups}
        assert "Ramp" in group_names
        assert "Lands" in group_names

    def test_commander_not_in_entries(self, db):
        data = _make_api_response([
            _make_card_entry("Atraxa, Praetors' Voice", ["Commander"]),
        ])
        deck, _ = _parse_archidekt_deck(data, db)
        assert len(deck.entries) == 0
        assert deck.commander is not None


# ── ArchidektImporter.can_handle ──────────────────────────────────────────────

class TestArchidektImporterCanHandle:
    def setup_method(self):
        self.importer = ArchidektImporter()

    def test_full_archidekt_url(self):
        assert self.importer.can_handle("https://archidekt.com/decks/12345") is True

    def test_archidekt_url_with_slug(self):
        assert self.importer.can_handle("https://archidekt.com/decks/12345/my-deck") is True

    def test_bare_integer(self):
        assert self.importer.can_handle("12345") is False

    def test_bare_integer_with_whitespace(self):
        assert self.importer.can_handle("  99999  ") is False

    def test_non_archidekt_url_returns_false(self):
        assert self.importer.can_handle("https://moxfield.com/decks/abc") is False

    def test_non_numeric_bare_string_returns_false(self):
        assert self.importer.can_handle("not-a-url") is False

    def test_empty_string_returns_false(self):
        assert self.importer.can_handle("") is False
