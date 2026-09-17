"""UI escape helpers must escape double and single quotes: kill-card
interpolations (tool_call_id from the LLM) land inside attribute
contexts like data-id="...". Quote-escaping is verified against the
shipped source so a JS regression fails the suite."""


def test_chat_html_escape_helper_covers_quotes():
    src = open("src/wichy/templates/chat.html").read()
    helper = src.split("function _escapeHtml", 1)[1].split("}", 1)[0]
    assert "&quot;" in helper, "chat.html _escapeHtml does not escape double quotes"
    assert "&#39;" in helper, "chat.html _escapeHtml does not escape single quotes"


def test_context_editor_escape_helper_covers_quotes():
    src = open("src/wichy/tools/context_editor/static/context_editor.js").read()
    helper = src.split("function escapeHtml", 1)[1].split("}", 1)[0]
    assert "&quot;" in helper, "context_editor escapeHtml misses double quotes"
    assert "&#39;" in helper, "context_editor escapeHtml misses single quotes"
