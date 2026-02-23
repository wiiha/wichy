from wichy.tools.task.agent_definitions import (
    bash_agent,
    explore_agent,
    general_purpose_agent,
    web_research_agent,
)
from wichy.tools.task.base import TaskAgentDefinitionBase


# Task agent definitions
TASK_AGENT_DEFS = {
    bash_agent.name: bash_agent,
    explore_agent.name: explore_agent,
    general_purpose_agent.name: general_purpose_agent,
    web_research_agent.name: web_research_agent,
}



def print_list(xs: dict[str, TaskAgentDefinitionBase]):
    out = ""
    for x in xs.values():
        out += "- " + x.name + ": " + x.description + "\n"
    return out