import json
from rich import print
from tools import ALL_TOOLS, get_tool_definitions
from llm_backend import called_tool, Message, call

context = []


def tool_call(tools, item: called_tool):
    result = None
    name = item.function.name
    args = json.loads(item.function.arguments)
    print("[italic]tool call: " + name + " " + json.dumps(args) + "[/italic]")
    for tool in tools:
        if name == tool.name:
            result = tool.validate_and_execute(**args)

    if result is None:
        result = "There is no tool called " + item.function.name + "."
    return {"role": "tool", "tool_call_id": item.id, "content": result}


def handle_tools(tools, response: Message):
    if response.finish_reason != "tool_calls":
        return False

    context.append(
        {
            "role": "assistent",
            "content": response.content,
            "tool_calls": [t.model_dump() for t in response.tool_calls],
        }
    )

    print("[italic]got " + str(len(response.tool_calls)) + " tool calls[/italic]")
    osz = len(context)
    for item in response.tool_calls:
        context.append(tool_call(tools, item))
    return len(context) != osz


def process(line):
    tools = ALL_TOOLS
    context.append({"role": "user", "content": line})
    tool_defs = get_tool_definitions(tools)
    response = call(context=context, tool_defs=tool_defs)

    while handle_tools(tools, response):
        response = call(context, tool_defs)
    context.append({"role": "assistant", "content": response.content})
    return response.content


def main():
    context.append({"role": "system", "content": "You are a helpful assistent."})
    while True:
        line = input("> ")
        result = process(line)
        print(f">>> {result}\n")


if __name__ == "__main__":
    main()
