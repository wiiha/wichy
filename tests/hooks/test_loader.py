"""Tests for the HookLoader class in wichy.hooks.loader."""

import os
import tempfile
from pathlib import Path


from wichy.hooks import (
    HookLoader,
    HookType,
    clear_hooks,
    get_hooks_for_tool,
)
from wichy.hooks.loader import get_default_paths


class TestHookLoader:
    """Test suite for HookLoader class."""

    def setup_method(self):
        """Clear hooks before each test."""
        clear_hooks()

    def teardown_method(self):
        """Clear hooks after each test."""
        clear_hooks()

    def test_hooks_path_default(self):
        """Default path is .wichy/hooks.py."""
        loader = HookLoader()
        expected_path = Path.cwd() / ".wichy" / "hooks.py"
        assert loader.hooks_path == expected_path

    def test_hooks_path_custom(self):
        """Custom path accepted."""
        custom_path = Path("/custom/path/hooks.py")
        loader = HookLoader(hooks_path=custom_path)
        assert loader.hooks_path == custom_path

    def test_discover_hooks_file_not_found(self):
        """Returns False when no file."""
        loader = HookLoader(hooks_path=Path("/nonexistent/hooks.py"))
        assert loader.discover_hooks_file() is False

    def test_load_hooks_no_file(self):
        """Returns True when no file (not an error)."""
        loader = HookLoader(hooks_path=Path("/nonexistent/hooks.py"))
        result = loader.load_hooks()
        assert result is True
        assert loader.is_loaded() is True

    def test_load_hooks_success(self):
        """Loads valid hooks file."""
        clear_hooks()

        # Create temp hooks file
        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
            f.write("""
from wichy.hooks import pre_tool, HookResult

@pre_tool('test')
def my_hook(ctx):
    return HookResult.approve()
""")
            temp_path = f.name

        try:
            loader = HookLoader(Path(temp_path))
            assert loader.load_hooks() is True
            assert loader.is_loaded() is True

            # Verify hook registered
            hooks = get_hooks_for_tool(HookType.PRE_TOOL, "test")
            assert len(hooks) == 1
        finally:
            os.unlink(temp_path)
            clear_hooks()

    def test_load_hooks_syntax_error(self):
        """Handles syntax errors gracefully."""
        clear_hooks()

        # Create temp hooks file with syntax error
        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
            f.write("""
from wichy.hooks import pre_tool, HookResult

@pre_tool('test')
def my_hook(ctx):
    return HookResult.approve()
    # Missing closing parenthesis below
    print("unclosed
""")
            temp_path = f.name

        try:
            loader = HookLoader(Path(temp_path))
            result = loader.load_hooks()
            assert result is False
            assert loader.is_loaded() is False
            assert loader.get_load_error() is not None
            assert isinstance(loader.get_load_error(), SyntaxError)
        finally:
            os.unlink(temp_path)
            clear_hooks()

    def test_load_hooks_import_error(self):
        """Handles import errors gracefully."""
        clear_hooks()

        # Create temp hooks file with import error
        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
            f.write("""
from nonexistent_module import something

@pre_tool('test')
def my_hook(ctx):
    return HookResult.approve()
""")
            temp_path = f.name

        try:
            loader = HookLoader(Path(temp_path))
            result = loader.load_hooks()
            assert result is False
            assert loader.is_loaded() is False
            assert loader.get_load_error() is not None
            # Import errors can be ModuleNotFoundError or ImportError
            assert isinstance(
                loader.get_load_error(), (ImportError, ModuleNotFoundError)
            )
        finally:
            os.unlink(temp_path)
            clear_hooks()

    def test_is_loaded(self):
        """Tracks loaded state."""
        loader = HookLoader(hooks_path=Path("/nonexistent/hooks.py"))

        # Initially not loaded
        assert loader.is_loaded() is False

        # After loading (even with no file)
        loader.load_hooks()
        assert loader.is_loaded() is True

    def test_reload_hooks(self):
        """Clears and reloads."""
        clear_hooks()

        # Create first temp hooks file
        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
            f.write("""
from wichy.hooks import pre_tool, HookResult

@pre_tool('test')
def first_hook(ctx):
    return HookResult.approve()
""")
            temp_path1 = f.name

        # Create second temp hooks file
        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
            f.write("""
from wichy.hooks import pre_tool, HookResult

@pre_tool('test')
def second_hook(ctx):
    return HookResult.approve()

@pre_tool('test')
def third_hook(ctx):
    return HookResult.approve()
""")
            temp_path2 = f.name

        try:
            # Load first file
            loader = HookLoader(Path(temp_path1))
            loader.load_hooks()

            hooks = get_hooks_for_tool(HookType.PRE_TOOL, "test")
            assert len(hooks) == 1
            assert hooks[0].name == "first_hook"

            # Reload with second file
            loader = HookLoader(Path(temp_path2))
            loader.load_hooks()

            hooks = get_hooks_for_tool(HookType.PRE_TOOL, "test")
            assert len(hooks) == 2
            hook_names = [h.name for h in hooks]
            assert "second_hook" in hook_names
            assert "third_hook" in hook_names
        finally:
            os.unlink(temp_path1)
            os.unlink(temp_path2)
            clear_hooks()

    def test_get_load_error(self):
        """Returns error from loading."""
        loader = HookLoader(hooks_path=Path("/nonexistent/hooks.py"))

        # No error initially
        assert loader.get_load_error() is None

        # Load succeeds (file doesn't exist is not an error)
        loader.load_hooks()
        assert loader.get_load_error() is None

        # Create file with error
        clear_hooks()
        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
            f.write("""
def broken(:
    pass
""")
            temp_path = f.name

        try:
            loader = HookLoader(Path(temp_path))
            loader.load_hooks()
            error = loader.get_load_error()
            assert error is not None
            assert isinstance(error, SyntaxError)
        finally:
            os.unlink(temp_path)
            clear_hooks()

    def test_load_hooks_file_exists(self):
        """discover_hooks_file returns True when file exists."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
            f.write("# empty hooks file")
            temp_path = f.name

        try:
            loader = HookLoader(Path(temp_path))
            assert loader.discover_hooks_file() is True
        finally:
            os.unlink(temp_path)

    def test_reload_hooks_clears_state(self):
        """reload_hooks clears loaded state before reloading."""
        clear_hooks()

        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
            f.write("""
from wichy.hooks import pre_tool, HookResult

@pre_tool('test')
def my_hook(ctx):
    return HookResult.approve()
""")
            temp_path = f.name

        try:
            loader = HookLoader(Path(temp_path))
            loader.load_hooks()
            assert loader.is_loaded() is True

            # Reset and reload
            result = loader.reload_hooks()
            assert result is True
            assert loader.is_loaded() is True

            # Verify hook still registered
            hooks = get_hooks_for_tool(HookType.PRE_TOOL, "test")
            assert len(hooks) == 1
        finally:
            os.unlink(temp_path)
            clear_hooks()


class TestHookLoaderMultipleHooks:
    """Test suite for HookLoader with multiple hooks."""

    def setup_method(self):
        """Clear hooks before each test."""
        clear_hooks()

    def teardown_method(self):
        """Clear hooks after each test."""
        clear_hooks()

    def test_load_multiple_hooks(self):
        """Loading a file with multiple hooks registers all of them."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
            f.write("""
from wichy.hooks import pre_tool, post_tool, HookResult

@pre_tool('bash')
def check_bash(ctx):
    return HookResult.approve()

@pre_tool('read_file')
def check_read(ctx):
    return HookResult.approve()

@post_tool('bash')
def log_bash(ctx):
    return HookResult.approve()
""")
            temp_path = f.name

        try:
            loader = HookLoader(Path(temp_path))
            assert loader.load_hooks() is True

            # Verify pre_tool hooks for bash
            bash_pre_hooks = get_hooks_for_tool(HookType.PRE_TOOL, "bash")
            assert len(bash_pre_hooks) == 1

            # Verify pre_tool hooks for read_file
            read_pre_hooks = get_hooks_for_tool(HookType.PRE_TOOL, "read_file")
            assert len(read_pre_hooks) == 1

            # Verify post_tool hooks for bash
            bash_post_hooks = get_hooks_for_tool(HookType.POST_TOOL, "bash")
            assert len(bash_post_hooks) == 1
        finally:
            os.unlink(temp_path)
            clear_hooks()

    def test_load_wildcard_hook(self):
        """Loading a file with wildcard hook registers for all tools."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
            f.write("""
from wichy.hooks import pre_tool, HookResult

@pre_tool()  # Wildcard - applies to all tools
def wildcard_hook(ctx):
    return HookResult.approve()
""")
            temp_path = f.name

        try:
            loader = HookLoader(Path(temp_path))
            assert loader.load_hooks() is True

            # Wildcard hooks should be retrieved for any tool
            hooks_for_bash = get_hooks_for_tool(HookType.PRE_TOOL, "bash")
            hooks_for_read = get_hooks_for_tool(HookType.PRE_TOOL, "read_file")

            # Both should return the wildcard hook
            assert len(hooks_for_bash) == 1
            assert len(hooks_for_read) == 1
            assert hooks_for_bash[0].name == "wildcard_hook"
        finally:
            os.unlink(temp_path)


def test_get_default_paths_returns_list():
    """Test that get_default_paths returns a list of two paths."""
    paths = get_default_paths()
    assert isinstance(paths, list)
    assert len(paths) == 2


def test_get_default_paths_user_global():
    """Test that first path is user global (~/.wichy/hooks.py)."""
    paths = get_default_paths()
    assert paths[0] == Path.home() / ".wichy" / "hooks.py"


def test_get_default_paths_project_local():
    """Test that second path is project local (.wichy/hooks.py)."""
    paths = get_default_paths()
    assert paths[1] == Path.cwd() / ".wichy" / "hooks.py"


class TestMultiPathLoading:
    """Test loading hooks from multiple paths."""

    def test_load_from_user_global_only(self, tmp_path):
        """Test loading hooks when only user-global exists."""
        clear_hooks()

        # Create user-global hooks file
        user_dir = tmp_path / "user"
        user_dir.mkdir()
        user_hooks = user_dir / "hooks.py"
        user_hooks.write_text("""
from wichy.hooks import pre_tool, HookResult

@pre_tool("test")
def user_hook(ctx):
    return HookResult.approve()
""")

        loader = HookLoader(hooks_paths=[user_hooks, Path("/nonexistent/path")])
        assert loader.load_hooks() is True

        hooks = get_hooks_for_tool(HookType.PRE_TOOL, "test")
        assert len(hooks) == 1
        assert hooks[0].name == "user_hook"

        clear_hooks()

    def test_load_from_project_local_only(self, tmp_path):
        """Test loading hooks when only project-local exists."""
        clear_hooks()

        # Create project-local hooks file
        proj_dir = tmp_path / "project"
        proj_dir.mkdir()
        proj_hooks = proj_dir / "hooks.py"
        proj_hooks.write_text("""
from wichy.hooks import pre_tool, HookResult

@pre_tool("test")
def project_hook(ctx):
    return HookResult.approve()
""")

        loader = HookLoader(hooks_paths=[Path("/nonexistent/path"), proj_hooks])
        assert loader.load_hooks() is True

        hooks = get_hooks_for_tool(HookType.PRE_TOOL, "test")
        assert len(hooks) == 1
        assert hooks[0].name == "project_hook"

        clear_hooks()

    def test_load_from_both_paths(self, tmp_path):
        """Test loading hooks from both user-global and project-local."""
        clear_hooks()

        # Create user-global hooks
        user_dir = tmp_path / "user"
        user_dir.mkdir()
        user_hooks = user_dir / "hooks.py"
        user_hooks.write_text("""
from wichy.hooks import pre_tool, HookResult

@pre_tool("test")
def user_hook(ctx):
    return HookResult.approve()
""")

        # Create project-local hooks
        proj_dir = tmp_path / "project"
        proj_dir.mkdir()
        proj_hooks = proj_dir / "hooks.py"
        proj_hooks.write_text("""
from wichy.hooks import pre_tool, HookResult

@pre_tool("test")
def project_hook(ctx):
    return HookResult.approve()
""")

        loader = HookLoader(hooks_paths=[user_hooks, proj_hooks])
        assert loader.load_hooks() is True

        # Should have hooks from both files
        hooks = get_hooks_for_tool(HookType.PRE_TOOL, "test")
        assert len(hooks) == 2
        names = [h.name for h in hooks]
        assert "user_hook" in names
        assert "project_hook" in names

        # Check loaded paths
        loaded = loader.get_loaded_paths()
        assert user_hooks in loaded
        assert proj_hooks in loaded

        clear_hooks()

    def test_user_global_loaded_first(self, tmp_path):
        """Test that user-global hooks are loaded before project-local."""
        clear_hooks()

        # User global with priority 10
        user_dir = tmp_path / "user"
        user_dir.mkdir()
        user_hooks = user_dir / "hooks.py"
        user_hooks.write_text("""
from wichy.hooks import pre_tool, HookResult

@pre_tool("test", priority=10)
def first_hook(ctx):
    return HookResult.approve()
""")

        # Project local with priority 50
        proj_dir = tmp_path / "project"
        proj_dir.mkdir()
        proj_hooks = proj_dir / "hooks.py"
        proj_hooks.write_text("""
from wichy.hooks import pre_tool, HookResult

@pre_tool("test", priority=50)
def second_hook(ctx):
    return HookResult.approve()
""")

        loader = HookLoader(hooks_paths=[user_hooks, proj_hooks])
        loader.load_hooks()

        hooks = get_hooks_for_tool(HookType.PRE_TOOL, "test")
        # Priority order: user hook (10) before project hook (50)
        assert hooks[0].priority == 10
        assert hooks[1].priority == 50

        clear_hooks()

    def test_neither_file_exists(self):
        """Test loading when neither file exists."""
        clear_hooks()

        loader = HookLoader(
            hooks_paths=[
                Path("/nonexistent/user/hooks.py"),
                Path("/nonexistent/project/hooks.py"),
            ]
        )
        # Should return True (no hooks to load is not an error)
        assert loader.load_hooks() is True
        assert len(loader.get_loaded_paths()) == 0

        clear_hooks()

    def test_both_files_have_syntax_errors(self, tmp_path):
        """Test that both files failing returns False."""
        clear_hooks()

        user_dir = tmp_path / "user"
        user_dir.mkdir()
        user_hooks = user_dir / "hooks.py"
        user_hooks.write_text("this is not valid python")

        proj_dir = tmp_path / "project"
        proj_dir.mkdir()
        proj_hooks = proj_dir / "hooks.py"
        proj_hooks.write_text("also not valid python")

        loader = HookLoader(hooks_paths=[user_hooks, proj_hooks])
        result = loader.load_hooks()

        # Both failed, should return False
        assert result is False
        assert len(loader.get_loaded_paths()) == 0
        assert len(loader.get_load_errors()) == 2

        clear_hooks()

    def test_first_file_error_second_success(self, tmp_path):
        """Test that errors in first file don't prevent loading second file."""
        clear_hooks()

        # User file has syntax error
        user_dir = tmp_path / "user"
        user_dir.mkdir()
        user_hooks = user_dir / "hooks.py"
        user_hooks.write_text("this is not valid python")

        # Project file is valid
        proj_dir = tmp_path / "project"
        proj_dir.mkdir()
        proj_hooks = proj_dir / "hooks.py"
        proj_hooks.write_text("""
from wichy.hooks import pre_tool, HookResult

@pre_tool("test")
def project_hook(ctx):
    return HookResult.approve()
""")

        loader = HookLoader(hooks_paths=[user_hooks, proj_hooks])
        result = loader.load_hooks()

        # Should succeed (at least one file loaded)
        assert result is True
        assert len(loader.get_loaded_paths()) == 1
        assert proj_hooks in loader.get_loaded_paths()

        # Hook from project file should be registered
        hooks = get_hooks_for_tool(HookType.PRE_TOOL, "test")
        assert len(hooks) == 1
        assert hooks[0].name == "project_hook"

        clear_hooks()
