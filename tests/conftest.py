"""Shared pytest fixtures for the deckbuilder test suite."""

import pytest
from db import load_db


@pytest.fixture(scope="session")
def db():
    """Load the card database once for the entire test session.

    Tests that need real card data request this fixture; tests that only
    exercise parsing logic should not request it so they stay fast.
    """
    return load_db()
