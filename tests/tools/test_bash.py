"""
Test cases for the BashTool.
"""

import pytest

from wichy.config.settings import settings
from wichy.tools.bash import BashTool


@pytest.fixture
def bash_tool(monkeypatch):
    """Fixture to create a fresh BashTool instance for each test."""
    # Monkey patch away the need for human verification
    monkeypatch.setattr(settings, "skip_human_verification", True)
    return BashTool()


def test_create_task(bash_tool):
    """Test known problematic command"""
    result = bash_tool.execute(command='find . -name "*test*" | grep bash', timeout=30)
    assert result.strip() != ""
    assert "find: |: unknown primary or operator" not in result
    assert "test_bash.py" in result
