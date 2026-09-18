"""
End-to-end test of the REAL batch dispatcher with the REAL file-editing tools.

tests/tools/test_same_file_batch.py drives `tool.execute()` directly, which
proves the lock but not that the batch path exercises it. This module drives
`AgentCore._handle_tools_base`, the loop that decides to dispatch a multi-call
message to a ThreadPoolExecutor (`agent/core.py:284-300`, `settings.parallel_exec`
defaults True). That is the level at which the original bug corrupted a file:
two calls in one assistant message, both reporting success, one edit gone.

**On the strength of the evidence here.** These tests are a *regression* suite:
post-fix every one must pass, and they do. They are NOT all equally strong as
*pre-fix proofs*, which is worth stating precisely because the rendezvous depends
on two threads starting within the barrier timeout:

- `test_no_file_edit_is_lost_across_many_interleavings` is the deterministic
  pre-fix proof at this level. It injects nothing and runs 25 real batches;
  measured 20/20 failing pre-fix.
- The rendezvous-based cases below are strong but NOT individually deterministic
  at dispatcher level. Measured over 20 pre-fix runs each:
  `test_one_batch_two_edits_both_land` 20/20,
  `test_batch_then_read_sees_the_committed_content` 20/20,
  `test_one_batch_mixed_tools_same_file` **17/20** (see below), and
  `test_write_file_and_replace_text_in_one_batch` **0-2/20** -- that last one is
  an outcome check only and does not serve as a pre-fix proof.

`test_one_batch_mixed_tools_same_file` was measured over 40 trials to produce
three distinct outcomes pre-fix: 22x `"alpha\\nbeta\\nTAIL\\n"` (the replace lost),
5x `"ALPHA\\nbeta\\n"` (the insert lost), and 13x `"ALPHA\\nbeta\\nTAIL\\n"` (both
landed). The last case means the two threads did not actually rendezvous -- the
insert ran after the replace rather than alongside it. Since a test that sometimes
passes pre-fix cannot be called deterministic, the deterministic tool-level proof
lives in `tests/tools/test_same_file_batch.py`, whose identical-shaped rendezvous
on a single tool measured 15/15.
"""

import builtins
import os
import threading
from unittest.mock import Mock

import pytest

from wichy.agent.core import AgentCore
from wichy.constants import ROLE_TOOL
from wichy.llm_backend import called_tool, function
from wichy.tools.file_safety import _reset_for_tests
from wichy.tools.insert_lines import InsertLinesTool
from wichy.tools.read_file import ReadFileTool
from wichy.tools.replace_text import ReplaceTextTool
from wichy.tools.write_file import WriteFileTool

_BARRIER_TIMEOUT_S = 0.5


class _BatchAgent(AgentCore):
    """Minimal concrete agent with a real, list-backed context."""

    def __init__(self):
        super().__init__()
        self._name = "BatchAgent"
        self.model_str = "test-model"
        self.tools: list = []
        self.context: list = []

    @property
    def name(self) -> str:
        return self._name

    def _log(self, message: str) -> None:
        pass

    def _log_dict(self, data) -> None:
        pass


@pytest.fixture(autouse=True)
def clean_locks():
    """Isolate the process-global lock registry per test."""
    _reset_for_tests()
    yield
    _reset_for_tests()


def _tool_call(name: str, call_id: str, **args) -> called_tool:
    import json

    return called_tool(
        id=call_id,
        type="function",
        function=function(name=name, arguments=json.dumps(args)),
    )


def _response(tool_calls, content="editing"):
    resp = Mock()
    resp.finish_reason = "tool_calls"
    resp.tool_calls = tool_calls
    resp.content = content
    resp.reasoning = None
    return resp


def _patch_parallel_reads(monkeypatch, target: str):
    """Deterministic rendezvous on the first two read-mode opens of target.

    Same rationale as tests/tools/test_same_file_batch.py: the barrier fires
    after the content is in hand, so both threads provably hold the stale
    snapshot before either writes.
    """
    key = os.path.realpath(target)
    barrier = threading.Barrier(2)
    real_open = builtins.open
    armed = []

    class _BarrieredRead:
        def __init__(self, handle, do_sync):
            self._handle = handle
            self._do_sync = do_sync

        def _sync(self):
            if not self._do_sync:
                return
            try:
                barrier.wait(timeout=_BARRIER_TIMEOUT_S)
            except threading.BrokenBarrierError:
                pass

        def read(self, *a):
            data = self._handle.read(*a)
            self._sync()
            return data

        def readlines(self, *a):
            data = self._handle.readlines(*a)
            self._sync()
            return data

        def __enter__(self):
            self._handle.__enter__()
            return self

        def __exit__(self, *e):
            return self._handle.__exit__(*e)

        def __getattr__(self, name):
            return getattr(self._handle, name)

    def rendezvous_open(file, mode="r", *a, **kw):
        handle = real_open(file, mode, *a, **kw)
        sync = (
            isinstance(file, (str, os.PathLike))
            and os.path.realpath(str(file)) == key
            and "r" in mode
            and "w" not in mode
            and "a" not in mode
            and len(armed) < 2
        )
        if sync:
            armed.append(1)
        return _BarrieredRead(handle, sync)

    monkeypatch.setattr(builtins, "open", rendezvous_open)


def test_one_batch_two_edits_both_land(monkeypatch, tmp_path):
    """The original bug, at the level it occurred.

    One assistant message, two `replace_text` calls on one file. Pre-fix both
    report success and one edit is missing from the file.
    """
    target = tmp_path / "f.txt"
    target.write_text("alpha\nbeta\ngamma\n")

    _patch_parallel_reads(monkeypatch, str(target))

    agent = _BatchAgent()
    tools = [ReplaceTextTool()]
    response = _response(
        [
            _tool_call(
                "replace_text",
                "c1",
                file_path=str(target),
                old_content="alpha",
                new_content="ALPHA",
                count=1,
            ),
            _tool_call(
                "replace_text",
                "c2",
                file_path=str(target),
                old_content="gamma",
                new_content="GAMMA",
                count=1,
            ),
        ]
    )

    modified, _ = agent._handle_tools_base(tools, response)

    assert modified is True
    # Both tool result messages must claim success (that is the trap).
    contents = [m["content"] for m in agent.context if m.get("role") == ROLE_TOOL]
    assert len(contents) == 2
    for c in contents:
        assert "Replaced 1 occurrence(s)" in c, c

    monkeypatch.undo()
    assert target.read_text() == "ALPHA\nbeta\nGAMMA\n"


def test_one_batch_mixed_tools_same_file(monkeypatch, tmp_path):
    """Cross-tool in one batch: replace_text + insert_lines on one file."""
    target = tmp_path / "f.txt"
    target.write_text("alpha\nbeta\n")

    _patch_parallel_reads(monkeypatch, str(target))

    agent = _BatchAgent()
    tools = [ReplaceTextTool(), InsertLinesTool()]
    response = _response(
        [
            _tool_call(
                "replace_text",
                "c1",
                file_path=str(target),
                old_content="alpha",
                new_content="ALPHA",
                count=1,
            ),
            _tool_call(
                "insert_lines",
                "c2",
                file_path=str(target),
                offset=2,
                content="TAIL\n",
            ),
        ]
    )

    agent._handle_tools_base(tools, response)

    monkeypatch.undo()
    content = target.read_text()
    assert "ALPHA" in content, content
    assert "TAIL\n" in content, content
    assert "beta" in content, content


def test_one_batch_different_files_still_parallel(monkeypatch, tmp_path):
    """Distinct files must not serialise each other.

    Two different files, each with an edit. If the lock were global rather
    than per-path, this would still pass -- so this asserts correctness of
    outcome, not parallelism. Parallelism itself is proven at the helper level
    by tests/tools/test_file_safety.py::test_different_keys_do_not_block.
    """
    a = tmp_path / "a.txt"
    b = tmp_path / "b.txt"
    a.write_text("one\n")
    b.write_text("two\n")

    agent = _BatchAgent()
    tools = [ReplaceTextTool()]
    response = _response(
        [
            _tool_call(
                "replace_text",
                "c1",
                file_path=str(a),
                old_content="one",
                new_content="ONE",
                count=1,
            ),
            _tool_call(
                "replace_text",
                "c2",
                file_path=str(b),
                old_content="two",
                new_content="TWO",
                count=1,
            ),
        ]
    )

    agent._handle_tools_base(tools, response)

    assert a.read_text() == "ONE\n"
    assert b.read_text() == "TWO\n"


def test_batch_then_read_sees_the_committed_content(monkeypatch, tmp_path):
    """The user-visible symptom: a later read must not see stale content."""
    target = tmp_path / "f.txt"
    target.write_text("alpha\nbeta\ngamma\n")

    _patch_parallel_reads(monkeypatch, str(target))

    agent = _BatchAgent()
    tools = [ReplaceTextTool()]
    response = _response(
        [
            _tool_call(
                "replace_text",
                "c1",
                file_path=str(target),
                old_content="alpha",
                new_content="ALPHA",
                count=1,
            ),
            _tool_call(
                "replace_text",
                "c2",
                file_path=str(target),
                old_content="beta",
                new_content="BETA",
                count=1,
            ),
        ]
    )
    agent._handle_tools_base(tools, response)
    monkeypatch.undo()

    # A subsequent turn reading the file must see both edits.
    out = ReadFileTool().execute(path=str(target))
    assert "ALPHA" in out, out
    assert "BETA" in out, out


def test_no_file_edit_is_lost_across_many_interleavings(tmp_path):
    """Stress the real dispatcher without an injected rendezvous.

    The other tests pin the mechanism deterministically; this one checks the
    fix holds under genuine scheduling jitter, where the race would otherwise
    surface intermittently.
    """
    tool = ReplaceTextTool()
    for i in range(25):
        target = tmp_path / f"f{i}.txt"
        target.write_text("a\nb\nc\nd\n")

        agent = _BatchAgent()
        response = _response(
            [
                _tool_call(
                    "replace_text",
                    f"c{i}a",
                    file_path=str(target),
                    old_content="a\n",
                    new_content="A\n",
                    count=1,
                ),
                _tool_call(
                    "replace_text",
                    f"c{i}b",
                    file_path=str(target),
                    old_content="c\n",
                    new_content="C\n",
                    count=1,
                ),
            ]
        )
        agent._handle_tools_base([tool], response)

        content = target.read_text()
        assert content == "A\nb\nC\nd\n", f"iteration {i} left {content!r}"


def test_write_file_and_replace_text_in_one_batch(tmp_path):
    """Batch with a wholesale writer and an editor.

    Outcome must be one complete version, never a splice. write_file has no
    read step, so last-writer-wins is the accepted behaviour: the loser's write
    is simply overwritten, exactly as it would be if run sequentially.
    """
    target = tmp_path / "f.txt"
    target.write_text("alpha\nbeta\n")

    agent = _BatchAgent()
    tools = [WriteFileTool(), ReplaceTextTool()]
    response = _response(
        [
            _tool_call("write_file", "c1", path=str(target), content="WHOLE\n"),
            _tool_call(
                "replace_text",
                "c2",
                file_path=str(target),
                old_content="alpha",
                new_content="ALPHA",
                count=1,
            ),
        ]
    )

    agent._handle_tools_base(tools, response)

    content = target.read_text()
    assert content in ("WHOLE\n", "ALPHA\nbeta\n"), f"spliced: {content!r}"
