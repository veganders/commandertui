"""Tests for the dirty flag: guard logic, save/load clearing, mutation marking."""

from __future__ import annotations

import json
from contextlib import contextmanager
from typing import Generator
from unittest.mock import MagicMock, patch, PropertyMock
import pytest

from app import MainScreen
from models import Deck, Group, MAYBEBOARD
from widgets import UnsavedChoice


# ── helpers ───────────────────────────────────────────────────────────────────

@contextmanager
def _bare_screen(dirty: bool = False) -> Generator[tuple[MainScreen, MagicMock], None, None]:
    """Yield (screen, mock_app) with Textual __init__ and app property bypassed."""
    mock_app = MagicMock()
    with patch.object(MainScreen, "app", new_callable=PropertyMock, return_value=mock_app):
        screen = object.__new__(MainScreen)
        screen._deck = Deck()
        screen._deck.dirty = dirty
        yield screen, mock_app


# ── _guard_unsaved ────────────────────────────────────────────────────────────

class TestGuardUnsaved:
    def test_proceeds_immediately_when_clean(self):
        with _bare_screen(dirty=False) as (screen, mock_app):
            called = []
            screen._guard_unsaved(lambda: called.append(True))
            assert called == [True]
            mock_app.push_screen.assert_not_called()

    def test_does_not_proceed_immediately_when_dirty(self):
        with _bare_screen(dirty=True) as (screen, mock_app):
            called = []
            screen._guard_unsaved(lambda: called.append(True))
            assert called == []

    def test_opens_modal_when_dirty(self):
        with _bare_screen(dirty=True) as (screen, mock_app):
            screen._guard_unsaved(lambda: None)
            mock_app.push_screen.assert_called_once()
            from widgets import UnsavedChangesModal
            modal_arg = mock_app.push_screen.call_args[0][0]
            assert isinstance(modal_arg, UnsavedChangesModal)

    def test_proceeds_after_discard(self):
        with _bare_screen(dirty=True) as (screen, mock_app):
            called = []

            def fake_push(modal, callback):
                callback(UnsavedChoice.DISCARD)

            mock_app.push_screen.side_effect = fake_push
            screen._guard_unsaved(lambda: called.append(True))
            assert called == [True]

    def test_does_not_proceed_on_cancel(self):
        with _bare_screen(dirty=True) as (screen, mock_app):
            called = []

            def fake_push(modal, callback):
                callback(UnsavedChoice.CANCEL)

            mock_app.push_screen.side_effect = fake_push
            screen._guard_unsaved(lambda: called.append(True))
            assert called == []


# ── dirty flag on Deck ────────────────────────────────────────────────────────

class TestDirtyOnDeck:
    def test_fresh_deck_is_clean(self):
        assert Deck().dirty is False

    def test_add_marks_dirty(self, db):
        from db import Card
        deck = Deck()
        card = next(iter(db.cards.values()))
        deck.add(card)
        assert deck.dirty is True

    def test_remove_one_marks_dirty(self, db):
        deck = Deck()
        card = next(iter(db.cards.values()))
        deck.add(card)
        deck.dirty = False
        deck.remove_one(card.oracle_id)
        assert deck.dirty is True

    def test_remove_all_marks_dirty(self, db):
        deck = Deck()
        card = next(iter(db.cards.values()))
        deck.add(card)
        deck.dirty = False
        deck.remove_all(card.oracle_id)
        assert deck.dirty is True

    def test_set_commander_marks_dirty(self, db):
        from models import CardEntry, CardRole
        deck = Deck()
        card = next(iter(db.cards.values()))
        deck.set_commander(CardEntry(card=card, role=CardRole.COMMANDER))
        assert deck.dirty is True

    def test_set_partner_marks_dirty(self, db):
        from models import CardEntry, CardRole
        deck = Deck()
        card = next(iter(db.cards.values()))
        deck.set_partner(CardEntry(card=card, role=CardRole.PARTNER))
        assert deck.dirty is True

    def test_add_group_marks_dirty(self):
        deck = Deck()
        deck.add_group("Ramp")
        assert deck.dirty is True

    def test_entry_join_group_marks_dirty(self, db):
        deck = Deck()
        card = next(iter(db.cards.values()))
        deck.add(card)
        deck.dirty = False
        deck.entry_join_group(card.oracle_id, "Ramp")
        assert deck.dirty is True

    def test_set_entry_printing_marks_dirty(self, db):
        deck = Deck()
        card = next(c for c in db.cards.values() if c.printings)
        deck.add(card)
        deck.dirty = False
        deck.set_entry_printing(card.oracle_id, 0)
        assert deck.dirty is True

    def test_set_entry_printing_non_deck_card_does_not_mark_dirty(self, db):
        deck = Deck()
        card = next(iter(db.cards.values()))
        deck.set_entry_printing(card.oracle_id, 0)  # card not in deck → selected_printings cache
        assert deck.dirty is False


# ── _mark_dirty / _clear_dirty ────────────────────────────────────────────────

class TestDirtyHelpers:
    def test_mark_dirty_sets_deck_dirty(self):
        with _bare_screen() as (screen, _):
            screen._mark_dirty()
            assert screen._deck.dirty is True

    def test_clear_dirty_clears_deck_dirty(self):
        with _bare_screen(dirty=True) as (screen, _):
            screen._clear_dirty()
            assert screen._deck.dirty is False


# ── save clears dirty ─────────────────────────────────────────────────────────

class TestSaveClearsDirty:
    def test_action_save_deck_named_clears_dirty(self, tmp_path):
        with _bare_screen(dirty=True) as (screen, _):
            screen._deck.name = "my_deck"
            screen.notify = MagicMock()
            with patch("app.save_deck", return_value=tmp_path / "my_deck.json"):
                screen.action_save_deck()
            assert screen._deck.dirty is False

    def test_action_save_deck_as_clears_dirty(self, tmp_path):
        with _bare_screen(dirty=True) as (screen, mock_app):
            screen._deck.name = "old name"
            screen.notify = MagicMock()
            screen.query_one = MagicMock()

            def fake_push(modal, callback):
                callback("new name")

            mock_app.push_screen.side_effect = fake_push
            with patch("app.save_deck", return_value=tmp_path / "new_name.json"):
                screen.action_save_deck_as()
            assert screen._deck.dirty is False


# ── load clears dirty ─────────────────────────────────────────────────────────

class TestLoadClearsDirty:
    def test_open_deck_clears_dirty(self, tmp_path):
        """Opening a deck (after discarding unsaved changes) clears the dirty flag."""
        from widgets import UnsavedChangesModal

        deck_path = tmp_path / "loaded_deck.json"
        deck_path.write_text(json.dumps({
            "name": "loaded_deck", "groups": [],
            "commander": None, "partner": None, "cards": [],
        }))

        with _bare_screen(dirty=True) as (screen, mock_app):
            screen._db = MagicMock()
            screen._rebuild_tree = MagicMock()
            screen.notify = MagicMock()
            screen.query_one = MagicMock()

            def fake_push(modal_or_screen, callback):
                if isinstance(modal_or_screen, UnsavedChangesModal):
                    callback(UnsavedChoice.DISCARD)
                else:
                    callback(deck_path)  # select a deck to open

            mock_app.push_screen.side_effect = fake_push

            with patch("app.load_deck", return_value=Deck(name="loaded_deck")), \
                 patch("app.list_decks", return_value=[deck_path]), \
                 patch("app._ensure_permanent_groups"):
                screen.action_open_deck()

            assert screen._deck.dirty is False
