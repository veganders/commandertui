"""Tests for _filter_token_context in widgets.py."""

from widgets import _filter_token_context

PREFIXES = frozenset(["otag:", "t:", "kw:"])


def ctx(s):
    """Call _filter_token_context with cursor at end of string."""
    return _filter_token_context(s, len(s), PREFIXES)


class TestFilterTokenContext:
    def test_plain_prefix(self):
        result = ctx("otag:ramp")
        assert result is not None
        _, _, partial, prefix = result
        assert prefix == "otag:" and partial == "ramp"

    def test_after_space(self):
        result = ctx("sol otag:ramp")
        assert result is not None
        _, _, partial, prefix = result
        assert prefix == "otag:" and partial == "ramp"

    def test_after_open_paren(self):
        result = ctx("(otag:ramp")
        assert result is not None
        _, _, partial, prefix = result
        assert prefix == "otag:"

    def test_after_open_bracket(self):
        # cursor is inside [otag:ramp — [ must be treated as a boundary
        result = ctx("[otag:ramp")
        assert result is not None, "[ should be a token boundary"
        _, _, partial, prefix = result
        assert prefix == "otag:" and partial == "ramp"

    def test_after_comma_no_space(self):
        # [otag:ramp,otag:draw — comma directly before prefix, no space
        result = ctx("[otag:ramp,otag:draw")
        assert result is not None, ", should be a token boundary"
        _, _, partial, prefix = result
        assert prefix == "otag:" and partial == "draw"

    def test_after_comma_with_space(self):
        # [otag:ramp, otag:draw — space after comma, already worked
        result = ctx("[otag:ramp, otag:draw")
        assert result is not None
        _, _, partial, prefix = result
        assert prefix == "otag:" and partial == "draw"

    def test_negation_inside_bracket(self):
        result = ctx("[-otag:ramp")
        assert result is not None
        _, _, partial, prefix = result
        assert prefix == "otag:" and partial == "ramp"

    def test_no_match_outside_prefix(self):
        assert ctx("sol ring") is None

    def test_complete_quoted_token_returns_none(self):
        # closing quote present → token is already complete
        assert ctx('otag:"mana rock"') is None
