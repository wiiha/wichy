from .agents import bash_agent, explore_agent, general_purpose_agent, plan_agent

TASK_AGENT_DEFS = {
    bash_agent.name: bash_agent,
    explore_agent.name: explore_agent,
    general_purpose_agent.name: general_purpose_agent,
    plan_agent.name: plan_agent,
}
