from wichy.root_agent.helpers import load_user_root_agents
from wichy.root_agent.root_agent_desc_basic import root_agent_desc
from wichy.root_agent.root_agent_desc_code_advanced import root_agent_desc_code_advanced

INCLUDED_ROOT_AGENT_DESC = [root_agent_desc, root_agent_desc_code_advanced]


ALL_ROOT_AGENT_DESC = INCLUDED_ROOT_AGENT_DESC

ALL_ROOT_AGENT_DESC.extend(load_user_root_agents())
