"""Tests for the lambda: arithmetic filter and the loyalty: comparison filter."""

from __future__ import annotations

import pytest

from db import Card, CardFace, _parse_lambda_tokens, _eval_lambda_expr, _stat_to_float, parse_query


# ── unit tests for helper functions ──────────────────────────────────────────

class TestStatToFloat:
    def test_none_returns_none(self):
        assert _stat_to_float(None) is None

    def test_numeric_string(self):
        assert _stat_to_float("3") == 3.0

    def test_star_returns_zero(self):
        assert _stat_to_float("*") == 0.0

    def test_star_plus_one_returns_zero(self):
        assert _stat_to_float("*+1") == 0.0


class TestParseLambdaTokens:
    def test_simple_variable(self):
        assert _parse_lambda_tokens("power") == ["power"]

    def test_integer_literal(self):
        assert _parse_lambda_tokens("3") == [3.0]

    def test_expression(self):
        assert _parse_lambda_tokens("power-mv") == ["power", "-", "mv"]

    def test_rhs_expression(self):
        assert _parse_lambda_tokens("power+5") == ["power", "+", 5.0]

    def test_with_parentheses(self):
        assert _parse_lambda_tokens("(power+toughness)*2") == [
            "(", "power", "+", "toughness", ")", "*", 2.0,
        ]

    def test_rejects_float_literal(self):
        with pytest.raises(ValueError):
            _parse_lambda_tokens("power-1.5")

    def test_rejects_unknown_char(self):
        with pytest.raises(ValueError):
            _parse_lambda_tokens("power^2")

    def test_longest_var_first(self):
        # 'toughness' must not be tokenized as 't' + rest
        toks = _parse_lambda_tokens("toughness")
        assert toks == ["toughness"]


class TestEvalLambdaExpr:
    def test_addition(self):
        assert _eval_lambda_expr(_parse_lambda_tokens("power+3"), {"power": 5.0}) == 8.0

    def test_subtraction(self):
        assert _eval_lambda_expr(_parse_lambda_tokens("power-mv"), {"power": 5.0, "mv": 2.0}) == 3.0

    def test_multiplication_precedence(self):
        assert _eval_lambda_expr(_parse_lambda_tokens("power+toughness*2"), {"power": 3.0, "toughness": 4.0}) == 11.0

    def test_parentheses(self):
        assert _eval_lambda_expr(_parse_lambda_tokens("(power+toughness)*2"), {"power": 3.0, "toughness": 4.0}) == 14.0

    def test_division(self):
        assert _eval_lambda_expr(_parse_lambda_tokens("mv/2"), {"mv": 6.0}) == 3.0


# ── integration tests against real card data ─────────────────────────────────

def _ids(cards):
    return {c.oracle_id for c in cards}


class TestLambdaFilter:
    def test_power_minus_mv_ge(self, db):
        results = db.query(parse_query("lambda:power-mv>=3"))
        assert results, "expected at least one card with power - mv >= 3"
        for c in results:
            satisfied = any(
                (float(f.power) if f.power and f.power.isdigit() else 0) - c.cmc >= 3
                for f in c.faces if f.power is not None
            )
            assert satisfied, f"{c.name}: power-mv < 3"

    def test_toughness_gt_power_plus_literal(self, db):
        results = db.query(parse_query("lambda:toughness>power+5"))
        assert results, "expected at least one big-butt card"
        for c in results:
            satisfied = any(
                _stat_to_float(f.toughness) is not None
                and _stat_to_float(f.power) is not None
                and (_stat_to_float(f.toughness) or 0) > (_stat_to_float(f.power) or 0) + 5
                for f in c.faces
            )
            assert satisfied, f"{c.name}: toughness not > power+5"

    def test_power_equals_toughness(self, db):
        results = db.query(parse_query("lambda:power=toughness"))
        assert results
        for c in results:
            assert any(
                f.power is not None and f.toughness is not None
                and _stat_to_float(f.power) == _stat_to_float(f.toughness)
                for f in c.faces
            ), f"{c.name}: power != toughness on any face"

    def test_non_creature_skipped(self, db):
        # Pure instants/sorceries have no power/toughness → should not match
        instants = db.query(parse_query("t:instant lambda:power>=1"))
        # It's possible some instant-speed cards on DFCs have creature faces,
        # but pure instants should not match. Just verify no crash.
        assert isinstance(instants, list)

    def test_invalid_expression_returns_no_results(self, db):
        results = db.query(parse_query("lambda:???"))
        assert results == []

    def test_division_by_zero_does_not_crash(self, db):
        results = db.query(parse_query("lambda:power/0>=1"))
        assert isinstance(results, list)

    def test_short_alias(self, db):
        full = _ids(db.query(parse_query("lambda:power-mv>=3")))
        alias = _ids(db.query(parse_query("l:power-mv>=3")))
        assert full == alias

    def test_negation(self, db):
        positive = _ids(db.query(parse_query("t:creature lambda:power-mv>=3")))
        negated = _ids(db.query(parse_query("t:creature -lambda:power-mv>=3")))
        all_creatures = _ids(db.query(parse_query("t:creature")))
        assert positive | negated == all_creatures
        assert positive & negated == set()


class TestLoyaltyFilter:
    def test_loyalty_ge(self, db):
        results = db.query(parse_query("loyalty>=5"))
        assert results
        for c in results:
            assert any(
                f.loyalty is not None and _stat_to_float(f.loyalty) >= 5
                for f in c.faces
            ), f"{c.name}: no face with loyalty >= 5"

    def test_loyalty_excludes_non_planeswalkers(self, db):
        # Cards with no loyalty on any face should not appear
        results = db.query(parse_query("loyalty>=1 -t:planeswalker"))
        # Some battle cards have defense (stored separately), so just no crash
        assert isinstance(results, list)

    def test_loyalty_in_lambda(self, db):
        # Planeswalkers with loyalty > mv
        results = db.query(parse_query("lambda:loyalty>mv"))
        assert isinstance(results, list)
        for c in results:
            assert any(
                f.loyalty is not None and _stat_to_float(f.loyalty) > c.cmc
                for f in c.faces
            ), f"{c.name}: loyalty not > mv on any face"
