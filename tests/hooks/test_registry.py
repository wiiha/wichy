"""Tests for the hooks registry module."""

import threading


from wichy.hooks import pre_tool
from wichy.hooks.registry import (
    HookRegistry,
    clear_hooks,
    get_hooks_for_tool,
    hook_registry,
    register_hook,
)
from wichy.hooks.result import HookResult
from wichy.hooks.types import HookType


def sample_hook(context):
    """Sample hook function for testing."""
    return HookResult.approve()


def another_hook(context):
    """Another sample hook function for testing."""
    return HookResult.deny("Test denial")


def third_hook(context):
    """Third sample hook function for testing."""
    return HookResult.log({"data": "test"})


class TestRegisterPreHook:
    """Test suite for registering pre-tool hooks."""

    def test_register_pre_hook(self):
        """Test registering a pre-tool hook."""
        clear_hooks()

        register_hook(
            hook_type=HookType.PRE_TOOL,
            function=sample_hook,
            tool_name="bash",
        )

        hooks = get_hooks_for_tool(HookType.PRE_TOOL, "bash")
        assert len(hooks) == 1
        assert hooks[0].function == sample_hook
        assert hooks[0].tool_name == "bash"
        assert hooks[0].hook_type == HookType.PRE_TOOL

    def test_register_pre_hook_with_priority(self):
        """Test registering a pre-tool hook with custom priority."""
        clear_hooks()

        register_hook(
            hook_type=HookType.PRE_TOOL,
            function=sample_hook,
            tool_name="bash",
            priority=10,
        )

        hooks = get_hooks_for_tool(HookType.PRE_TOOL, "bash")
        assert len(hooks) == 1
        assert hooks[0].priority == 10

    def test_register_pre_hook_with_name(self):
        """Test registering a pre-tool hook with custom name."""
        clear_hooks()

        register_hook(
            hook_type=HookType.PRE_TOOL,
            function=sample_hook,
            tool_name="bash",
            name="custom_hook_name",
        )

        hooks = get_hooks_for_tool(HookType.PRE_TOOL, "bash")
        assert hooks[0].name == "custom_hook_name"


class TestRegisterPostHook:
    """Test suite for registering post-tool hooks."""

    def test_register_post_hook(self):
        """Test registering a post-tool hook."""
        clear_hooks()

        register_hook(
            hook_type=HookType.POST_TOOL,
            function=sample_hook,
            tool_name="write_file",
        )

        hooks = get_hooks_for_tool(HookType.POST_TOOL, "write_file")
        assert len(hooks) == 1
        assert hooks[0].function == sample_hook
        assert hooks[0].tool_name == "write_file"
        assert hooks[0].hook_type == HookType.POST_TOOL

    def test_register_post_hook_different_from_pre(self):
        """Test that post hooks are stored separately from pre hooks."""
        clear_hooks()

        register_hook(
            hook_type=HookType.PRE_TOOL,
            function=sample_hook,
            tool_name="bash",
        )
        register_hook(
            hook_type=HookType.POST_TOOL,
            function=another_hook,
            tool_name="bash",
        )

        pre_hooks = get_hooks_for_tool(HookType.PRE_TOOL, "bash")
        post_hooks = get_hooks_for_tool(HookType.POST_TOOL, "bash")

        assert len(pre_hooks) == 1
        assert len(post_hooks) == 1
        assert pre_hooks[0].function == sample_hook
        assert post_hooks[0].function == another_hook


class TestRegisterWildcardHook:
    """Test suite for registering wildcard hooks (tool_name=None)."""

    def test_register_wildcard_hook(self):
        """Test registering a wildcard hook that applies to all tools."""
        clear_hooks()

        register_hook(
            hook_type=HookType.PRE_TOOL,
            function=sample_hook,
            tool_name=None,  # Wildcard
        )

        # Should appear for any tool
        bash_hooks = get_hooks_for_tool(HookType.PRE_TOOL, "bash")
        write_hooks = get_hooks_for_tool(HookType.PRE_TOOL, "write_file")

        assert len(bash_hooks) == 1
        assert len(write_hooks) == 1
        assert bash_hooks[0].function == sample_hook
        assert write_hooks[0].function == sample_hook

    def test_wildcard_and_specific_hooks_combined(self):
        """Test that wildcard and specific hooks are both returned."""
        clear_hooks()

        register_hook(
            hook_type=HookType.PRE_TOOL,
            function=sample_hook,
            tool_name=None,  # Wildcard
        )
        register_hook(
            hook_type=HookType.PRE_TOOL,
            function=another_hook,
            tool_name="bash",
        )

        hooks = get_hooks_for_tool(HookType.PRE_TOOL, "bash")

        assert len(hooks) == 2
        # Both wildcard and specific should be present
        functions = [h.function for h in hooks]
        assert sample_hook in functions
        assert another_hook in functions


class TestGetHooksForTool:
    """Test suite for get_hooks_for_tool function."""

    def test_get_hooks_for_tool(self):
        """Test getting hooks for a specific tool."""
        clear_hooks()

        register_hook(
            hook_type=HookType.PRE_TOOL,
            function=sample_hook,
            tool_name="bash",
        )
        register_hook(
            hook_type=HookType.POST_TOOL,
            function=another_hook,
            tool_name="bash",
        )

        # Only pre hooks for PRE_TOOL
        pre_hooks = get_hooks_for_tool(HookType.PRE_TOOL, "bash")
        assert len(pre_hooks) == 1
        assert pre_hooks[0].hook_type == HookType.PRE_TOOL

        # Only post hooks for POST_TOOL
        post_hooks = get_hooks_for_tool(HookType.POST_TOOL, "bash")
        assert len(post_hooks) == 1
        assert post_hooks[0].hook_type == HookType.POST_TOOL

    def test_get_hooks_includes_wildcard(self):
        """Test that get_hooks includes wildcard hooks."""
        clear_hooks()

        register_hook(
            hook_type=HookType.PRE_TOOL,
            function=sample_hook,
            tool_name=None,  # Wildcard
        )
        register_hook(
            hook_type=HookType.PRE_TOOL,
            function=another_hook,
            tool_name="bash",
        )

        hooks = get_hooks_for_tool(HookType.PRE_TOOL, "bash")

        assert len(hooks) == 2


class TestGetHooksPriorityOrder:
    """Test suite for hooks priority ordering."""

    def test_get_hooks_priority_order(self):
        """Test that hooks are returned in priority order."""
        clear_hooks()

        # Register hooks with different priorities (out of order)
        register_hook(
            hook_type=HookType.PRE_TOOL,
            function=sample_hook,
            tool_name="bash",
            priority=50,
        )
        register_hook(
            hook_type=HookType.PRE_TOOL,
            function=another_hook,
            tool_name="bash",
            priority=10,  # Should come first
        )
        register_hook(
            hook_type=HookType.PRE_TOOL,
            function=third_hook,
            tool_name="bash",
            priority=90,  # Should come last
        )

        hooks = get_hooks_for_tool(HookType.PRE_TOOL, "bash")

        assert len(hooks) == 3
        assert hooks[0].priority == 10
        assert hooks[1].priority == 50
        assert hooks[2].priority == 90

    def test_priority_order_with_wildcards(self):
        """Test priority ordering when wildcards and specific hooks are mixed."""
        clear_hooks()

        # Wildcard hook with priority 30
        register_hook(
            hook_type=HookType.PRE_TOOL,
            function=sample_hook,
            tool_name=None,
            priority=30,
        )
        # Specific hook with priority 10
        register_hook(
            hook_type=HookType.PRE_TOOL,
            function=another_hook,
            tool_name="bash",
            priority=10,
        )
        # Specific hook with priority 50
        register_hook(
            hook_type=HookType.PRE_TOOL,
            function=third_hook,
            tool_name="bash",
            priority=50,
        )

        hooks = get_hooks_for_tool(HookType.PRE_TOOL, "bash")

        assert len(hooks) == 3
        assert hooks[0].priority == 10
        assert hooks[1].priority == 30
        assert hooks[2].priority == 50


class TestUnregisterHook:
    """Test suite for unregistering hooks."""

    def test_unregister_hook(self):
        """Test removing a hook by name."""
        clear_hooks()

        register_hook(
            hook_type=HookType.PRE_TOOL,
            function=sample_hook,
            tool_name="bash",
            name="my_hook",
        )

        hooks = get_hooks_for_tool(HookType.PRE_TOOL, "bash")
        assert len(hooks) == 1

        # Unregister by name
        result = hook_registry.unregister(HookType.PRE_TOOL, "my_hook")
        assert result is True

        hooks = get_hooks_for_tool(HookType.PRE_TOOL, "bash")
        assert len(hooks) == 0

    def test_unregister_hook_not_found(self):
        """Test unregistering a hook that doesn't exist."""
        clear_hooks()

        result = hook_registry.unregister(HookType.PRE_TOOL, "nonexistent_hook")
        assert result is False

    def test_unregister_hook_from_wildcard(self):
        """Test unregistering a wildcard hook."""
        clear_hooks()

        register_hook(
            hook_type=HookType.PRE_TOOL,
            function=sample_hook,
            tool_name=None,
            name="wildcard_hook",
        )

        hooks = get_hooks_for_tool(HookType.PRE_TOOL, "any_tool")
        assert len(hooks) == 1

        result = hook_registry.unregister(HookType.PRE_TOOL, "wildcard_hook")
        assert result is True

        hooks = get_hooks_for_tool(HookType.PRE_TOOL, "any_tool")
        assert len(hooks) == 0

    def test_unregister_one_of_many(self):
        """Test unregistering one hook when multiple exist for same tool."""
        clear_hooks()

        register_hook(
            hook_type=HookType.PRE_TOOL,
            function=sample_hook,
            tool_name="bash",
            name="hook_1",
        )
        register_hook(
            hook_type=HookType.PRE_TOOL,
            function=another_hook,
            tool_name="bash",
            name="hook_2",
        )

        result = hook_registry.unregister(HookType.PRE_TOOL, "hook_1")
        assert result is True

        hooks = get_hooks_for_tool(HookType.PRE_TOOL, "bash")
        assert len(hooks) == 1
        assert hooks[0].name == "hook_2"


class TestClearHooks:
    """Test suite for clearing all hooks."""

    def test_clear_hooks(self):
        """Test clearing all hooks from the registry."""
        clear_hooks()

        # Register multiple hooks
        register_hook(
            hook_type=HookType.PRE_TOOL,
            function=sample_hook,
            tool_name="bash",
        )
        register_hook(
            hook_type=HookType.POST_TOOL,
            function=another_hook,
            tool_name="write_file",
        )
        register_hook(
            hook_type=HookType.PRE_TOOL,
            function=third_hook,
            tool_name=None,
        )

        # Clear all
        clear_hooks()

        # All should be empty
        assert len(get_hooks_for_tool(HookType.PRE_TOOL, "bash")) == 0
        assert len(get_hooks_for_tool(HookType.POST_TOOL, "write_file")) == 0
        assert len(get_hooks_for_tool(HookType.PRE_TOOL, "any_tool")) == 0

    def test_clear_hooks_idempotent(self):
        """Test that clear_hooks can be called multiple times safely."""

        # Register a hook first
        @pre_tool("bash")
        def some_hook(ctx):
            return HookResult.approve()

        assert len(get_hooks_for_tool(HookType.PRE_TOOL, "bash")) == 1

        # First clear
        clear_hooks()
        assert len(get_hooks_for_tool(HookType.PRE_TOOL, "bash")) == 0

        # Second clear (no hooks to clear, but should not error)
        clear_hooks()
        assert len(get_hooks_for_tool(HookType.PRE_TOOL, "bash")) == 0

        # Third clear
        clear_hooks()
        assert len(get_hooks_for_tool(HookType.PRE_TOOL, "bash")) == 0


class TestSingletonPattern:
    """Test suite for registry singleton pattern."""

    def test_singleton_pattern(self):
        """Test that registry is a singleton."""
        # Get two references
        registry1 = HookRegistry()
        registry2 = HookRegistry()
        hook_registry_ref = hook_registry

        # All should be the same instance
        assert registry1 is registry2
        assert registry1 is hook_registry_ref

    def test_singleton_shared_state(self):
        """Test that singleton instances share state."""
        clear_hooks()

        # Register using one reference
        registry1 = HookRegistry()
        register_hook(
            hook_type=HookType.PRE_TOOL,
            function=sample_hook,
            tool_name="bash",
        )

        # Access using another reference
        registry2 = HookRegistry()
        hooks = registry2.get_hooks(HookType.PRE_TOOL, "bash")

        assert len(hooks) == 1
        assert hooks[0].function == sample_hook


class TestNoHooksRegistered:
    """Test suite for empty registry scenarios."""

    def test_no_hooks_registered(self):
        """Test that get_hooks returns empty list when no hooks registered."""
        clear_hooks()

        hooks = get_hooks_for_tool(HookType.PRE_TOOL, "bash")
        assert hooks == []

        hooks = get_hooks_for_tool(HookType.POST_TOOL, "write_file")
        assert hooks == []


class TestMultipleHookSameTool:
    """Test suite for multiple hooks on same tool."""

    def test_multiple_hooks_same_tool(self):
        """Test registering multiple hooks for the same tool."""
        clear_hooks()

        register_hook(
            hook_type=HookType.PRE_TOOL,
            function=sample_hook,
            tool_name="bash",
            name="hook_1",
        )
        register_hook(
            hook_type=HookType.PRE_TOOL,
            function=another_hook,
            tool_name="bash",
            name="hook_2",
        )
        register_hook(
            hook_type=HookType.PRE_TOOL,
            function=third_hook,
            tool_name="bash",
            name="hook_3",
        )

        hooks = get_hooks_for_tool(HookType.PRE_TOOL, "bash")

        assert len(hooks) == 3
        names = [h.name for h in hooks]
        assert "hook_1" in names
        assert "hook_2" in names
        assert "hook_3" in names

    def test_multiple_hooks_same_tool_different_priorities(self):
        """Test multiple hooks with different priorities for same tool."""
        clear_hooks()

        register_hook(
            hook_type=HookType.PRE_TOOL,
            function=sample_hook,
            tool_name="bash",
            priority=100,
            name="late_hook",
        )
        register_hook(
            hook_type=HookType.PRE_TOOL,
            function=another_hook,
            tool_name="bash",
            priority=1,
            name="early_hook",
        )
        register_hook(
            hook_type=HookType.PRE_TOOL,
            function=third_hook,
            tool_name="bash",
            priority=50,
            name="normal_hook",
        )

        hooks = get_hooks_for_tool(HookType.PRE_TOOL, "bash")

        assert len(hooks) == 3
        assert hooks[0].name == "early_hook"
        assert hooks[1].name == "normal_hook"
        assert hooks[2].name == "late_hook"


class TestHookForNonexistentTool:
    """Test suite for registering hooks for tools that don't exist."""

    def test_hook_for_nonexistent_tool(self):
        """Test that you can register a hook for a tool that doesn't exist."""
        clear_hooks()

        # This should work - the registry doesn't check if the tool exists
        register_hook(
            hook_type=HookType.PRE_TOOL,
            function=sample_hook,
            tool_name="nonexistent_tool_xyz",
        )

        hooks = get_hooks_for_tool(HookType.PRE_TOOL, "nonexistent_tool_xyz")
        assert len(hooks) == 1
        assert hooks[0].function == sample_hook

    def test_hook_for_special_characters_in_tool_name(self):
        """Test registering hooks for tools with special characters in name."""
        clear_hooks()

        register_hook(
            hook_type=HookType.PRE_TOOL,
            function=sample_hook,
            tool_name="tool-with-dashes_and_underscores",
        )

        hooks = get_hooks_for_tool(
            HookType.PRE_TOOL, "tool-with-dashes_and_underscores"
        )
        assert len(hooks) == 1


class TestRegistryThreadSafety:
    """Test suite for registry thread safety."""

    def test_concurrent_registration(self):
        """Test that concurrent registrations don't cause issues."""
        clear_hooks()

        num_threads = 10
        hooks_per_thread = 100
        errors = []

        def register_hooks(thread_id):
            try:
                for i in range(hooks_per_thread):
                    register_hook(
                        hook_type=HookType.PRE_TOOL,
                        function=sample_hook,
                        tool_name=f"tool_{thread_id}",
                        name=f"hook_{thread_id}_{i}",
                    )
            except Exception as e:
                errors.append(e)

        threads = [
            threading.Thread(target=register_hooks, args=(i,))
            for i in range(num_threads)
        ]

        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0

        # Check total hooks registered
        total = 0
        for i in range(num_threads):
            hooks = get_hooks_for_tool(HookType.PRE_TOOL, f"tool_{i}")
            total += len(hooks)

        assert total == num_threads * hooks_per_thread


class TestHookSource:
    """Test suite for hook source attribute."""

    def test_default_source_is_python(self):
        """Test that default source is 'python'."""
        clear_hooks()

        register_hook(
            hook_type=HookType.PRE_TOOL,
            function=sample_hook,
            tool_name="bash",
        )

        hooks = get_hooks_for_tool(HookType.PRE_TOOL, "bash")
        assert hooks[0].source == "python"

    def test_custom_source(self):
        """Test setting a custom source."""
        clear_hooks()

        register_hook(
            hook_type=HookType.PRE_TOOL,
            function=sample_hook,
            tool_name="bash",
            source="yaml",
        )

        hooks = get_hooks_for_tool(HookType.PRE_TOOL, "bash")
        assert hooks[0].source == "yaml"


class TestListAll:
    """Test suite for list_all method."""

    def test_list_all_returns_copy(self):
        """Test that list_all returns a copy of the registry."""
        clear_hooks()

        register_hook(
            hook_type=HookType.PRE_TOOL,
            function=sample_hook,
            tool_name="bash",
        )

        all_hooks = hook_registry.list_all()

        assert HookType.PRE_TOOL in all_hooks
        assert "bash" in all_hooks[HookType.PRE_TOOL]
        assert len(all_hooks[HookType.PRE_TOOL]["bash"]) == 1

        # Modify the copy shouldn't affect registry
        all_hooks[HookType.PRE_TOOL]["bash"] = []

        hooks = get_hooks_for_tool(HookType.PRE_TOOL, "bash")
        assert len(hooks) == 1
