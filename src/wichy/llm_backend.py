from openai import OpenAI
import time
from pydantic import BaseModel
from typing import List, Optional
from rich import print
from wichy.helpers.console import console
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

def backend_and_model_from_model_str(model_str:str):
    """
    Takes a string on format <backend>/<model_path>/<longer_sub_path>
    
    Args:
        model_str: string containing both backend and model name

    Returns:
        tuple (backend,model)
    """
    parts = model_str.strip().split("/")
    backend = parts[0]
    model = "/".join(parts[1:])
    model = model.strip()
    return (backend,model)


def call(context, tool_defs=None, model_str=None):
    """
    Call an LLM backend with the given context and tools.

    Args:
        context: The conversation context/messages
        tool_defs: Tool definitions for function calling
        model_str: string specifying backend and model name on format `<backend>/<model_name>`

    Returns:
        Message object with the model's response
    """
    if model_str is None:
        raise ValueError("missing value for parameter model_str, cannot be None.")
    backend, model_name = backend_and_model_from_model_str(model_str)
    # Configure client based on backend
    model = None
    if backend == "ollama":
        client = OpenAI(
            base_url="http://localhost:11434/v1",
            api_key="ollama",
        )
        if model_name == None:
            raise ValueError("parameter model_name is required for backend ollama.")
        model = model_name

    elif backend == "llama_cpp":
        client = OpenAI(base_url="http://localhost:8080/v1", api_key="sk-local")
        model = "model-set-by-llama-server"
    else:
        raise ValueError(f"Unknown backend: {backend}. Use 'ollama' or 'llama_cpp'. got model string: {model_str}")

    # Make the API call
    console.log(f"calling llm endpoint [backend={backend}, model={model}]")
    start_time = time.time()
    response = client.chat.completions.create(
        model=model,
        messages=context,
        tools=tool_defs,
        # max_tokens=512
    )
    elapsed_time = time.time() - start_time

    # Unwrap response
    m = Message.from_choice(response.choices[0])
    console.log(
        {
            "finish reason": m.finish_reason,
            "elapsed time": f"{elapsed_time:.2f}s",
            "total tokens": f"{response.usage.total_tokens}",
        }
    )

    if contains_unparsed_tool_call(m.content):
        console.log(
            "[italic][yellow]found unparsed tool call[/yellow][/italic]",
            {"unparsed call": extract_tool_calls(m.content)},
        )

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


def extract_tool_calls(text):
    start = str(text).index("<tool_call>")
    end = str(text).index("</tool_call>")
    x = text[start + len("<tool_call>") : end]
    return x


def parse_tool_calls(text):

    x = extract_tool_calls(text)

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
