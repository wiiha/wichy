"""Agent builder - constructs RootAgent with proper configuration."""

from typing import Dict

from wichy.console import user_console

from wichy.cli_parser import CliConfig
from wichy.constants import ROLE_SYSTEM
from wichy.context.handler import context_from_file
from wichy.helpers.environment_info import environment_information
from wichy.helpers.prompt import preprocess_prompt
from wichy.root_agent.helpers import ParsedRootAgentDesc, parse_root_agent_markdown_desc
from wichy.root_agent.root_agent import RootAgent
from wichy.skills.skills_info import skills_information
from wichy.tools.base import BaseTool


class AgentBuilderError(Exception):
    """Base exception for agent building errors."""

    pass


class RootAgentNotFoundError(AgentBuilderError):
    """Raised when specified root agent doesn't exist."""

    pass


class ModelNotSpecifiedError(AgentBuilderError):
    """Raised when no model is specified."""

    pass


class SystemPromptEmptyError(AgentBuilderError):
    """Raised when system prompt is empty."""

    pass


class ContextLoadError(AgentBuilderError):
    """Raised when context file cannot be loaded."""

    pass


class AgentBuilder:
    """Builds RootAgent instances from configuration."""

    def __init__(
        self,
        cli_config: CliConfig,
        tools: list[BaseTool],
        skills: Dict[str, object],
        root_agent_descriptions: list[ParsedRootAgentDesc],
        all_descriptions: list[ParsedRootAgentDesc] = None,
    ):
        """
        Initialize AgentBuilder.

        Args:
            cli_config: CLI configuration from CliParser
            tools: List of instantiated and filtered tools
            skills: Dictionary of loaded skills
            root_agent_descriptions: List of parsed root agent descriptions (from files)
            all_descriptions: Deprecated - use root_agent_descriptions instead
        """
        self.cli_config = cli_config
        self.tools = tools
        self.skills = skills
        self.root_agent_descriptions = {
            ra.props["name"]: ra for ra in root_agent_descriptions
        }
        self.all_descriptions = (
            all_descriptions
            if all_descriptions is not None
            else root_agent_descriptions
        )

    def build(self, context=None) -> RootAgent:
        """
        Build and return a RootAgent instance.

        Args:
            context: Optional pre-loaded context. If None, fresh conversation.

        Returns:
            Configured RootAgent ready for use.

        Raises:
            RootAgentNotFoundError: If specified root agent doesn't exist
            ModelNotSpecifiedError: If no model is specified
            SystemPromptEmptyError: If system prompt is empty
        """
        # 1. Select root agent
        selected_root_agent = self._select_root_agent()

        # 2. Resolve model string (CLI overrides frontmatter)
        model_str = self._resolve_model_str(selected_root_agent)

        # 3. Validate model string
        if model_str.strip() == "":
            raise ModelNotSpecifiedError(
                "No model specified, either specify in frontmatter or using --model-str"
            )

        agent_goes_first = True

        if self.cli_config.user_first:
            # Agent doesn't go first if user specified other using cli flag
            agent_goes_first = False

        if context != None:
            # If we load a previous context, then agent dont go first.
            agent_goes_first = False

        # 4. Create RootAgent instance
        root_agent = RootAgent(
            model_str=model_str,
            tools=self.tools,
            name=selected_root_agent.props.get("name"),
            context=context,
            skills=self.skills,
            agent_has_first_initiative=agent_goes_first,
            auto_compact_threshold=self.cli_config.auto_compact_threshold,
        )

        # 5. Add system prompt if fresh conversation (no context provided)
        if context is None:
            self._add_system_prompt(root_agent, selected_root_agent)

        return root_agent

    def _select_root_agent(self) -> ParsedRootAgentDesc:
        """Select the root agent based on CLI config."""
        agent_name = self.cli_config.root_agent_description
        selected = self.root_agent_descriptions.get(agent_name)

        if not selected:
            raise RootAgentNotFoundError(
                f"Specified root agent '{agent_name}' does not exist"
            )

        return selected

    def _resolve_model_str(self, root_agent: ParsedRootAgentDesc) -> str:
        """Resolve model string, with CLI taking precedence over frontmatter."""
        model_from_frontmatter = (
            root_agent.props.get("model") or root_agent.props.get("model_str") or ""
        )
        cli_model = self.cli_config.model_str

        return cli_model if cli_model != "" else model_from_frontmatter

    def _load_context_if_specified(self):
        """Load context from file if --load-ctx was specified."""
        if not self.cli_config.load_ctx:
            return None

        try:
            context = context_from_file(self.cli_config.load_ctx)
            return context
        except Exception as e:
            raise ContextLoadError(f"Failed to load context file: {e}") from e

    def _add_system_prompt(
        self, root_agent: RootAgent, root_agent_desc: ParsedRootAgentDesc
    ):
        """
        Add system prompt to root agent's context with optional skills and environment info.

        Args:
            root_agent: The RootAgent instance
            root_agent_desc: The parsed root agent description
        """
        system_prompt = root_agent_desc.system_prompt

        if system_prompt.strip() == "":
            raise SystemPromptEmptyError(
                "Loaded root agent description did not contain a system prompt. It is required."
            )

        # Preprocess prompt with tool verification
        verify_against = {"tools": [x.name for x in self.tools]}
        system_prompt = preprocess_prompt(
            prompt=system_prompt, verify_against=verify_against
        )

        # Add skills information if enabled
        include_skills = (
            root_agent_desc.props.get("include_skills", "true").lower() != "false"
        )
        if include_skills:
            skills_info = skills_information()
            if skills_info:
                system_prompt += (
                    f"\n\nYou have access to the following skills:\n{skills_info}\n"
                )

        # Add environment information if enabled
        if root_agent_desc.props.get("include_env_info", "").lower() != "false":
            system_prompt += (
                f"\n\nHere is useful information about the environment you are running in:\n"
                f"{environment_information()}\n\n"
            )

        root_agent.context.append(
            {
                "role": ROLE_SYSTEM,
                "content": system_prompt,
            }
        )

    def print_context_loaded_info(self, context):
        """Print information about loaded context."""
        msg_count = len(context.context)
        roles = {}
        for msg in context.context:
            role = msg.get("role", "unknown")
            roles[role] = roles.get(role, 0) + 1
        role_summary = ", ".join([f"{count} {role}" for role, count in roles.items()])
        user_console.print(
            f"[blue]Context loaded:[/blue] {msg_count} messages ({role_summary})"
        )


def build_agent_from_config(
    cli_config: CliConfig,
    tools: list[BaseTool],
    skills: Dict[str, object],
    root_agent_descriptions: list[ParsedRootAgentDesc] = None,
    context=None,
) -> RootAgent:
    """
    Convenience function to build an agent without instantiating AgentBuilder directly.

    Args:
        cli_config: CLI configuration
        tools: List of instantiated tools
        skills: Dictionary of loaded skills
        root_agent_descriptions: List of parsed root agent descriptions.
            If None, will load from ALL_ROOT_AGENT_DESC.
        context: Optional pre-loaded context for conversation continuation.

    Returns:
        Built RootAgent instance.
    """
    if root_agent_descriptions is None:
        from wichy.root_agent import ALL_ROOT_AGENT_DESC

        root_agent_descriptions = [
            parse_root_agent_markdown_desc(rad) for rad in ALL_ROOT_AGENT_DESC
        ]

    builder = AgentBuilder(
        cli_config=cli_config,
        tools=tools,
        skills=skills,
        root_agent_descriptions=root_agent_descriptions,
    )
    return builder.build(context=context)
