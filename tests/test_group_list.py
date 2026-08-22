"""Tests for GroupList — the sorted Group container in models.py."""

import pytest
from models import Deck, Group, GroupList, MAYBEBOARD


def g(name: str, permanent: bool = False) -> Group:
    return Group(name=name, permanent=permanent)


# ── construction ──────────────────────────────────────────────────────────────

class TestConstruction:
    def test_empty(self):
        gl = GroupList()
        assert list(gl) == []

    def test_already_sorted_input_stays_sorted(self):
        gl = GroupList([g("Alpha"), g("Beta"), g("Gamma")])
        assert [x.name for x in gl] == ["Alpha", "Beta", "Gamma"]

    def test_unsorted_input_is_sorted(self):
        gl = GroupList([g("Gamma"), g("Alpha"), g("Beta")])
        assert [x.name for x in gl] == ["Alpha", "Beta", "Gamma"]

    def test_case_insensitive_sort(self):
        gl = GroupList([g("ramp"), g("Draw"), g("Interaction")])
        assert [x.name for x in gl] == ["Draw", "Interaction", "ramp"]

    def test_maybeboard_last_in_construction(self):
        gl = GroupList([g(MAYBEBOARD), g("Alpha"), g("Ramp")])
        assert gl[-1].name == MAYBEBOARD
        assert [x.name for x in gl] == ["Alpha", "Ramp", MAYBEBOARD]

    def test_maybeboard_last_even_when_alphabetically_earlier(self):
        # "Maybeboard" sorts before "Ramp" and "Zzz" alphabetically,
        # but must always be last.
        gl = GroupList([g("Zzz"), g(MAYBEBOARD), g("Aaa")])
        assert gl[-1].name == MAYBEBOARD


# ── append ────────────────────────────────────────────────────────────────────

class TestAppend:
    def test_append_to_empty(self):
        gl = GroupList()
        gl.append(g("Ramp"))
        assert [x.name for x in gl] == ["Ramp"]

    def test_append_inserts_in_sorted_position(self):
        gl = GroupList([g("Alpha"), g("Gamma")])
        gl.append(g("Beta"))
        assert [x.name for x in gl] == ["Alpha", "Beta", "Gamma"]

    def test_append_at_front(self):
        gl = GroupList([g("Beta"), g("Gamma")])
        gl.append(g("Alpha"))
        assert gl[0].name == "Alpha"

    def test_append_at_end(self):
        gl = GroupList([g("Alpha"), g("Beta")])
        gl.append(g("Gamma"))
        assert gl[-1].name == "Gamma"

    def test_append_maybeboard_always_last(self):
        gl = GroupList([g("Alpha"), g("Beta")])
        gl.append(g(MAYBEBOARD))
        assert gl[-1].name == MAYBEBOARD

    def test_append_after_maybeboard_stays_before_it(self):
        gl = GroupList([g("Alpha"), g(MAYBEBOARD)])
        gl.append(g("Zeta"))
        assert [x.name for x in gl] == ["Alpha", "Zeta", MAYBEBOARD]

    def test_append_case_insensitive_position(self):
        gl = GroupList([g("draw"), g("Ramp")])
        gl.append(g("Interaction"))
        assert [x.name for x in gl] == ["draw", "Interaction", "Ramp"]


# ── remove ────────────────────────────────────────────────────────────────────

class TestRemove:
    def test_remove_middle(self):
        gl = GroupList([g("Alpha"), g("Beta"), g("Gamma")])
        gl.remove(gl[1])
        assert [x.name for x in gl] == ["Alpha", "Gamma"]

    def test_remove_only_element(self):
        group = g("Solo")
        gl = GroupList([group])
        gl.remove(group)
        assert len(gl) == 0

    def test_remove_maybeboard(self):
        mb = g(MAYBEBOARD)
        gl = GroupList([g("Alpha"), mb])
        gl.remove(mb)
        assert [x.name for x in gl] == ["Alpha"]


# ── interface ─────────────────────────────────────────────────────────────────

class TestInterface:
    def test_len(self):
        gl = GroupList([g("A"), g("B"), g("C")])
        assert len(gl) == 3

    def test_getitem(self):
        gl = GroupList([g("Alpha"), g("Beta")])
        assert gl[0].name == "Alpha"
        assert gl[1].name == "Beta"

    def test_negative_index(self):
        gl = GroupList([g("Alpha"), g("Beta"), g(MAYBEBOARD)])
        assert gl[-1].name == MAYBEBOARD

    def test_iter(self):
        names = ["Alpha", "Beta", "Gamma"]
        gl = GroupList([g(n) for n in names])
        assert [x.name for x in gl] == names

    def test_bool_empty(self):
        assert not GroupList()

    def test_bool_nonempty(self):
        assert GroupList([g("X")])


# ── Deck integration ──────────────────────────────────────────────────────────

class TestDeckIntegration:
    def test_deck_groups_is_group_list(self):
        deck = Deck()
        assert isinstance(deck.groups, GroupList)

    def test_deck_post_init_wraps_plain_list(self):
        """Deck(groups=[...]) wraps the plain list automatically."""
        deck = Deck(groups=[g("Ramp"), g("Alpha")])
        assert isinstance(deck.groups, GroupList)
        assert [x.name for x in deck.groups] == ["Alpha", "Ramp"]

    def test_deck_append_maintains_order(self):
        deck = Deck()
        deck.groups.append(g("Ramp"))
        deck.groups.append(g("Alpha"))
        deck.groups.append(g(MAYBEBOARD))
        deck.groups.append(g("Draw"))
        assert [x.name for x in deck.groups] == ["Alpha", "Draw", "Ramp", MAYBEBOARD]
