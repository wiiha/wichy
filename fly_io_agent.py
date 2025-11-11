from openai import OpenAI
import json
from pydantic import BaseModel
from typing import List, Optional
from rich import print
from tools import ALL_TOOLS, get_tool_definitions


class function(BaseModel):
    arguments: str
    name: str


class called_tool(BaseModel):
    id: str
    type: str
    function: function


class Message(BaseModel):
    content: str
    role: str
    tool_calls: Optional[List[called_tool]] = None
    finish_reason: str

    @classmethod
    def from_choice(cls, c):
        m = c.message
        content = m.content
        if content is None:
            content = ""
        x = []
        if m.tool_calls:
            for t in m.tool_calls:
                cid = t.id
                tool_type = t.type
                function_args = t.function.arguments
                function_name = t.function.name
                x.append(
                    called_tool(
                        id=cid,
                        type=tool_type,
                        function=function(name=function_name, arguments=function_args),
                    )
                )
        else:
            x = None

        return cls(
            content=content, role=m.role, tool_calls=x, finish_reason=c.finish_reason
        )


# point to local llama-cpp-python OpenAI-compatible server
client = OpenAI(base_url="http://localhost:8080/v1", api_key="sk-local")

context = []


def call(tools):
    # use chat completions API
    print("[italic]calling llm endpoint[/italic]")
    response = client.chat.completions.create(
        model="model-set-by-llama-server",
        messages=context,
        tools=tools,
        # max_tokens=512
    )

    # unwrap response
    m = Message.from_choice(response.choices[0])
    return m


def tool_call(item: called_tool):
    result = None
    name = item.function.name
    args = json.loads(item.function.arguments)
    print("[italic]tool call: " + name + " " + json.dumps(args)+"[/italic]")
    for tool in ALL_TOOLS:
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
        context.append(tool_call(item))
    return len(context) != osz


def process(line):
    context.append({"role": "user", "content": line})
    tools = get_tool_definitions(ALL_TOOLS)
    response = call(tools)
    # new code: resolve tool calls
    while handle_tools(tools, response):
        response = call(tools)
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
