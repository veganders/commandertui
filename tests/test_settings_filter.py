"""Tests for settings-based card pool filtering.

Pure-logic tests: no DB fixture required, runs in ~0.1 s.
"""

import json
from pathlib import Path

import pytest

from db import Card, CardFace, _card_passes_filter
from scryfall import _is_commander_candidate, _strip_card
from settings import Settings


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_card(commander_legal: str = "legal", released_at: str = "2020-01-01") -> Card:
    face = CardFace(name="Test", mana_cost="{1}", oracle_text="", type_line="instant")
    return Card(
        oracle_id="test-id",
        name="Test Card",
        cmc=1.0,
        colors=[],
        color_identity=[],
        keywords=[],
        rarity="common",
        layout="normal",
        commander_legal=commander_legal,
        released_at=released_at,
        faces=[face],
    )


# ── _card_passes_filter ───────────────────────────────────────────────────────

class TestCardPassesFilter:
    TODAY = "2030-01-01"  # fixed reference date; all test cards are in the past

    def _pass(self, card: Card, **overrides) -> bool:
        s = Settings(**overrides)
        return _card_passes_filter(card, s, self.TODAY)

    def test_legal_card_shown_when_show_legal_true(self):
        assert self._pass(_make_card("legal"), show_legal=True)

    def test_legal_card_hidden_when_show_legal_false(self):
        assert not self._pass(_make_card("legal"), show_legal=False)

    def test_banned_card_shown_when_show_banned_true(self):
        assert self._pass(_make_card("banned"), show_legal=True, show_banned=True)

    def test_banned_card_hidden_by_default(self):
        assert not self._pass(_make_card("banned"))

    def test_not_legal_card_shown_when_show_not_legal_true(self):
        assert self._pass(_make_card("not_legal"), show_legal=True, show_not_legal=True)

    def test_not_legal_card_hidden_by_default(self):
        assert not self._pass(_make_card("not_legal"))

    def test_unreleased_card_shown_when_show_unreleased_true(self):
        future = "2099-12-31"
        assert self._pass(_make_card("legal", released_at=future), show_unreleased=True)

    def test_unreleased_card_hidden_by_default(self):
        future = "2099-12-31"
        assert not self._pass(_make_card("legal", released_at=future))

    def test_unreleased_supersedes_legal_toggle(self):
        # An unreleased card is gated by show_unreleased, not show_legal.
        future = "2099-12-31"
        assert not self._pass(
            _make_card("legal", released_at=future),
            show_legal=True, show_unreleased=False,
        )
        assert self._pass(
            _make_card("legal", released_at=future),
            show_legal=False, show_unreleased=True,
        )

    def test_empty_released_at_treated_as_released(self):
        # Old data has no released_at; must not be treated as unreleased.
        assert self._pass(_make_card("legal", released_at=""), show_legal=True)
        assert not self._pass(_make_card("legal", released_at=""), show_unreleased=True, show_legal=False)

    def test_default_settings_pass_legal_only(self):
        s = Settings()
        assert _card_passes_filter(_make_card("legal"), s, self.TODAY)
        assert not _card_passes_filter(_make_card("banned"), s, self.TODAY)
        assert not _card_passes_filter(_make_card("not_legal"), s, self.TODAY)
        assert not _card_passes_filter(_make_card("legal", "2099-12-31"), s, self.TODAY)


# ── Settings serialisation ────────────────────────────────────────────────────

class TestSettingsSerialization:
    def test_new_fields_have_correct_defaults(self):
        s = Settings()
        assert s.show_legal is True
        assert s.show_not_legal is False
        assert s.show_banned is False
        assert s.show_unreleased is False

    def test_save_and_load_roundtrip(self, tmp_path, monkeypatch):
        import settings as sm
        path = tmp_path / "settings.json"
        monkeypatch.setattr(sm, "_SETTINGS_PATH", path)

        s = Settings(currency="eur", show_not_legal=True, show_banned=True)
        s.save()
        loaded = Settings.load()

        assert loaded.currency == "eur"
        assert loaded.show_not_legal is True
        assert loaded.show_banned is True
        assert loaded.show_unreleased is False

    def test_load_ignores_unknown_keys(self, tmp_path, monkeypatch):
        import settings as sm
        path = tmp_path / "settings.json"
        monkeypatch.setattr(sm, "_SETTINGS_PATH", path)
        path.write_text(json.dumps({"currency": "usd", "unknown_key": 42}))

        s = Settings.load()
        assert s.currency == "usd"
        assert not hasattr(s, "unknown_key")


# ── _is_commander_candidate (download filter) ─────────────────────────────────

class TestIsCommanderCandidate:
    def _card(self, layout: str) -> dict:
        return {"layout": layout, "legalities": {"commander": "legal"}}

    def test_normal_card_passes(self):
        assert _is_commander_candidate(self._card("normal"))

    def test_transform_card_passes(self):
        assert _is_commander_candidate(self._card("transform"))

    def test_token_excluded(self):
        assert not _is_commander_candidate(self._card("token"))

    def test_emblem_excluded(self):
        assert not _is_commander_candidate(self._card("emblem"))

    def test_scheme_excluded(self):
        assert not _is_commander_candidate(self._card("scheme"))

    def test_plane_excluded(self):
        assert not _is_commander_candidate(self._card("plane"))

    def test_not_legal_card_passes(self):
        c = {"layout": "normal", "legalities": {"commander": "not_legal"}}
        assert _is_commander_candidate(c)

    def test_banned_card_passes(self):
        c = {"layout": "normal", "legalities": {"commander": "banned"}}
        assert _is_commander_candidate(c)


# ── _strip_card extracts commander_legal ──────────────────────────────────────

class TestStripCard:
    def _raw(self, commander: str = "legal") -> dict:
        return {
            "oracle_id": "x",
            "name": "Foo",
            "layout": "normal",
            "legalities": {"commander": commander},
            "released_at": "2023-06-09",
            "cmc": 1.0,
            "colors": [],
            "color_identity": [],
            "keywords": [],
            "rarity": "common",
            "oracle_text": "",
            "type_line": "instant",
            "mana_cost": "{1}",
            "finishes": ["nonfoil"],
            "prices": {},
            "set": "m21",
            "set_name": "Core Set 2021",
            "collector_number": "1",
            "id": "abc",
        }

    def test_commander_legal_extracted(self):
        stripped = _strip_card(self._raw("legal"))
        assert stripped["commander_legal"] == "legal"

    def test_commander_banned_extracted(self):
        stripped = _strip_card(self._raw("banned"))
        assert stripped["commander_legal"] == "banned"

    def test_released_at_kept(self):
        stripped = _strip_card(self._raw())
        assert stripped["released_at"] == "2023-06-09"

    def test_legalities_dict_not_stored(self):
        stripped = _strip_card(self._raw())
        assert "legalities" not in stripped
