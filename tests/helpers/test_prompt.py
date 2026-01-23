"""
Test cases for the GlobTool.
"""

import pytest

from wichy.helpers.prompt import preprocess_prompt


def test_basic_prompt():
    """Test a basic prompt with no conditions"""
    prompt = "This is my prompt."
    result = preprocess_prompt(prompt=prompt, verify_against={})
    assert result == prompt


def test_single_condition_prompt():
    """Test a prompt containing a single conditional."""
    prompt = """Hello world!\n<conditional>\n<condition>\n<tool>my_tool</tool>\n</condition>my_tool is here!</conditional>"""
    correct = """Hello world!\nmy_tool is here!"""

    result = preprocess_prompt(prompt=prompt, verify_against={"tool": ["my_tool"]})

    assert result == correct


def test_single_conditional_condition_no_match_prompt():
    """Test a prompt containing a single conditional. However, the condition is not met."""
    prompt = """Hello world!\n<conditional><condition><tool>my_tool</tool></condition>my_tool is here!</conditional>"""
    correct = """Hello world!\n"""

    result = preprocess_prompt(prompt=prompt, verify_against={})

    assert result == correct


def test_multi_conditionals_prompt():
    prompt = """Hello world!\n<conditional><condition><tool>my_tool</tool></condition>my_tool is here!</conditional>\n\n<conditional><condition><tool>my_other_tool</tool></condition>my_other_tool is here!</conditional>"""
    correct = """Hello world!\nmy_tool is here!\nmy_other_tool is here!"""

    result = preprocess_prompt(
        prompt=prompt, verify_against={"tool": ["my_tool", "my_other_tool"]}
    )

    assert result == correct


def test_partial_match_multiple_tools():
    """Test multiple tools in verify_against with only partial matches."""
    prompt = """Hello world!
<conditional><condition><tool>tool_a</tool></condition>Tool A is here!</conditional>

<conditional><condition><tool>tool_b</tool></condition>Tool B is here!</conditional>
<conditional><condition><tool>tool_c</tool></condition>Tool C is here!</conditional>"""
    correct = """Hello world!
Tool A is here!
Tool C is here!"""

    result = preprocess_prompt(
        prompt=prompt, verify_against={"tool": ["tool_a", "tool_c"]}
    )
    assert result == correct


def test_empty_verify_against_with_conditionals():
    """Test conditional blocks when verify_against is empty."""
    prompt = """Hello world!
<conditional><condition><tool>any_tool</tool></condition>This should not appear.</conditional>
More text here."""
    correct = """Hello world!
More text here."""

    result = preprocess_prompt(prompt=prompt, verify_against={})
    assert result == correct


def test_conditional_without_tool_specified_error():
    """Test that conditional block without tool specified raises appropriate error."""
    prompt = """Hello world!
<conditional><condition></condition>This conditional has no tool specified.</conditional>"""

    # Test that the function raises an exception when no tool is specified
    with pytest.raises(Exception) as excinfo:
        preprocess_prompt(prompt=prompt, verify_against={"tool": ["some_tool"]})

    # Verify the type and message of the exception
    assert "condition block is empty" in str(excinfo.value)
    # You can also check for specific exception type if needed
    # assert isinstance(excinfo.value, ValueError)


def test_malformed_conditional_syntax_error():
    """Test that malformed conditional syntax raises appropriate error."""
    prompt = """Hello world!
<conditional>This is malformed - no condition tag.
<conditional><condition><tool>my_tool</tool></condition>This is proper.</conditional>"""

    # Test that the function raises an exception for malformed syntax
    with pytest.raises(Exception) as excinfo:
        preprocess_prompt(prompt=prompt, verify_against={"tool": ["my_tool"]})

    # Verify the type and message of the exception
    assert "count mismatch for" in str(excinfo.value)


def test_tag_plural_name():
    prompt = """Hello world!\n<conditional><condition><tool>my_tool</tool></condition>my_tool is here!</conditional>\n\n<conditional><condition><tool>my_other_tool</tool></condition>my_other_tool is here!</conditional>"""
    correct = """Hello world!\nmy_tool is here!\nmy_other_tool is here!"""

    result = preprocess_prompt(
        prompt=prompt, verify_against={"tools": ["my_tool", "my_other_tool"]}
    )

    assert result == correct
