from openai import OpenAI
from pydantic import BaseModel
from typing import List, Optional
from rich import print
import json


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


def call(context, tool_defs):
    # point to local llama-cpp-python OpenAI-compatible server
    client = OpenAI(base_url="http://localhost:8080/v1", api_key="sk-local")
    # use chat completions API
    print("[italic]calling llm endpoint[/italic]")
    response = client.chat.completions.create(
        model="model-set-by-llama-server",
        messages=context,
        tools=tool_defs,
        # max_tokens=512
    )

    # unwrap response
    m = Message.from_choice(response.choices[0])
    print(f"[italic]finish reason: {m.finish_reason}[/italic]")
    if contains_unparsed_tool_call(m.content):
        print("[italic][yellow]found unparsed tool call[/yellow][/italic]")
    return m


def contains_unparsed_tool_call(text):
    try:
        str(text).index("<tool_call>")
        str(text).index("</tool_call>")
    except ValueError as e:
        return False
    except Exception:
        # Lazy for now
        return False
    return True


def parse_tool_calls(text):
    start = str(text).index("<tool_call>")
    end = str(text).index("</tool_call>")
    x = text[start + len("<tool_call>") : end]
    print(start, end, x)

    calls = []
    for l in str(x).splitlines():
        if l.strip() == "":
            continue
        c = parse_tool_call(l)


def parse_tool_call(text):
    try:
        f = function(json.loads(text))
        print(f)
    except Exception as e:
        return None



if __name__ == "__main__":
    test_text = """I see the issue. The regex pattern `FLAG\{[^\}]*\}` should work correctly to match the flag format `FLAG{<text>}`. Let me try searching again with this corrected 
pattern.

I see the issue. The regex pattern `FLAG\{[^\}]*\}` should work correctly to match the flag format `FLAG{<text>}`. Let me try searching again with this corrected pattern.

<tool_call>
{"name": "search_recursive", "arguments": {"path": ".", "pattern": "FLAG\\{[^\}]*\\}"}}
</tool_call>"""

    res = parse_tool_calls(test_text)
