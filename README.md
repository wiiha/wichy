# WICHY

This is an initial try for me on building a small agent by the
definition of simonw [An LLM agent runs tools in a loop to achive a goal](https://simonwillison.net/2025/Sep/18/agents/).

**some goals**

- all is running locally
- should be a stepping stone for larger projects

## future todos

- ADD "generic sub agent" is like a version of the root agent and has tools, but it cannot call sub agents. The goal is to avoid the root agents context to overflow. This generic agent should be encouraged to create artifacts whenever possible.

## reference

- https://docs.unsloth.ai/models/ibm-granite-4.0#recommended-inference-settings
- https://fly.io/blog/everyone-write-an-agent/
- https://platform.openai.com/docs/guides/function-calling#handling-function-calls
- https://www.ibm.com/granite/docs/models/granite#tool-calling
- Qwen thinking mode switch: https://docs.unsloth.ai/models/qwen3-how-to-run-and-fine-tune#switching-between-thinking-and-non-thinking-mode
- Embedding model: https://ollama.com/library/nomic-embed-text
- Xiaomi: MiMo-V2-Flash: https://openrouter.ai/xiaomi/mimo-v2-flash:free
