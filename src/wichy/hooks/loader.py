"""
Hook loader for the Wichy hooks system.

This module provides the HookLoader class that discovers and loads hooks
from multiple locations. Hooks are loaded from both user-global and
project-local locations, similar to how git config works.

Load order:
    1. User-global: ~/.wichy/hooks.py (loaded first)
    2. Project-local: .wichy/hooks.py (loaded second, can override)

Usage:
    from wichy.hooks.loader import hook_loader, initialize_hooks

    # Initialize hooks (typically called from __main__.py)
    initialize_hooks()

    # Or use the loader directly
    loader = HookLoader()
    if loader.load_hooks():
        # Hooks loaded successfully
        pass

    # Or with custom paths (new multi-path API)
    loader = HookLoader(hooks_paths=[Path("/custom/hooks.py")])

    # Or with single custom path (backward compatibility)
    loader = HookLoader(hooks_path=Path("/custom/hooks.py"))
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Optional

from wichy.console.user import user_console

from .registry import clear_hooks, hook_registry


def get_default_paths() -> list[Path]:
    """Return the default hook file paths in load order.

    The order determines priority: later files can override hooks from
    earlier files (or add additional hooks).

    Returns:
        List of paths in load order:
        - User-global: ~/.wichy/hooks.py
        - Project-local: .wichy/hooks.py (relative to cwd)
    """
    return [
        Path.home() / ".wichy" / "hooks.py",  # User-global (loaded first)
        Path.cwd() / ".wichy" / "hooks.py",  # Project-local (loaded second)
    ]


class HookLoader:
    """Discovers and loads hooks from multiple .wichy/hooks.py locations.

    This class handles the discovery and loading of user-defined hooks.
    When a hooks file is loaded, decorators in that file call
    hook_registry.register() to register hooks.

    Hooks are loaded from both user-global (~/.wichy/hooks.py) and
    project-local (.wichy/hooks.py) locations. Later files can add
    hooks or override hooks from earlier files.

    Attributes:
        hooks_paths: List of paths to load hooks from
        hooks_path: First path in hooks_paths (for backward compatibility)
        _loaded_paths: Paths that were successfully loaded
        _errors: Dictionary mapping paths to their load errors
        _loaded: Whether hooks have been successfully loaded
    """

    def __init__(
        self,
        hooks_path: Optional[Path] = None,
        hooks_paths: Optional[list[Path]] = None,
    ):
        """Initialize the HookLoader.

        Args:
            hooks_path: Optional path to a single hooks file. If provided,
                       this takes precedence and sets hooks_paths to [hooks_path].
                       Kept for backward compatibility.
            hooks_paths: Optional list of paths to hooks files. If not provided
                        and hooks_path is also not provided, defaults to
                        [~/.wichy/hooks.py, .wichy/hooks.py].

        Note:
            If both hooks_path and hooks_paths are provided, hooks_path takes
            precedence (for backward compatibility).
        """
        # hooks_path parameter takes precedence for backward compatibility
        if hooks_path is not None:
            self.hooks_paths = [hooks_path]
        elif hooks_paths is not None:
            self.hooks_paths = hooks_paths
        else:
            self.hooks_paths = get_default_paths()

        self._loaded_paths: list[Path] = []
        self._errors: dict[Path, Exception] = {}
        self._loaded: bool = False

    @property
    def hooks_path(self) -> Optional[Path]:
        """Get the hooks path for backward compatibility.

        When using default paths, returns the project-local path.
        When a single path was explicitly set, returns that path.
        When multiple paths were explicitly set, returns the first one.

        Returns:
            The applicable hooks path, or None if hooks_paths is empty.
        """
        if not self.hooks_paths:
            return None
        # If using default paths (both user-global and project-local),
        # return project-local for backward compatibility
        default_paths = get_default_paths()
        if self.hooks_paths == default_paths and len(self.hooks_paths) >= 2:
            return self.hooks_paths[1]  # Project-local path
        # Otherwise return the first (or only) path
        return self.hooks_paths[0]

    def discover_hooks_file(self) -> bool:
        """Check if any hooks file exists.

        Returns:
            True if at least one hooks file exists, False otherwise.
        """
        return any(path.exists() for path in self.hooks_paths)

    def _load_from_path(self, path: Path) -> bool:
        """Load hooks from a single path.

        Args:
            path: Path to the hooks file to load.

        Returns:
            True on success, False on error.
        """
        # Generate unique module name based on path
        # This allows multiple hooks files to be loaded
        module_name = f"wichy_user_hooks_{hash(str(path)) & 0x7FFFFFFF}"

        # Remove module from sys.modules if it exists (for reload support)
        if module_name in sys.modules:
            del sys.modules[module_name]

        try:
            # Create module spec from file
            spec = importlib.util.spec_from_file_location(
                module_name,
                path,
            )

            if spec is None or spec.loader is None:
                error_msg = f"Could not create module spec from {path}"
                user_console.print(f"[yellow]Warning: {error_msg}[/yellow]")
                self._errors[path] = ImportError(error_msg)
                return False

            # Create and execute the module
            module = importlib.util.module_from_spec(spec)
            sys.modules[module_name] = module
            spec.loader.exec_module(module)

            self._loaded_paths.append(path)
            return True

        except SyntaxError as e:
            self._errors[path] = e
            user_console.print(
                f"[yellow]Warning: Failed to load hooks from {path}: {e}[/yellow]"
            )
            return False

        except Exception as e:
            self._errors[path] = e
            user_console.print(
                f"[yellow]Warning: Failed to load hooks from {path}: {e}[/yellow]"
            )
            return False

    def load_hooks(self) -> bool:
        """Load hooks from all paths.

        This method imports hooks from each path in order, which triggers
        decorator registration (decorators call hook_registry.register()).

        Returns:
            True if any hooks loaded successfully or if no hooks files exist.
            False if all hooks files failed to load.

        Note:
            - If a hooks file doesn't exist, it's skipped silently
            - If a hooks file has syntax errors, a warning is logged but
              loading continues to the next file
            - Later files can override hooks from earlier files
        """
        # Clear any previous state
        self._loaded_paths = []
        self._errors = {}

        # Clear registry before loading (for reload support)
        clear_hooks()

        # Remove all wichy_user_hooks modules from sys.modules
        for module_name in list(sys.modules.keys()):
            if module_name.startswith("wichy_user_hooks"):
                del sys.modules[module_name]

        any_success = False
        for path in self.hooks_paths:
            if path.exists():
                if self._load_from_path(path):
                    any_success = True

        self._loaded = any_success or not self.discover_hooks_file()

        # Show summary of loaded hooks
        if self._loaded_paths:
            hooks = hook_registry.list_all()
            total_hooks = sum(
                len(hooks_list)
                for tool_hooks in hooks.values()
                for hooks_list in tool_hooks.values()
            )
            if total_hooks > 0:
                files_str = ", ".join(str(p) for p in self._loaded_paths)
                user_console.print(
                    f"[dim]Loaded {total_hooks} hook(s) from {files_str}[/dim]"
                )

        return self._loaded

    def get_load_errors(self) -> dict[Path, Exception]:
        """Get any errors that occurred during loading.

        Returns:
            Dictionary mapping paths to their exceptions.
        """
        return self._errors.copy()

    def get_loaded_paths(self) -> list[Path]:
        """Get the paths that were successfully loaded.

        Returns:
            List of paths that were loaded successfully.
        """
        return self._loaded_paths.copy()

    def get_load_error(self) -> Optional[Exception]:
        """Get the first error that occurred during loading.

        Returns:
            The first exception that occurred during loading, or None if no error.
            Kept for backward compatibility.
        """
        if self._errors:
            return next(iter(self._errors.values()))
        return None

    def is_loaded(self) -> bool:
        """Check if hooks have been loaded.

        Returns:
            True if hooks have been loaded successfully, False otherwise.
        """
        return self._loaded

    def reload_hooks(self) -> bool:
        """Clear registry and reload hooks.

        This method clears all registered hooks and reloads the hooks files.
        Useful for development or when the hooks files have been modified.

        Returns:
            True on success, False on error.
        """
        # Reset loaded state
        self._loaded = False

        # Reload hooks
        return self.load_hooks()


# Global HookLoader instance
hook_loader = HookLoader()


def initialize_hooks() -> bool:
    """Main entry point called from __main__.py.

    This function initializes the hooks system by loading hooks from
    both user-global and project-local locations.

    Returns:
        True on success, False on error.
    """
    return hook_loader.load_hooks()
