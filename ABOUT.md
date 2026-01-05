# Wichy - Agent-Based Workflow Autonomy

---

## About Wichy

Wichy is an agentic framework designed for **multi-faceted task automation**, leveraging a modular architecture involving specialized agents and dynamic tools to seamlessly execute tasks. At its core, **Wichy ensures autonomous interaction**, guided by an internal decision-making structure embedded within `RootAgent`.

---

## Core Philosophy: Autonomous Decision Making
- **User prompts do not directly invoke agents**: In Wichy, **user inputs are handled purely for contextual input**. The **RootAgent** ensures that decisions like agent or tool selection happen automatically, ensuring **internal logic-driven workflows**.
- **LLM and Context Integration**: Decisions regarding execution are informed by LLM responses, context management, and the predefined operational logic, entirely within `RootAgent`.

---

## How Wichy Operates Internally

The `RootAgent` orchestrates seamless execution via multiple phases:

### 1. Context and Task Flow
   - User input enters as a data point within the context.
   - The `RootAgent` processes and dynamically integrates sub-agents such as `code_planner_agent`, `code_implementer_agent`, or specialized search agents (`web_research_agent` and `web_research_agent_lite`).
   - Validates and triggers tools such as:
     - `grep` for pattern-based recursive file searching.
     - `tree` for directory tree visualization.
     - `ls` for recursive directory listing.

   - Continuously maintains the context for seamless continuation via tool submissions.

### 2. Dynamic Sub-Agent Decisions
   - Sub-agents are invoked internally based on defined task logic.
   - **Workflow example for code tasks**:
     Use `code_planner_agent` for creating implementation plans, proceeding to `code_implementer_agent` afterward, if appropriate, after context validation.
   - Respects task specificity: Agents are tailored for unique functionalities like research or code logic.

### 3. Tool Subsystems Integration
   Agent-based systems leverage system integrations:
   - OS tasks like `bash`, `ls`, `grep`, and tree directories.
   - Each agent has tools aligned to its operational scope.

---

## Architectural Features

### Autonomous Decision Flow
   Wichy leverages independent decision processes within `Root-Agent`, allowing seamless workflow execution without user overrides.

### Dynamic Workflow Continuity
   Context is updated continuously, ensuring tools are applied within each sub-agent interaction.

### Specialized Tool Support
   OS tools (`grep`, `tree`) help enrich context with execution-specific data, allowing complex tasks.

---

## Why Use Wichy?
- **Modular Task Integration**: Each agent (code or research) is deployable to handle unique aspects of tasks.
- **Seamless Execution**: System-driven automation through pre-defined workflows avoids manual intervention and ensures reliability.
- **Efficient Workflow**: Tasks are executed with clear logic within the workflow, optimized for autonomy.
---

## Practical Usage
To start using Wichy, simply launch the root script with:

```bash
python -m wichy --model <your-model-setting> [optional-arguments]
```

When prompted, enter your task in terms of agent usage, and let Wichy handle the orchestration internally.

---

The framework is designed for **fluid task resolution**, ensuring tasks are executed through logical processes without human-driven agent selection.