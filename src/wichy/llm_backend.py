import time
from typing import Any, Dict, List, Optional, Union

from openai import BadRequestError, OpenAI
from pydantic import BaseModel
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


class LLMBackendMultimodalNotSupported(Exception):
    """Raised when the backend model doesn't support multimodal content."""

    def __init__(self, message="backend model does not support multimodal content"):
        self.message = message
        super().__init__(self.message)

    def __str__(self):
        return self.message


class LLMBackendRateLimitExceeded(Exception):
    """Raised when the LLM backend rate limit is exceeded after maximum retries."""

    def __init__(
        self, message="rate limit exceeded after maximum retries", retry_count=None
    ):
        self.retry_count = retry_count
        self.message = message
        super().__init__(self.message)

    def __str__(self):
        m = self.message
        if self.retry_count is not None:
            m += f" (attempted {self.retry_count} retries)"
        return m


class function(BaseModel):
    arguments: str
    name: str


class called_tool(BaseModel):
    id: str
    type: str
    function: function


class Message(BaseModel):
    content: Union[str, List[Dict[str, Any]]]
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


class LLMResponse(BaseModel):
    """Container for LLM response including token usage data."""

    message: Message
    usage: Optional[Dict[str, int]] = (
        None  # contains prompt_tokens, completion_tokens, total_tokens
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
    if (
        host_part.startswith("localhost")
        or host_part.startswith("127.")
        or host_part.startswith("192.168.")
        or host_part.startswith("10.")
        or host_part.startswith("172.")
    ):
        base_url = f"http://{host_part}/v1"
    else:
        base_url = f"https://{host_part}/v1"

    return (base_url, model)


def message_indicates_context_length_reached(m: str) -> bool:
    if "maximum" in m and "context" in m and "length" in m:
        return True

    # can be extended with more conditions

    return False


def message_indicates_rate_limit(error) -> bool:
    m = str(error).lower()
    if "temporarily rate-limited upstream" in m:
        return True

    if "Error code".lower() in m and "429" in m and "rate" in m and "limit" in m:
        return True

    return False


def error_indicates_multimodal_not_supported(error) -> bool:
    """
    Check if an error indicates the model doesn't support multimodal content.

    Args:
        error: The exception or error object

    Returns:
        True if the error indicates multimodal content is not supported
    """
    # Check for various error patterns that indicate multimodal not supported
    error_str = str(error).lower()

    # Patterns that indicate multimodal content involvement
    multimodal_patterns = [
        "image_url",  # OpenAI-style error
        "image",  # Generic image reference
        "multimodal",
        "content type",
        "invalid content",
        "unsupported content",
        "content_part",
        "does not support image",
        "vision",
    ]

    # Must also have some indication this is about content type not supported
    type_indicators = [
        "unsupported",
        "invalid",
        "not supported",
        "does not support",
        "does not have",
        "cannot process",
    ]

    # Specific error messages that definitively indicate multimodal not supported
    # These bypass the pattern + type_indicator requirement
    definitive_patterns = [
        "no endpoints found that support image input",  # OpenRouter-style 404
        "does not support image",
        "image input not supported",
        "vision not supported",
        "multimodal not supported",
    ]

    # Check definitive patterns first
    if any(p in error_str for p in definitive_patterns):
        return True

    has_pattern = any(p in error_str for p in multimodal_patterns)
    has_type_indicator = any(p in error_str for p in type_indicators)

    return has_pattern and has_type_indicator


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

        # Check for multimodal not supported error
        if error_indicates_multimodal_not_supported(e):
            raise LLMBackendMultimodalNotSupported(
                message=f"Model does not support multimodal content: {e.message or str(e)}"
            )

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
        # Check for multimodal errors in other exception types
        if error_indicates_multimodal_not_supported(e):
            raise LLMBackendMultimodalNotSupported(
                message=f"Model does not support multimodal content: {str(e)}"
            )
        if message_indicates_rate_limit(e):
            MAX_RETRIES = 3
            retry_count = extra_kwargs.pop("retry_count", 0)
            if retry_count >= MAX_RETRIES:
                raise LLMBackendRateLimitExceeded(retry_count=retry_count)
            backoff = 3 * (2**retry_count)  # 3, 6, 12 seconds
            console.log(
                f"got rate limited, will retry in {backoff} seconds (attempt {retry_count + 1})"
            )
            Console().print(
                f"[dim][bold]→[/bold] LLM backend:[/dim] Rate limited, will retry in {backoff} seconds (attempt {retry_count + 1})"
            )
            time.sleep(backoff)
            return call(
                context=context,
                tool_defs=tool_defs,
                model_str=model_str,
                extra_args=extra_args,
                retry_count=retry_count + 1,
                **extra_kwargs,
            )
        # something else is not right
        raise e

    elapsed_time = time.time() - start_time

    # Unwrap response
    m = Message.from_choice(response.choices[0])

    # Extract usage data
    usage_data = None
    if hasattr(response, "usage") and response.usage:
        usage_data = {
            "prompt_tokens": getattr(response.usage, "prompt_tokens", 0),
            "completion_tokens": getattr(response.usage, "completion_tokens", 0),
            "total_tokens": getattr(response.usage, "total_tokens", 0),
        }

    log_msg = {
        "finish reason": m.finish_reason,
        "model": response.model,
        "elapsed time": f"{elapsed_time:.2f}s",
        "total tokens": f"{usage_data['total_tokens'] if usage_data else 'N/A'}",
    }
    # Pretty print similar to base.py
    total_tokens_str = str(usage_data["total_tokens"]) if usage_data else "N/A"
    Console().print(
        f"[dim][bold]→[/bold] LLM response:[/dim] "
        f"[bold]{response.model}[/bold]"
        f"[dim] finish={m.finish_reason}, elapsed={elapsed_time:.2f}s, total_tokens={total_tokens_str}[/dim]"
    )
    console.log(log_msg)  # raw output

    return LLMResponse(message=m, usage=usage_data)
