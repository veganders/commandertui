"""Tests for the query parser, tokenizer, and validator in db.py.

These tests cover only pure parsing/AST logic and never load the database,
so they run instantly and are safe to run on every change.
"""

import pytest
from db import (
    And, Atom, AtLeastN, Not, Or,
    _parse_colon_atom, _parse_threshold, _scan_bracket_token,
    _skip_delimited, _split_threshold_parts, _tokenize,
    parse_query, validate_query,
)


# ── _scan_bracket_token ──────────────────────────────────────────────────────

class TestScanBracketToken:
    def _scan(self, s):
        return _scan_bracket_token(s, 0, len(s))

    def test_simple_no_suffix(self):
        s = "[otag:ramp, otag:draw]"
        assert self._scan(s) == len(s)

    def test_with_n_suffix(self):
        s = "[otag:ramp, otag:draw]{2}"
        assert self._scan(s) == len(s)

    def test_nested_brackets(self):
        s = "[[a, b]{1}, c]{2}"
        assert self._scan(s) == len(s)

    def test_quoted_inner_does_not_confuse_depth(self):
        s = '[o:"[not a bracket]", otag:ramp]{1}'
        assert self._scan(s) == len(s)

    def test_unclosed_bracket_reaches_end(self):
        s = "[otag:ramp"
        assert self._scan(s) == len(s)


# ── _parse_threshold ─────────────────────────────────────────────────────────

class TestParseThreshold:
    def test_basic(self):
        node = _parse_threshold("[otag:ramp, otag:draw]{2}")
        assert isinstance(node, AtLeastN)
        assert node.n == 2
        assert len(node.children) == 2

    def test_default_n_is_1(self):
        node = _parse_threshold("[otag:ramp, otag:draw]")
        assert node.n == 1

    def test_unclosed_bracket_falls_back(self):
        # unclosed — should not raise
        node = _parse_threshold("[otag:ramp")
        assert isinstance(node, AtLeastN)

    def test_invalid_n_falls_back_to_1(self):
        node = _parse_threshold("[otag:ramp]{abc}")
        assert node.n == 1


# ── _parse_colon_atom ────────────────────────────────────────────────────────

class TestParseColonAtom:
    def test_plain_value(self):
        assert _parse_colon_atom("t", "creature") == Atom(key="t", value="creature")

    def test_key_is_lowercased(self):
        assert _parse_colon_atom("T", "creature") == Atom(key="t", value="creature")

    def test_quoted_value_strips_quotes(self):
        atom = _parse_colon_atom("o", '"draw a card"')
        assert atom.value == "draw a card"

    def test_quoted_value_unescapes_inner_quote(self):
        atom = _parse_colon_atom("o", '"he said \\"hi\\""')
        assert atom.value == 'he said "hi"'

    def test_regex_value_kept_with_slashes(self):
        atom = _parse_colon_atom("o", "/draw|scry/")
        assert atom.value == "/draw|scry/"

    def test_regex_unescapes_slash(self):
        atom = _parse_colon_atom("o", "/a\\/b/")
        assert atom.value == "/a/b/"


# ── _skip_delimited ──────────────────────────────────────────────────────────

class TestSkipDelimited:
    def test_simple_quote(self):
        s = '"hello" rest'
        assert _skip_delimited(s, 0, '"') == 7  # lands after closing "

    def test_escaped_delimiter_not_a_closer(self):
        s = '"he\\"llo" rest'
        assert _skip_delimited(s, 0, '"') == 9

    def test_unclosed_span_returns_end(self):
        s = '"unclosed'
        assert _skip_delimited(s, 0, '"') == len(s)

    def test_regex_delimiter(self):
        s = '/pat\\/tern/ rest'
        assert _skip_delimited(s, 0, '/') == 11


# ── _split_threshold_parts ───────────────────────────────────────────────────

class TestSplitThresholdParts:
    def test_simple(self):
        assert _split_threshold_parts("otag:ramp, otag:draw") == ["otag:ramp", " otag:draw"]

    def test_comma_inside_quotes_not_a_separator(self):
        parts = _split_threshold_parts('o:"draw, deal", otag:ramp')
        assert len(parts) == 2
        assert parts[0] == 'o:"draw, deal"'
        assert parts[1].strip() == "otag:ramp"

    def test_comma_inside_nested_brackets_not_a_separator(self):
        parts = _split_threshold_parts("[a, b]{1}, c")
        assert len(parts) == 2
        assert parts[0] == "[a, b]{1}"
        assert parts[1].strip() == "c"

    def test_single_part_no_trailing_empty(self):
        # no comma → one part containing the whole string
        assert _split_threshold_parts("otag:ramp") == ["otag:ramp"]


# ── _tokenize ────────────────────────────────────────────────────────────────

class TestTokenize:
    def test_simple_words(self):
        assert _tokenize("sol ring") == ["sol", "ring"]

    def test_parens(self):
        assert _tokenize("(a OR b)") == ["(", "a", "OR", "b", ")"]

    def test_quoted_string_with_space(self):
        assert _tokenize('o:"draw a card"') == ['o:"draw a card"']

    def test_quoted_string_with_escaped_quote(self):
        assert _tokenize('o:"he said \\"hi\\""') == ['o:"he said \\"hi\\""']

    def test_regex(self):
        assert _tokenize("o:/draw|scry/") == ["o:/draw|scry/"]

    def test_regex_with_escaped_slash(self):
        assert _tokenize("o:/a\\/b/") == ["o:/a\\/b/"]

    def test_threshold_basic(self):
        assert _tokenize("[otag:ramp, otag:draw]{2}") == ["[otag:ramp, otag:draw]{2}"]

    def test_threshold_no_n(self):
        assert _tokenize("[otag:ramp, otag:draw]") == ["[otag:ramp, otag:draw]"]

    def test_threshold_with_leading_negation(self):
        assert _tokenize("-[otag:ramp, otag:draw]{2}") == ["-[otag:ramp, otag:draw]{2}"]

    def test_threshold_with_quoted_inner(self):
        toks = _tokenize('[o:"draw, deal", otag:ramp]{1}')
        assert toks == ['[o:"draw, deal", otag:ramp]{1}']

    def test_threshold_followed_by_outer_filter(self):
        toks = _tokenize("[otag:ramp, otag:draw]{2} eur<1")
        assert toks == ["[otag:ramp, otag:draw]{2}", "eur<1"]

    def test_multiple_threshold_groups(self):
        toks = _tokenize("[otag:ramp, otag:draw]{1} [otag:lifegain, otag:counters]{1}")
        assert toks == [
            "[otag:ramp, otag:draw]{1}",
            "[otag:lifegain, otag:counters]{1}",
        ]


# ── parse_query ───────────────────────────────────────────────────────────────

class TestParseQuery:
    def test_empty_returns_and_empty(self):
        assert parse_query("") == And([])

    def test_bare_word_is_name_atom(self):
        assert parse_query("sol") == Atom(key="name", value="sol")

    def test_type_filter(self):
        assert parse_query("t:creature") == Atom(key="t", value="creature")

    def test_oracle_quoted(self):
        assert parse_query('o:"draw a card"') == Atom(key="o", value="draw a card")

    def test_oracle_regex(self):
        assert parse_query("o:/draw|scry/") == Atom(key="o", value="/draw|scry/")

    def test_negation(self):
        assert parse_query("-t:creature") == Not(Atom(key="t", value="creature"))

    def test_implicit_and(self):
        node = parse_query("t:creature otag:ramp")
        assert isinstance(node, And)
        assert len(node.children) == 2

    def test_explicit_or(self):
        node = parse_query("otag:ramp OR otag:draw")
        assert isinstance(node, Or)
        assert len(node.children) == 2

    def test_and_binds_tighter_than_or(self):
        # "a b OR c" → Or(And(a, b), c)
        node = parse_query("a b OR c")
        assert isinstance(node, Or)
        assert isinstance(node.children[0], And)

    def test_parens_override_precedence(self):
        # "a (b OR c)" → And(a, Or(b, c))
        node = parse_query("a (b OR c)")
        assert isinstance(node, And)
        assert isinstance(node.children[1], Or)

    def test_mv_comparison(self):
        assert parse_query("mv>=3") == Atom(key="mv", value=">=3")

    def test_price_comparison(self):
        assert parse_query("eur<1") == Atom(key="eur", value="<1")

    # ── AtLeastN parsing ──────────────────────────────────────────────────────

    def test_threshold_basic(self):
        node = parse_query("[otag:ramp, otag:draw]{2}")
        assert isinstance(node, AtLeastN)
        assert node.n == 2
        assert len(node.children) == 2

    def test_threshold_no_n_defaults_to_1(self):
        node = parse_query("[otag:ramp, otag:draw]")
        assert isinstance(node, AtLeastN)
        assert node.n == 1

    def test_threshold_negated(self):
        node = parse_query("-[otag:ramp, otag:draw]{1}")
        assert isinstance(node, Not)
        assert isinstance(node.child, AtLeastN)
        assert len(node.child.children) == 2

    def test_threshold_with_outer_filter(self):
        node = parse_query("[otag:ramp, otag:draw]{2} eur<1")
        assert isinstance(node, And)
        children_types = [type(c) for c in node.children]
        assert AtLeastN in children_types
        assert Atom in children_types

    def test_multiple_threshold_groups_anded(self):
        node = parse_query("[otag:ramp, otag:draw]{1} [otag:lifegain, otag:counters]{1}")
        assert isinstance(node, And)
        assert all(isinstance(c, AtLeastN) for c in node.children)

    def test_threshold_comma_inside_quoted_string(self):
        node = parse_query('[o:"draw, deal", otag:ramp]{1}')
        assert isinstance(node, AtLeastN)
        assert len(node.children) == 2

    def test_threshold_sub_query_can_use_any_syntax(self):
        # Each sub-query is parsed with the full parse_query, so negation,
        # AND/OR, comparisons all work inside brackets.
        node = parse_query("[otag:ramp, -t:creature]{1}")
        assert isinstance(node, AtLeastN)
        assert isinstance(node.children[1], Not)


# ── validate_query ────────────────────────────────────────────────────────────

class TestValidateQuery:
    def test_valid_name(self):
        assert validate_query(parse_query("sol ring"))

    def test_valid_mv(self):
        assert validate_query(parse_query("mv>=3"))

    def test_invalid_mv(self):
        assert not validate_query(parse_query("mv>=abc"))

    def test_valid_regex(self):
        assert validate_query(parse_query("o:/draw|scry/"))

    def test_invalid_regex(self):
        assert not validate_query(parse_query("o:/[INVALID(/"))

    def test_valid_threshold(self):
        assert validate_query(parse_query("[otag:ramp, otag:draw]{2}"))

    def test_threshold_n_zero_invalid(self):
        assert not validate_query(parse_query("[otag:ramp]{0}"))

    def test_threshold_n_exceeds_m_invalid(self):
        assert not validate_query(parse_query("[otag:ramp]{3}"))

    def test_threshold_n_equals_m_valid(self):
        # N == M means all must match (equivalent to AND)
        assert validate_query(parse_query("[otag:ramp, otag:draw]{2}"))

    def test_threshold_invalid_inner_atom_propagates(self):
        assert not validate_query(parse_query("[mv>=abc, otag:draw]{1}"))

    def test_threshold_empty_brackets_invalid(self):
        # [] → AtLeastN(children=[], n=1) — children empty, invalid
        node = parse_query("[]")
        assert not validate_query(node)
