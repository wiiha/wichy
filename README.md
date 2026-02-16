# WICHY

This is an initial try for me on building a small agent by the
definition of simonw [An LLM agent runs tools in a loop to achive a goal](https://simonwillison.net/2025/Sep/18/agents/).

**some goals**

- all is running locally
- should be a stepping stone for larger projects

**You probably want to go to the [ABOUT.md](ABOUT.md)**

## future todos

- ADD @block_on decorator that can be put on a tools execute function. It should work similar to the @require_human_verification in the sense that it can stop execution flow. The idea is that the decorator should be passed a function (or some other way of passing) that contains the logic for deciding wether to block execution or not.
- ADD first stab at accessing users (markdown based) knowledge store using a certain tool, `KnowledgeStoreTool`. I am thinking a similar interface as the grep tool, regarding output_mode. I guess it will be like the grep tool but with the dir path "locked". Should still be its own tool thou since I plan to have a more advanced implementation with a sqlite backend in the future. An example of a very simple knowledge store is the `notes` dir in this project.
- ADD `AskUserQuestionTool`: ref: /Users/wilhelm/projects/wichy/notes/claude-code-prompt-2.1.17.md
- CHANGE how model is boot strapped so that it is easier to import and extend in other projects.
- CHANGE return from todo tool should contain the full todo list
- ADD possibility to define the path where `.wichy` dir should be stored.
- ADD feature based on "Tool results and user messages may include <system-reminder> tags. <system-reminder> tags contain useful information and reminders. They are automatically added by the system, and bear no direct relation to the specific tool results or user messages in which they appear."

- FIX the artifact matching. The current implementation where an LLM is used to decide on similar artifacts does not yield satisfying results. It usually us to prone on matching things that should not be matched. I am considering some kind of vector based approach. I read that duckdb has implemented an array column type that can be used with an index and special function in order to do vector similarity searches.

## reference

- https://docs.unsloth.ai/models/ibm-granite-4.0#recommended-inference-settings
- https://fly.io/blog/everyone-write-an-agent/
- https://platform.openai.com/docs/guides/function-calling#handling-function-calls
- https://www.ibm.com/granite/docs/models/granite#tool-calling
- Qwen thinking mode switch: https://docs.unsloth.ai/models/qwen3-how-to-run-and-fine-tune#switching-between-thinking-and-non-thinking-mode
- Embedding model: https://ollama.com/library/nomic-embed-text
- Xiaomi: MiMo-V2-Flash: https://openrouter.ai/xiaomi/mimo-v2-flash:free
