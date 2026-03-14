"""Tests for the AgentBuilder class."""

from unittest.mock import MagicMock, patch

import pytest

from wichy.agent_builder import (
    AgentBuilder,
    AgentBuilderError,
    RootAgentNotFoundError,
    ModelNotSpecifiedError,
    SystemPromptEmptyError,
    ContextLoadError,
)
from wichy.root_agent.helpers import ParsedRootAgentDesc


class MockRootAgentDesc:
    """Mock root agent description for testing."""

    def __init__(self, props, system_prompt):
        self.props = props
        self.system_prompt = system_prompt


class MockSkill:
    """Mock skill for testing."""

    def __init__(self, name="test_skill"):
        self.name = name
        self.description = "Test skill"
        self.tags = []
        self.scripts = []


class TestAgentBuilder:
    """Test suite for AgentBuilder."""

    @pytest.fixture
    def mock_cli_config(self):
        """Create a mock CliConfig."""
        from wichy.cli_parser import CliConfig

        return CliConfig(
            show_log=False,
            list_tools=False,
            log_tools=False,
            log_agents=False,
            model_str="",
            tools="",
            not_tools="",
            root_agent_description="test-agent",
            load_ctx=None,
            no_server=False,
        )

    @pytest.fixture
    def mock_root_agent_descriptions(self):
        """Create mock root agent descriptions with required 'name' prop."""
        return [
            MockRootAgentDesc(
                props={
                    "name": "test-agent",
                    "description": "Test agent",
                    "model": "ollama/test",
                },
                system_prompt="You are a test assistant.",
            ),
            MockRootAgentDesc(
                props={
                    "name": "another-agent",
                    "description": "Another test agent",
                    "model": "openai/gpt-4",
                },
                system_prompt="You are another assistant.",
            ),
        ]

    @pytest.fixture
    def mock_tools(self):
        """Create mock tools."""
        from wichy.tools.base import BaseTool

        class MockTool(BaseTool):
            name = "mock_tool"
            description = "A mock tool"
            parameters_model = None

            def execute(self):
                return "result"

        return [MockTool()]

    @pytest.fixture
    def mock_skills(self):
        """Create mock skills dict."""
        return {"test_skill": MockSkill()}

    def test_builder_initialization(
        self, mock_cli_config, mock_tools, mock_skills, mock_root_agent_descriptions
    ):
        """Test AgentBuilder initialization."""
        builder = AgentBuilder(
            cli_config=mock_cli_config,
            tools=mock_tools,
            skills=mock_skills,
            root_agent_descriptions=mock_root_agent_descriptions,
        )
        assert builder.cli_config == mock_cli_config
        assert builder.tools == mock_tools
        assert builder.skills == mock_skills

    def test_build_success(
        self, mock_cli_config, mock_tools, mock_skills, mock_root_agent_descriptions
    ):
        """Test successful agent build."""
        builder = AgentBuilder(
            cli_config=mock_cli_config,
            tools=mock_tools,
            skills=mock_skills,
            root_agent_descriptions=mock_root_agent_descriptions,
        )
        agent = builder.build()

        assert agent is not None
        assert agent.model_str == "ollama/test"
        assert agent.tools == mock_tools
        assert agent.skills == mock_skills

    def test_build_unknown_root_agent(
        self, mock_cli_config, mock_tools, mock_skills, mock_root_agent_descriptions
    ):
        """Test building with non-existent root agent raises error."""
        mock_cli_config.root_agent_description = "nonexistent"
        builder = AgentBuilder(
            cli_config=mock_cli_config,
            tools=mock_tools,
            skills=mock_skills,
            root_agent_descriptions=mock_root_agent_descriptions,
        )

        with pytest.raises(RootAgentNotFoundError) as exc_info:
            builder.build()
        assert "does not exist" in str(exc_info.value)

    def test_build_cli_model_overrides_frontmatter(
        self, mock_cli_config, mock_tools, mock_skills, mock_root_agent_descriptions
    ):
        """Test that CLI model string overrides frontmatter."""
        mock_cli_config.model_str = "openai/gpt-4"
        builder = AgentBuilder(
            cli_config=mock_cli_config,
            tools=mock_tools,
            skills=mock_skills,
            root_agent_descriptions=mock_root_agent_descriptions,
        )
        agent = builder.build()
        assert agent.model_str == "openai/gpt-4"  # CLI overrides

    def test_build_uses_frontmatter_model_if_cli_not_set(
        self, mock_cli_config, mock_tools, mock_skills, mock_root_agent_descriptions
    ):
        """Test that frontmatter model is used if CLI not set."""
        mock_cli_config.model_str = ""
        builder = AgentBuilder(
            cli_config=mock_cli_config,
            tools=mock_tools,
            skills=mock_skills,
            root_agent_descriptions=mock_root_agent_descriptions,
        )
        agent = builder.build()
        assert agent.model_str == "ollama/test"

    def test_build_missing_model_raises_error(
        self, mock_cli_config, mock_tools, mock_skills
    ):
        """Test that missing model raises error."""
        mock_cli_config.model_str = ""
        root_agent_desc = MockRootAgentDesc(
            props={
                "name": "test-agent",
            },  # No model
            system_prompt="Prompt",
        )
        builder = AgentBuilder(
            cli_config=mock_cli_config,
            tools=mock_tools,
            skills=mock_skills,
            root_agent_descriptions=[root_agent_desc],
        )

        with pytest.raises(ModelNotSpecifiedError):
            builder.build()

    def test_build_missing_system_prompt_raises_error(
        self, mock_cli_config, mock_tools, mock_skills
    ):
        """Test that empty system prompt raises error."""
        mock_cli_config.model_str = "test/model"
        root_agent_desc = MockRootAgentDesc(
            props={
                "name": "test-agent",
                "model": "test/model",
            },
            system_prompt="   ",  # whitespace only
        )
        builder = AgentBuilder(
            cli_config=mock_cli_config,
            tools=mock_tools,
            skills=mock_skills,
            root_agent_descriptions=[root_agent_desc],
        )

        with pytest.raises(SystemPromptEmptyError):
            builder.build()

    def test_build_loads_context_if_provided(
        self, mock_cli_config, mock_tools, mock_skills
    ):
        """Test that context is used when provided to build()."""
        from wichy.helpers.context import ContextHandler

        mock_cli_config.load_ctx = None  # Not used in this test

        # Create a fake context
        mock_context = MagicMock(spec=ContextHandler)
        mock_context.context = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "user"},
        ]

        root_agent_desc = MockRootAgentDesc(
            props={
                "name": "test-agent",
                "model": "test/model",
            },
            system_prompt="You are an assistant.",
        )

        builder = AgentBuilder(
            cli_config=mock_cli_config,
            tools=mock_tools,
            skills=mock_skills,
            root_agent_descriptions=[root_agent_desc],
        )

        agent = builder.build(context=mock_context)

        # Should have used the provided context (not added system prompt)
        assert agent.context is mock_context
        # Verify no additional system message was appended (original context is kept as-is)
        assert len(agent.context.context) == 2

    def test_build_does_not_add_system_prompt_if_context_loaded(
        self, mock_cli_config, mock_tools, mock_skills
    ):
        """Test that system prompt is not added when context is provided."""
        from wichy.helpers.context import ContextHandler

        mock_cli_config.load_ctx = None

        mock_context = MagicMock(spec=ContextHandler)
        mock_context.context = [{"role": "system", "content": "existing system"}]

        root_agent_desc = MockRootAgentDesc(
            props={
                "name": "test-agent",
                "model": "test/model",
            },
            system_prompt="You are an assistant.",
        )

        builder = AgentBuilder(
            cli_config=mock_cli_config,
            tools=mock_tools,
            skills=mock_skills,
            root_agent_descriptions=[root_agent_desc],
        )

        agent = builder.build(context=mock_context)

        # Should not have appended a new system message
        assert len(agent.context.context) == 1
        assert agent.context.context[0]["role"] == "system"

    def test_build_adds_system_prompt_if_fresh_conversation(
        self, mock_cli_config, mock_tools, mock_skills
    ):
        """Test that system prompt is added when no context provided."""
        mock_cli_config.load_ctx = None
        root_agent_desc = MockRootAgentDesc(
            props={
                "name": "test-agent",
                "model": "test/model",
            },
            system_prompt="You are an assistant.",
        )
        builder = AgentBuilder(
            cli_config=mock_cli_config,
            tools=mock_tools,
            skills=mock_skills,
            root_agent_descriptions=[root_agent_desc],
        )
        agent = builder.build()

        # Should have system message
        context_list = agent.context.context
        assert any(msg["role"] == "system" for msg in context_list)
        system_msg = [msg for msg in context_list if msg["role"] == "system"][0]
        assert "You are an assistant." in system_msg["content"]

    def test_build_includes_skills_in_system_prompt(
        self, mock_cli_config, mock_tools, mock_skills
    ):
        """Test that skills info is included when enabled."""
        mock_cli_config.load_ctx = None
        root_agent_desc = MockRootAgentDesc(
            props={
                "name": "test-agent",
                "model": "test/model",
                "include_skills": "true",  # default is true, but explicit
            },
            system_prompt="You are an assistant.",
        )
        builder = AgentBuilder(
            cli_config=mock_cli_config,
            tools=mock_tools,
            skills=mock_skills,
            root_agent_descriptions=[root_agent_desc],
        )

        with patch("wichy.agent_builder.skills_information") as mock_info:
            mock_info.return_value = "Available skills: test_skill"
            agent = builder.build()

            # Check that system prompt includes skills info
            system_msg = [
                msg for msg in agent.context.context if msg["role"] == "system"
            ][0]
            assert "Available skills: test_skill" in system_msg["content"]

    def test_build_skills_info_not_included_when_disabled(
        self, mock_cli_config, mock_tools, mock_skills
    ):
        """Test that skills info is not included when disabled."""
        mock_cli_config.load_ctx = None
        root_agent_desc = MockRootAgentDesc(
            props={
                "name": "test-agent",
                "model": "test/model",
                "include_skills": "false",
            },
            system_prompt="You are an assistant.",
        )
        builder = AgentBuilder(
            cli_config=mock_cli_config,
            tools=mock_tools,
            skills=mock_skills,
            root_agent_descriptions=[root_agent_desc],
        )

        with patch("wichy.agent_builder.skills_information") as mock_info:
            agent = builder.build()
            system_msg = [
                msg for msg in agent.context.context if msg["role"] == "system"
            ][0]
            assert "skills" not in system_msg["content"].lower()

    def test_build_includes_env_info_by_default(
        self, mock_cli_config, mock_tools, mock_skills
    ):
        """Test that environment info is included by default."""
        mock_cli_config.load_ctx = None
        root_agent_desc = MockRootAgentDesc(
            props={
                "name": "test-agent",
                "model": "test/model",
                # include_env_info not set or empty should include
            },
            system_prompt="You are an assistant.",
        )
        builder = AgentBuilder(
            cli_config=mock_cli_config,
            tools=mock_tools,
            skills=mock_skills,
            root_agent_descriptions=[root_agent_desc],
        )

        with patch("wichy.agent_builder.environment_information") as mock_env:
            mock_env.return_value = "OS: TestOS"
            agent = builder.build()
            system_msg = [
                msg for msg in agent.context.context if msg["role"] == "system"
            ][0]
            assert "OS: TestOS" in system_msg["content"]

    def test_build_env_info_can_be_disabled(
        self, mock_cli_config, mock_tools, mock_skills
    ):
        """Test that environment info can be disabled."""
        mock_cli_config.load_ctx = None
        root_agent_desc = MockRootAgentDesc(
            props={
                "name": "test-agent",
                "model": "test/model",
                "include_env_info": "false",
            },
            system_prompt="You are an assistant.",
        )
        builder = AgentBuilder(
            cli_config=mock_cli_config,
            tools=mock_tools,
            skills=mock_skills,
            root_agent_descriptions=[root_agent_desc],
        )

        with patch("wichy.agent_builder.environment_information") as mock_env:
            agent = builder.build()
            system_msg = [
                msg for msg in agent.context.context if msg["role"] == "system"
            ][0]
            assert "environment" not in system_msg["content"].lower()
