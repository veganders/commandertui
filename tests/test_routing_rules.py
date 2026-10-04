"""Tests for routing rule defaults, serialization, and route_groups logic."""

import json
import tempfile
from pathlib import Path

import pytest

from settings import RoutingRule, RuleScope, Settings, _DEFAULT_ROUTES


# ── Settings serialization ────────────────────────────────────────────────────

class TestRoutingRuleDefaults:
    def test_new_settings_has_default_routes(self):
        s = Settings()
        assert len(s.routes) == len(_DEFAULT_ROUTES)
        assert all(r.scope == RuleScope.GLOBAL for r in s.routes)

    def test_missing_routes_key_injects_defaults(self, tmp_path):
        path = tmp_path / "settings.json"
        path.write_text(json.dumps({"currency": "eur"}))
        import settings as settings_mod
        orig = settings_mod._SETTINGS_PATH
        settings_mod._SETTINGS_PATH = path
        try:
            s = Settings.load()
        finally:
            settings_mod._SETTINGS_PATH = orig
        assert len(s.routes) == len(_DEFAULT_ROUTES)

    def test_empty_routes_key_respected(self, tmp_path):
        path = tmp_path / "settings.json"
        path.write_text(json.dumps({"currency": "usd", "routes": []}))
        import settings as settings_mod
        orig = settings_mod._SETTINGS_PATH
        settings_mod._SETTINGS_PATH = path
        try:
            s = Settings.load()
        finally:
            settings_mod._SETTINGS_PATH = orig
        assert s.routes == []

    def test_roundtrip_preserves_rules(self, tmp_path):
        path = tmp_path / "settings.json"
        import settings as settings_mod
        orig = settings_mod._SETTINGS_PATH
        settings_mod._SETTINGS_PATH = path
        try:
            s = Settings()
            s.routes = [RoutingRule(query="t:land", group="Lands", enabled=False, scope=RuleScope.GLOBAL)]
            s.save()
            s2 = Settings.load()
        finally:
            settings_mod._SETTINGS_PATH = orig
        assert len(s2.routes) == 1
        assert s2.routes[0].query == "t:land"
        assert s2.routes[0].enabled is False
        assert s2.routes[0].scope == RuleScope.GLOBAL


# ── route_groups logic ────────────────────────────────────────────────────────

class TestRouteGroups:
    def test_land_routes_to_lands_group(self, db):
        from models import Deck, Group
        from search import route_groups
        lands = [c for c in db.cards.values() if c.has_type("land") and not c.has_type("creature")]
        if not lands:
            pytest.skip("no land cards in db")
        card = lands[0]
        deck = Deck()
        deck.groups.append(Group("Lands", permanent=True))
        db.settings.routes = [RoutingRule(query="t:land", group="Lands")]
        groups = route_groups(card, deck, db)
        assert any(g.name == "Lands" for g in groups)

    def test_missing_group_is_created(self, db):
        from models import Deck
        from search import route_groups
        lands = [c for c in db.cards.values() if c.has_type("land") and not c.has_type("creature")]
        if not lands:
            pytest.skip("no land cards in db")
        card = lands[0]
        deck = Deck()
        db.settings.routes = [RoutingRule(query="t:land", group="Lands")]
        route_groups(card, deck, db)
        assert any(g.name == "Lands" for g in deck.groups)

    def test_disabled_rule_not_applied(self, db):
        from models import Deck
        from search import route_groups
        lands = [c for c in db.cards.values() if c.has_type("land") and not c.has_type("creature")]
        if not lands:
            pytest.skip("no land cards in db")
        card = lands[0]
        deck = Deck()
        db.settings.routes = [RoutingRule(query="t:land", group="Lands", enabled=False)]
        groups = route_groups(card, deck, db)
        assert groups == []

    def test_local_rules_applied(self, db):
        from models import Deck
        from search import route_groups
        lands = [c for c in db.cards.values() if c.has_type("land") and not c.has_type("creature")]
        if not lands:
            pytest.skip("no land cards in db")
        card = lands[0]
        deck = Deck()
        deck.local_routes = [RoutingRule(query="t:land", group="MyLands", scope=RuleScope.LOCAL)]
        db.settings.routes = []
        groups = route_groups(card, deck, db)
        assert any(g.name == "MyLands" for g in groups)
