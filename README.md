# WICHY

This is an initial try for me on building a small agent by the
definition of simonw [An LLM agent runs tools in a loop to achive a goal](https://simonwillison.net/2025/Sep/18/agents/).

**some goals**

- all is running locally
- should be a stepping stone for larger projects

## future todos

- ADD "generic sub agent" is like a version of the root agent and has tools, but it cannot call sub agents. The goal is to avoid the root agents context to overflow. This generic agent should be encouraged to create artifacts whenever possible.
- CHANGE return from todo tool should contain the full todo list
- ADD possibility to define the path where `.wichy` dir should be stored.
- ADD functionality for defining conditional parts in root agent and sub agent prompts by means of a `<conditional>` tag system. Spec below.

```xml
<conditional>
    <condition>
        <tool>tool_name
                        <!--
tool_name => exact match
*partial_name => endswith
tool_partial* => beginswith
*partial* => contains
-->
        </tool>
    </condition>
This text would only appear if the tool named `tool_name` is present in the available tools for the agent.
</conditional>
```

- FIX the artifact matching. The current implementation where an LLM is used to decide on similar artifacts does not yield satisfying results. It usually us to prone on matching things that should not be matched. I am considering some kind of vector based approach. I read that duckdb has implemented an array column type that can be used with an index and special function in order to do vector similarity searches.

## reference

- https://docs.unsloth.ai/models/ibm-granite-4.0#recommended-inference-settings
- https://fly.io/blog/everyone-write-an-agent/
- https://platform.openai.com/docs/guides/function-calling#handling-function-calls
- https://www.ibm.com/granite/docs/models/granite#tool-calling
- Qwen thinking mode switch: https://docs.unsloth.ai/models/qwen3-how-to-run-and-fine-tune#switching-between-thinking-and-non-thinking-mode
- Embedding model: https://ollama.com/library/nomic-embed-text
- Xiaomi: MiMo-V2-Flash: https://openrouter.ai/xiaomi/mimo-v2-flash:free
