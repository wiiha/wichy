from wichy.tools.task.agent_definitions import (
    bash_agent,
    explore_agent,
    general_purpose_agent,
    web_research_agent,
)
from wichy.tools.task.data_analysis_agent import data_analysis_agent
from wichy.tools.task.base import TaskAgentDefinitionBase
from wichy.tools.task.loader import load_all_sub_agents

# Default (built-in) task agent definitions
_DEFAULT_TASK_AGENT_DEFS = {
    bash_agent.name: bash_agent,
    explore_agent.name: explore_agent,
    general_purpose_agent.name: general_purpose_agent,
    web_research_agent.name: web_research_agent,
    data_analysis_agent.name: data_analysis_agent,
}

# Task agent definitions — defaults merged with home dir and local dir overrides
TASK_AGENT_DEFS = load_all_sub_agents(defaults=_DEFAULT_TASK_AGENT_DEFS)


def print_list(xs: dict[str, TaskAgentDefinitionBase]):
    out = ""
    for x in xs.values():
        out += "- " + x.name + ": " + x.description + "\n"
    return out
