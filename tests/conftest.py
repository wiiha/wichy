"""Pytest fixtures for wichy tests."""

import pytest
from wichy.tools.registry import get_registry_copy, restore_registry


@pytest.fixture
def isolated_tool_registry():
    """
    Fixture that isolates the tool registry for tests.

    Saves the registry state before the test and restores it after,
    ensuring test tools don't pollute the global registry and vice versa.
    """
    saved_state = get_registry_copy()
    yield
    restore_registry(saved_state)
