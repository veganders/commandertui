"""Tests for query evaluation against real card data.

These tests request the `db` fixture from conftest.py, which loads the full
database once for the session.  They are necessarily slower than the pure
parsing tests in test_query.py, but the DB is only loaded once regardless of
how many tests in this file run.
"""

from db import parse_query


def test_threshold_n2_is_subset_of_n1(db):
    """Cards matching 2-of-2 must be a subset of cards matching 1-of-2."""
    results_n1 = set(c.oracle_id for c in db.query(parse_query("[otag:ramp, otag:draw]{1}")))
    results_n2 = set(c.oracle_id for c in db.query(parse_query("[otag:ramp, otag:draw]{2}")))
    assert results_n2.issubset(results_n1)
    assert len(results_n2) < len(results_n1)


def test_threshold_n_equals_m_same_as_and(db):
    """[q1, q2]{2} should return the same cards as q1 q2 (implicit AND)."""
    threshold = set(c.oracle_id for c in db.query(parse_query("[otag:ramp, otag:draw]{2}")))
    explicit_and = set(c.oracle_id for c in db.query(parse_query("otag:ramp otag:draw")))
    assert threshold == explicit_and


def test_threshold_n1_same_as_or(db):
    """[q1, q2]{1} should return the same cards as q1 OR q2."""
    threshold = set(c.oracle_id for c in db.query(parse_query("[otag:ramp, otag:draw]{1}")))
    explicit_or = set(c.oracle_id for c in db.query(parse_query("otag:ramp OR otag:draw")))
    assert threshold == explicit_or


def test_outer_filter_restricts_threshold_results(db):
    """Filters outside the brackets narrow the result set."""
    without = set(c.oracle_id for c in db.query(parse_query("[otag:ramp, otag:draw]{2}")))
    with_filter = set(c.oracle_id for c in db.query(parse_query("[otag:ramp, otag:draw]{2} mv<=2")))
    assert with_filter.issubset(without)


def test_negated_threshold(db):
    """Negating a threshold group excludes those cards from the result."""
    positive = set(c.oracle_id for c in db.query(parse_query("[otag:ramp, otag:draw]{2}")))
    negated = set(c.oracle_id for c in db.query(parse_query("-[otag:ramp, otag:draw]{2}")))
    assert positive.isdisjoint(negated)
