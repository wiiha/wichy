from prompt_toolkit.completion import NestedCompleter
from rich.markdown import Markdown

from wichy.agents.root_agent import ContextResetStrategies
from wichy.agents.sub_agent import console_sub_agents
from wichy.artifact import SESSION_ID
from wichy.artifact.artifact import ArtifactReference
from wichy.artifact.store import ArtifactStore
from wichy.helpers.console import console
from wichy.tools.base import console_tool_result


class ContextResetException(Exception):
    def __init__(self, strategy: ContextResetStrategies, message="Reset context"):
        self.strategy = strategy
        self.message = message
        super().__init__(self.message)

    def __str__(self):
        return f"{self.message}: strategy='{self.strategy}'"


slash_completer = NestedCompleter.from_nested_dict(
    {
        "/logging": {
            "on": None,
            "off": None,
        },
        "/context": {"reset": None, "reset_by_summary": None},
        "/exit": None,
        "/artifacts": {"list": None},
    }
)


class SlashCommandChecker:
    def check_command(self, line: str):
        if line.startswith("/"):
            command = line.strip().lower()
            if command == "/exit":
                raise EOFError
            if command == "/logging on":
                console.quiet = False
                console_tool_result.quiet = False
                console_sub_agents.quiet = False
                return "logging on"
            if command == "/logging off":
                console.quiet = True
                console_tool_result.quiet = True
                console_sub_agents.quiet = True
                return "logging off"
            if command == "/logging":
                return f"{console.quiet=} {console_tool_result.quiet=} {console_sub_agents.quiet=}"
            if command == "/context reset":
                raise ContextResetException(strategy=ContextResetStrategies.NUKE)
            if command == "/context reset_by_summary":
                raise ContextResetException(strategy=ContextResetStrategies.SUMMARY)
            if command.startswith("/artifacts list"):
                store = ArtifactStore(session_id=SESSION_ID)
                artifacts = store.all_latest()
                if not artifacts:
                    return "No artifacts found."
                if len(artifacts) <= 3:
                    result = "\n\n".join([f"{a.as_text()}" for a in artifacts])
                    return f"Found {len(artifacts)} artifact(s):\n\n{result}"

                ars: list[ArtifactReference] = []
                for a in artifacts:
                    ars.append(ArtifactReference.from_artifact(a))

                ars.sort(key=lambda ar: ar.created_at)

                result = "\n".join([f"{ar.format_for_prompt()}" for ar in ars])
                return Markdown(f"**Found {len(ars)} artifact(s):**\n\n{result}")

            return f"Unknown command: {command}"
        return None
