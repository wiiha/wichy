import json
import os
import time
from typing import List, Optional

from openai import BadRequestError, OpenAI
from pydantic import BaseModel
from rich import print
from rich.console import Console

from wichy.config import settings
from wichy.helpers.console import console


class LLMBackendContextLimitReached(Exception):
    def __init__(
        self,
        allowed_max=None,
        current_count=None,
        message="current context exceeds LLM backend limits",
    ):
        self.allowed_max = allowed_max
        self.current_count = current_count
        self.message = message
        super().__init__(self.message)

    def __str__(self):
        m = f"{self.message}"
        if self.allowed_max:
            m += f" allowed_max_tokens={self.allowed_max}"

        if self.current_count:
            m += f" current_token_count={self.current_count}"
        return m


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


def backend_and_model_from_model_str(model_str: str):
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
    return (backend, model)


def parse_generic_backend(model_str: str):
    """
    Parse generic backend string: generic/<host>[:<port>]##<model>

    Args:
        model_str: string in format "generic/<host>[:<port>]##<model>"

    Returns:
        tuple (base_url, model) where base_url includes the /v1 suffix

    Examples:
        "generic/localhost:8080##llama-3" -> ("http://localhost:8080/v1", "llama-3")
        "generic/api.myservice.com##gpt-4" -> ("https://api.myservice.com/v1", "gpt-4")
        "generic/192.168.1.10:9000##my-model" -> ("http://192.168.1.10:9000/v1", "my-model")
    """
    parts = model_str.strip().split("/")
    if len(parts) < 2:
        raise ValueError(
            f"Invalid generic backend format. Expected 'generic/<host>##<model>', got: {model_str}"
        )

    host_model_part = "/".join(parts[1:])
    if "##" not in host_model_part:
        raise ValueError(
            f"Invalid generic backend format. Expected 'generic/<host>##<model>', got: {model_str}"
        )

    host_part, model = host_model_part.split("##", 1)
    model = model.strip()

    if not host_part:
        raise ValueError(f"Host is empty in generic backend: {model_str}")

    if not model:
        raise ValueError(f"Model is empty in generic backend: {model_str}")

    # Determine protocol and build base_url
    # Default to http for localhost/local IPs, https otherwise
    if host_part.startswith("localhost") or host_part.startswith("127.") or host_part.startswith("192.168.") or host_part.startswith("10.") or host_part.startswith("172."):
        base_url = f"http://{host_part}/v1"
    else:
        base_url = f"https://{host_part}/v1"

    return (base_url, model)


def message_indicates_context_length_reached(m: str) -> bool:
    if "maximum" in m and "context" in m and "length" in m:
        return True

    # can be extended with more conditions

    return False


def call(context, tool_defs=None, model_str=None, extra_args=None, **extra_kwargs):
    """
    Call an LLM backend with the given context and tools.

    extra_args: optional dict of parameters to forward to client.chat.completions.create
    extra_kwargs: alternative way to pass forwarded parameters as kwargs
    """
    if model_str is None:
        raise ValueError("missing value for parameter model_str, cannot be None.")
    backend, model_name = backend_and_model_from_model_str(model_str)

    # Configure client based on backend
    backend_specific_headers: dict = {}
    model = None
    if backend == "ollama":
        client = OpenAI(
            base_url=settings.ollama_base_url,
            api_key="ollama",
        )
        if model_name is None:
            raise ValueError("parameter model_name is required for backend ollama.")
        model = model_name

    elif backend == "llama_cpp":
        client = OpenAI(base_url=settings.llama_cpp_base_url, api_key="sk-local")
        model = "model-set-by-llama-server"
    elif backend == "open_router":
        api_key = settings.openrouter_api_key
        if api_key is None:
            raise ValueError(
                "using backend open_router requires env variable OPEN_ROUTER_API_KEY, it is missing."
            )
        client = OpenAI(
            base_url=settings.openrouter_base_url,
            api_key=api_key,
        )
        model = model_name
        backend_specific_headers = {
            "provider": {
                "allow_fallbacks": True,
                "sort": "price",
                # "data_collection": "deny",
            },
        }
        if "xiaomi/mimo-v2-flash:free" in model:
            backend_specific_headers["reasoning"] = {"enabled": False}
    elif backend == "generic":
        base_url, model = parse_generic_backend(model_str)
        api_key = settings.openai_api_key or "sk-generic"
        client = OpenAI(base_url=base_url, api_key=api_key)
    else:
        raise ValueError(
            f"Unknown backend: {backend}. Use 'ollama', 'llama_cpp', 'open_router', or 'generic', got model string: {model_str}"
        )

    # Build forwarded arguments
    forwarded = {}
    if extra_args:
        if not isinstance(extra_args, dict):
            raise TypeError("extra_args must be a dict if provided")
        forwarded.update(extra_args)
    forwarded.update(extra_kwargs)

    # Allowlist known safe parameters to avoid passing unexpected fields
    ALLOWED_FORWARD_KEYS = {
        "max_tokens",
        "temperature",
        "top_p",
        "n",
        "stop",
        "presence_penalty",
        "frequency_penalty",
        "logit_bias",
        "timeout",
        "response_format",
    }
    # keep only allowed keys to avoid invalid API errors
    forwarded = {k: v for k, v in forwarded.items() if k in ALLOWED_FORWARD_KEYS}

    # Make the API call
    console.log(f"calling llm endpoint [backend={backend}, model={model}]")
    start_time = time.time()

    try:
        response = client.chat.completions.create(
            model=model,
            messages=context,
            tools=tool_defs,
            **forwarded,
            extra_body={**backend_specific_headers},
        )
    except BadRequestError as e:
        context_len_error = False
        if e.type == "exceed_context_size_error":
            context_len_error = True

        if e.message and message_indicates_context_length_reached(e.message):
            context_len_error = True

        if not context_len_error:
            raise e

        b = e.body
        allowed_max = None
        current_count = None

        if b:
            allowed_max = b.get("n_ctx")
            current_count = b.get("n_prompt_tokens")

        raise LLMBackendContextLimitReached(
            allowed_max=allowed_max, current_count=current_count, message=e.message
        )

    except Exception as e:
        # something else is not right
        raise e

    elapsed_time = time.time() - start_time

    # Unwrap response
    m = Message.from_choice(response.choices[0])
    log_msg = {
        "finish reason": m.finish_reason,
        "model": response.model,
        "elapsed time": f"{elapsed_time:.2f}s",
        "total tokens": f"{response.usage.total_tokens}",
    }
    # Pretty print similar to base.py
    Console().print(
        f"[dim][bold]→[/bold] LLM response:[/dim] "
        f"[bold]{response.model}[/bold]"
        f"[dim] finish={m.finish_reason}, elapsed={elapsed_time:.2f}s, total_tokens={response.usage.total_tokens}[/dim]"
    )
    console.log(log_msg)  # raw output

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
