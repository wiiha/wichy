"""Control exceptions that slash command hooks may raise to steer the session.

A control exception raised inside any slash hook propagates out of
check_command so the caller's existing except-clause (REPL or web chat)
handles it exactly as a built-in command's exceptions.

wichy.slash_commands imports these and re-exports them as
ContextResetException / ContextDropException / BtwException so every
existing importer keeps working unchanged.
"""

from wichy.root_agent.root_agent import ContextResetStrategies


class SlashContextResetException(Exception):
    """Signals a context reset request raised from a slash command hook."""

    def __init__(
        self,
        strategy: ContextResetStrategies,
        message: str = "Reset context",
    ) -> None:
        self.strategy = strategy
        self.message = message
        super().__init__(self.message)

    def __str__(self) -> str:
        return f"{self.message}: strategy='{self.strategy}'"


class SlashContextDropException(Exception):
    """Signals a drop-last-context-entry request from a slash command hook."""

    def __init__(self, message: str = "Drop last context entry") -> None:
        self.message = message
        super().__init__(message)

    def __str__(self) -> str:
        return self.message


class SlashBtwException(Exception):
    """Signals a sandboxed one-shot question raised from a slash hook."""

    def __init__(self, question: str, model_str: str, btw_tools: list) -> None:
        self.question = question
        self.model_str = model_str
        self.btw_tools = btw_tools
        super().__init__(question)

    def __str__(self) -> str:
        return f"/btw: {self.question}"
