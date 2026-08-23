"""Tests for the `is:` shorthand search filter in db.py.

Pure-logic tests — no database fixture needed.
"""

import pytest
from db import Card, CardFace, _IS_PREDICATES, _eval_atom, Atom, parse_query, validate_query

def _valid(query: str) -> bool:
    return validate_query(parse_query(query))


# ── helpers ───────────────────────────────────────────────────────────────────

def _face(type_line: str = "", oracle_text: str = "") -> CardFace:
    return CardFace(name="Test", mana_cost="", oracle_text=oracle_text, type_line=type_line.lower())


def _card(type_line: str = "", oracle_text: str = "", game_changer: bool = False, layout: str = "normal") -> Card:
    return Card(
        oracle_id="test",
        name="Test Card",
        cmc=0.0,
        colors=[],
        color_identity=[],
        keywords=[],
        rarity="common",
        layout=layout,
        faces=[_face(type_line, oracle_text)],
        game_changer=game_changer,
    )


def _is(value: str, card: Card) -> bool:
    return _eval_atom(Atom("is", value), card, [], frozenset())


# ── is:permanent ─────────────────────────────────────────────────────────────

class TestIsPermanent:
    def test_creature(self):
        assert _is("permanent", _card("legendary creature — elf"))

    def test_artifact(self):
        assert _is("permanent", _card("artifact"))

    def test_enchantment(self):
        assert _is("permanent", _card("enchantment"))

    def test_planeswalker(self):
        assert _is("permanent", _card("legendary planeswalker — jace"))

    def test_land(self):
        assert _is("permanent", _card("basic land — forest"))

    def test_battle(self):
        assert _is("permanent", _card("battle — siege"))

    def test_instant_is_not_permanent(self):
        assert not _is("permanent", _card("instant"))

    def test_sorcery_is_not_permanent(self):
        assert not _is("permanent", _card("sorcery"))


# ── is:historic ──────────────────────────────────────────────────────────────

class TestIsHistoric:
    def test_artifact(self):
        assert _is("historic", _card("artifact"))

    def test_legendary_creature(self):
        assert _is("historic", _card("legendary creature — human"))

    def test_saga(self):
        assert _is("historic", _card("enchantment — saga"))

    def test_vanilla_creature_not_historic(self):
        assert not _is("historic", _card("creature — bear"))

    def test_instant_not_historic(self):
        assert not _is("historic", _card("instant"))


# ── is:party ─────────────────────────────────────────────────────────────────

class TestIsParty:
    def test_cleric(self):
        assert _is("party", _card("creature — human cleric"))

    def test_rogue(self):
        assert _is("party", _card("creature — elf rogue"))

    def test_warrior(self):
        assert _is("party", _card("creature — dwarf warrior"))

    def test_wizard(self):
        assert _is("party", _card("creature — human wizard"))

    def test_ranger_not_party(self):
        assert not _is("party", _card("creature — human ranger"))

    def test_druid_not_party(self):
        assert not _is("party", _card("creature — elf druid"))


# ── is:commander ─────────────────────────────────────────────────────────────

class TestIsCommander:
    def test_legendary_creature(self):
        assert _is("commander", _card("legendary creature — human wizard"))

    def test_background_enchantment(self):
        assert _is("commander", _card("enchantment — background"))

    def test_can_be_your_commander_oracle(self):
        assert _is("commander", _card("planeswalker — grist", "can be your commander"))

    def test_non_legendary_creature_not_commander(self):
        assert not _is("commander", _card("creature — human"))

    def test_non_legendary_enchantment_not_commander(self):
        assert not _is("commander", _card("enchantment — aura"))

    def test_legendary_non_creature_not_commander(self):
        # Legendary artifact without "can be your commander" text
        assert not _is("commander", _card("legendary artifact"))


# ── is:gamechanger ───────────────────────────────────────────────────────────

class TestIsGamechanger:
    def test_game_changer_true(self):
        assert _is("gamechanger", _card(game_changer=True))

    def test_game_changer_false(self):
        assert not _is("gamechanger", _card(game_changer=False))

    def test_default_is_false(self):
        assert not _is("gamechanger", _card("creature — dragon"))


# ── is:outlaw ────────────────────────────────────────────────────────────────

class TestIsOutlaw:
    def test_assassin(self):
        assert _is("outlaw", _card("creature — human assassin"))

    def test_mercenary(self):
        assert _is("outlaw", _card("creature — mercenary"))

    def test_pirate(self):
        assert _is("outlaw", _card("creature — human pirate"))

    def test_rogue(self):
        assert _is("outlaw", _card("creature — rogue"))

    def test_warlock(self):
        assert _is("outlaw", _card("creature — human warlock"))

    def test_wizard_not_outlaw(self):
        assert not _is("outlaw", _card("creature — human wizard"))

    def test_knight_not_outlaw(self):
        assert not _is("outlaw", _card("creature — human knight"))


# ── edge cases ────────────────────────────────────────────────────────────────

class TestIsEdgeCases:
    def test_unknown_predicate_returns_false(self):
        assert not _is("bogus", _card("creature"))

    def test_case_insensitive(self):
        assert _is("Permanent", _card("creature — elf"))
        assert _is("HISTORIC", _card("artifact"))
        assert _is("GameChanger", _card(game_changer=True))

    def test_negation(self):
        assert _is("permanent", _card("creature"))
        assert not _is("permanent", _card("instant"))


# ── validate_query ────────────────────────────────────────────────────────────

class TestIsValidation:
    @pytest.mark.parametrize("value", list(_IS_PREDICATES))
    def test_known_predicate_is_valid(self, value):
        assert _valid(f"is:{value}")

    def test_unknown_predicate_is_invalid(self):
        assert not _valid("is:bogus")

    def test_valid_combined_with_other_filters(self):
        assert _valid("is:permanent mv<=3")

    def test_negated_is_valid(self):
        assert _valid("-is:permanent")


# ── is:vanilla ────────────────────────────────────────────────────────────────

class TestIsVanilla:
    def test_creature_no_oracle_text(self):
        # Classic vanilla: Grizzly Bears — creature, no abilities
        assert _is("vanilla", _card("creature — bear"))

    def test_creature_with_keyword_ability(self):
        # Flying is an ability → not vanilla (reminder text accompanies it, which is fine)
        assert not _is("vanilla", _card("creature — bird", "Flying"))

    def test_creature_with_triggered_ability(self):
        assert not _is("vanilla", _card("creature — human", "When this creature enters, draw a card."))

    def test_non_creature_no_oracle_text(self):
        # Vanilla only applies to creatures
        assert not _is("vanilla", _card("artifact"))

    def test_instant_not_vanilla(self):
        assert not _is("vanilla", _card("instant"))


# ── is:split ─────────────────────────────────────────────────────────────────

class TestIsSplit:
    def test_split_layout(self):
        assert _is("split", _card(layout="split"))

    def test_normal_layout_not_split(self):
        assert not _is("split", _card(layout="normal"))

    def test_transform_layout_not_split(self):
        assert not _is("split", _card(layout="transform"))

    def test_adventure_layout_not_split(self):
        assert not _is("split", _card(layout="adventure"))


# ── is:transform ─────────────────────────────────────────────────────────────

class TestIsTransform:
    def test_transform_layout(self):
        assert _is("transform", _card(layout="transform"))

    def test_normal_layout_not_transform(self):
        assert not _is("transform", _card(layout="normal"))

    def test_split_layout_not_transform(self):
        assert not _is("transform", _card(layout="split"))

    def test_modal_dfc_not_transform(self):
        assert not _is("transform", _card(layout="modal_dfc"))
