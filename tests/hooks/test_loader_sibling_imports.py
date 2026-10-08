"""Tests for hook loading from a file that imports a sibling module.

A hooks file lives in its own directory and may do a plain ``import helper``.
Before the fix, that directory was never on ``sys.path`` (the import failed)
and the helper was never evicted from ``sys.modules`` (its hooks vanished on
reload). These tests pin both behaviours and the reload path.
"""

import json
import sys
from pathlib import Path

import pytest
from flask import Blueprint, Flask

from wichy.console.user import ServerConsole, user_console
from wichy.hooks import clear_hooks
from wichy.hooks.loader import HookLoader, hook_loader
from wichy.hooks.registry import hook_registry
from wichy.wichy_server.api import register_routes

SIBLING = """
from wichy.hooks import pre_tool, HookResult

@pre_tool("external_tool")
def external_hook(ctx):
    return HookResult.approve()
"""

INLINE_IMPORTING = """
from helper_mod import external_hook  # noqa: F401
from wichy.hooks import pre_tool, HookResult

@pre_tool("inline_tool")
def inline_hook(ctx):
    return HookResult.approve()
"""


def _hook_names():
    names = set()
    for by_tool in hook_registry.list_all().values():
        for hooks in by_tool.values():
            for hook in hooks:
                names.add(hook.name)
    return names


@pytest.fixture(autouse=True)
def _isolate_globals():
    """Snapshot and restore sys.path / sys.modules / the hook registry."""
    path_before = list(sys.path)
    modules_before = set(sys.modules)
    clear_hooks()
    try:
        yield
    finally:
        sys.path[:] = path_before
        for name in set(sys.modules) - modules_before:
            sys.modules.pop(name, None)
        clear_hooks()


def _write(dir_path: Path, name: str, content: str) -> Path:
    target = dir_path / name
    target.write_text(content)
    return target


# -- INV-017: sibling import works; sys.path is left clean ------------------


def test_sibling_import_registers_and_cleans_path(tmp_path):
    _write(tmp_path, "helper_mod.py", SIBLING)
    hooks_file = _write(tmp_path, "hooks.py", INLINE_IMPORTING)

    path_before = list(sys.path)
    loader = HookLoader(hooks_path=hooks_file)
    assert loader.load_hooks() is True
    assert "inline_hook" in _hook_names()
    assert sys.path == path_before


def test_path_not_left_behind_when_hooks_file_fails(tmp_path):
    hooks_file = _write(tmp_path, "hooks.py", "raise RuntimeError('nope')\n")
    path_before = list(sys.path)
    loader = HookLoader(hooks_path=hooks_file)
    assert loader.load_hooks() is False
    assert sys.path == path_before


def test_sequential_loads_do_not_accumulate_paths(tmp_path):
    first = tmp_path / "a"
    second = tmp_path / "b"
    first.mkdir()
    second.mkdir()
    f1 = _write(first, "hooks.py", INLINE_IMPORTING.replace("helper_mod", "helper_a"))
    _write(first, "helper_a.py", SIBLING)
    f2 = _write(second, "hooks.py", INLINE_IMPORTING.replace("helper_mod", "helper_b"))
    _write(second, "helper_b.py", SIBLING)

    path_before = list(sys.path)
    HookLoader(hooks_path=f1).load_hooks()
    HookLoader(hooks_path=f2).load_hooks()
    assert sys.path == path_before


# -- INV-018: reload re-registers imported-file hooks ----------------------


def test_reload_keeps_inline_and_imported_hooks(tmp_path):
    _write(tmp_path, "helper_mod.py", SIBLING)
    hooks_file = _write(tmp_path, "hooks.py", INLINE_IMPORTING)

    loader = HookLoader(hooks_path=hooks_file)
    assert loader.load_hooks() is True
    names_first = _hook_names()
    assert {"inline_hook", "external_hook"} <= names_first

    assert loader.reload_hooks() is True
    assert {"inline_hook", "external_hook"} <= _hook_names()


# -- INV-019: never evict wichy / site-packages-like / unresolvable ---------


def test_wichy_modules_never_evicted(tmp_path):
    hooks_file = _write(tmp_path, "hooks.py", "x = 1\n")
    loader = HookLoader(hooks_path=hooks_file)
    loader.load_hooks()
    assert "wichy" in sys.modules
    assert any(name.startswith("wichy.") for name in sys.modules)


def test_unrelated_module_survives(tmp_path):
    _write(tmp_path, "hooks.py", "x = 1\n")
    other_dir = tmp_path / "elsewhere"
    other_dir.mkdir()
    _write(other_dir, "unrelated_mod.py", "value = 1\n")
    sys.path.insert(0, str(other_dir))
    import unrelated_mod  # noqa: F401

    loader = HookLoader(hooks_path=tmp_path / "hooks.py")
    loader.load_hooks()
    assert "unrelated_mod" in sys.modules


def test_unresolvable_module_survives(tmp_path):
    _write(tmp_path, "hooks.py", "x = 1\n")
    mod = type(sys)("bogus_mod")
    mod.__file__ = "\x00not a path\x00"
    sys.modules["bogus_mod"] = mod

    loader = HookLoader(hooks_path=tmp_path / "hooks.py")
    loader.load_hooks()
    assert "bogus_mod" in sys.modules


# -- INV-020: failures are recorded, never partially loaded ----------------


def test_failing_file_recorded_and_not_in_loaded_paths(tmp_path):
    hooks_file = _write(tmp_path, "hooks.py", "raise ValueError('bad hooks')\n")
    loader = HookLoader(hooks_path=hooks_file)
    assert loader.load_hooks() is False
    assert loader.get_load_errors()
    assert hooks_file not in loader.get_loaded_paths()


def test_failing_file_prints_yellow_warning(tmp_path):
    prior = user_console._impl
    user_console.set_impl(ServerConsole())
    try:
        hooks_file = _write(tmp_path, "hooks.py", "raise ValueError('bad hooks')\n")
        HookLoader(hooks_path=hooks_file).load_hooks()
        messages = user_console.get_messages()
    finally:
        user_console.set_impl(prior)
    assert any("Failed to load hooks" in m for m in messages)


def test_mutating_hooks_file_cannot_mask_its_error(tmp_path):
    hooks_file = _write(
        tmp_path,
        "hooks.py",
        "import sys\nsys.path.insert(0, '/tmp/evil')\nraise ValueError('still bad')\n",
    )
    loader = HookLoader(hooks_path=hooks_file)
    assert loader.load_hooks() is False
    assert loader.get_load_errors()


# -- R1-R4 reproductions (fail pre-change, pass post-change) ----------------


def test_r1_sibling_import_without_manual_path_works(tmp_path):
    _write(tmp_path, "helper_mod.py", SIBLING)
    hooks_file = _write(tmp_path, "hooks.py", INLINE_IMPORTING)
    loader = HookLoader(hooks_path=hooks_file)
    assert loader.load_hooks() is True
    assert "external_hook" in _hook_names()


def test_r2_imported_hook_reappears_after_reload(tmp_path):
    _write(tmp_path, "helper_mod.py", SIBLING)
    hooks_file = _write(tmp_path, "hooks.py", INLINE_IMPORTING)
    loader = HookLoader(hooks_path=hooks_file)
    loader.load_hooks()
    loader.reload_hooks()
    assert "external_hook" in _hook_names()


# -- INV-021: reload is the same path as startup ----------------------------


def test_reload_matches_a_fresh_loader(tmp_path):
    _write(tmp_path, "helper_mod.py", SIBLING)
    hooks_file = _write(tmp_path, "hooks.py", INLINE_IMPORTING)

    loader = HookLoader(hooks_path=hooks_file)
    loader.load_hooks()
    after_reload = sorted(_hook_names())

    fresh = HookLoader(hooks_path=hooks_file)
    fresh.load_hooks()
    assert sorted(_hook_names()) == after_reload


# -- Stage 7: GET /server/api/hooks/status (INV-020 route half) -------------


@pytest.fixture
def hooks_status_client():
    app = Flask(__name__)
    app.config["TESTING"] = True
    bp = Blueprint("wichy_server_api", __name__, url_prefix="/server/api")
    register_routes(bp)
    app.register_blueprint(bp)
    with app.test_client() as client:
        yield client


def test_status_route_reports_clean_state(hooks_status_client, tmp_path):
    errors_before = dict(hook_loader.get_load_errors())
    loaded_before = list(hook_loader.get_loaded_paths())
    loaded_flag_before = hook_loader.is_loaded()
    hook_loader._errors = {}
    hook_loader._loaded_paths = []
    hook_loader._loaded = True
    try:
        resp = hooks_status_client.get("/server/api/hooks/status")
        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert data == {"loaded": True, "loaded_paths": [], "errors": []}
    finally:
        hook_loader._errors = errors_before
        hook_loader._loaded_paths = loaded_before
        hook_loader._loaded = loaded_flag_before


def test_status_route_reports_failure(hooks_status_client, tmp_path):
    hooks_file = _write(tmp_path, "hooks.py", "raise ValueError('bad hooks')\n")
    loader = HookLoader(hooks_path=hooks_file)
    loader.load_hooks()

    errors_before = hook_loader._errors
    loaded_before = hook_loader._loaded_paths
    loaded_flag = hook_loader._loaded
    hook_loader._errors = loader.get_load_errors()
    hook_loader._loaded_paths = loader.get_loaded_paths()
    hook_loader._loaded = loader.is_loaded()
    try:
        resp = hooks_status_client.get("/server/api/hooks/status")
        data = json.loads(resp.data)
        assert data["loaded"] is False
        assert data["errors"][0]["path"] == str(hooks_file)
        assert data["errors"][0]["error_type"] == "ValueError"
        assert "bad hooks" in data["errors"][0]["error_message"]
        assert str(hooks_file) not in data["loaded_paths"]
    finally:
        hook_loader._errors = errors_before
        hook_loader._loaded_paths = loaded_before
        hook_loader._loaded = loaded_flag


def test_wichy_named_module_inside_hooks_dir_survives(tmp_path):
    """The wichy/wichy.* name guard is load-bearing, not just the dir check."""
    _write(tmp_path, "hooks.py", "x = 1\n")
    plain = type(sys)("plain_sibling_mod")
    plain.__file__ = str(tmp_path / "plain_sibling_mod.py")
    sys.modules["plain_sibling_mod"] = plain
    fake_wichy = type(sys)("wichy.fake_ext")
    fake_wichy.__file__ = str(tmp_path / "fake_ext.py")
    sys.modules["wichy.fake_ext"] = fake_wichy

    HookLoader(hooks_path=tmp_path / "hooks.py").load_hooks()

    assert "plain_sibling_mod" not in sys.modules
    assert "wichy.fake_ext" in sys.modules
