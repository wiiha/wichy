# MCP Server Integration - Implementation Plan

Enable wichy to act as an MCP Host/Client, connecting to MCP servers and making their tools available to the agent.

**Approach:** Full discovery - connect to configured MCP servers at startup, discover all tools, register them as native-feeling tools.

---

## Table of Contents

1. [User Configuration](#1-user-configuration)
2. [Directory Structure](#2-directory-structure)
3. [Component Design](#3-component-design)
4. [Async Bridge](#4-async-bridge)
5. [Integration Points](#5-integration-points)
6. [Error Handling](#6-error-handling)
7. [Known Limitations](#7-known-limitations)
8. [Implementation Steps](#8-implementation-steps)
9. [Testing Strategy](#9-testing-strategy)

---

## 1. User Configuration

### 1.1 Configuration File

**Location:** `~/.wichy/mcp_servers.json` (or `settings.wichy_home / "mcp_servers.json"`)

```json
{
  "mcpServers": {
    "weather": {
      "transport": "stdio",
      "command": "python",
      "args": ["/home/user/mcp-servers/weather/server.py"],
      "env": {
        "OPENWEATHER_API_KEY": "${OPENWEATHER_API_KEY}"
      },
      "disabled": false
    },
    "github": {
      "transport": "stdio",
      "command": "uvx",
      "args": ["mcp-server-github"],
      "env": {
        "GITHUB_TOKEN": "${GITHUB_TOKEN}"
      }
    },
    "calendar": {
      "transport": "http",
      "url": "http://localhost:3000/mcp",
      "headers": {
        "Authorization": "Bearer ${CALENDAR_API_KEY}"
      }
    }
  }
}
```

### 1.2 Configuration Schema

```python
from pydantic import BaseModel, Field
from typing import Literal

class MCPServerConfigStdio(BaseModel):
    """Configuration for stdio-based MCP server (subprocess)."""
    model_config = {"extra": "ignore"}  # Forward compatibility
    
    transport: Literal["stdio"]
    command: str
    args: list[str] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)
    disabled: bool = False

class MCPServerConfigHttp(BaseModel):
    """Configuration for HTTP-based MCP server."""
    model_config = {"extra": "ignore"}
    
    transport: Literal["http"]
    url: str
    headers: dict[str, str] = Field(default_factory=dict)
    disabled: bool = False

MCPServerConfig = MCPServerConfigStdio | MCPServerConfigHttp

class MCPConfig(BaseModel):
    mcpServers: dict[str, MCPServerConfig] = Field(default_factory=dict)
```

### 1.3 Environment Variable Interpolation

Values in `env` and `headers` support `${VAR_NAME}` syntax:

```python
import os

def interpolate_env_vars(value: str) -> str:
    """Replace ${VAR} with environment variable value."""
    return os.path.expandvars(value)
```

### 1.4 Configuration Loading Priority

1. `~/.wichy/mcp_servers.json` (file)
2. `WICHY_MCP_SERVERS` environment variable (JSON string)
3. Empty config (no MCP servers)

---

## 2. Directory Structure

```
src/wichy/mcp/
├── __init__.py           # Public API: discover_mcp_tools(), shutdown_mcp()
├── config.py             # Config loading, validation, interpolation
├── client.py             # MCPClient - wraps fastmcp for one server
├── manager.py            # MCPManager - handles multiple server connections
├── tool_proxy.py         # MCPToolProxy - bridges MCP tools to BaseTool
├── async_bridge.py       # Sync-to-async bridge (event loop in daemon thread)
└── errors.py             # Exception classes
```

---

## 3. Component Design

### 3.1 `config.py` - Configuration Loading

```python
from pathlib import Path
from pydantic import BaseModel, Field
from typing import Literal
import os
import json

from wichy.config import settings


class MCPServerConfigStdio(BaseModel):
    model_config = {"extra": "ignore"}
    transport: Literal["stdio"]
    command: str
    args: list[str] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)
    disabled: bool = False
    
    def get_interpolated_env(self) -> dict[str, str]:
        """Return env with ${VAR} interpolated."""
        return {k: os.path.expandvars(v) for k, v in self.env.items()}


class MCPServerConfigHttp(BaseModel):
    model_config = {"extra": "ignore"}
    transport: Literal["http"]
    url: str
    headers: dict[str, str] = Field(default_factory=dict)
    disabled: bool = False
    
    def get_interpolated_headers(self) -> dict[str, str]:
        """Return headers with ${VAR} interpolated."""
        return {k: os.path.expandvars(v) for k, v in self.headers.items()}


MCPServerConfig = MCPServerConfigStdio | MCPServerConfigHttp


class MCPConfig(BaseModel):
    mcpServers: dict[str, MCPServerConfig] = Field(default_factory=dict)


def load_mcp_config() -> MCPConfig:
    """Load MCP server configuration from file or environment."""
    
    # Try config file
    config_path = settings.wichy_home / "mcp_servers.json"
    if config_path.exists():
        try:
            with open(config_path) as f:
                return MCPConfig.model_validate_json(f.read())
        except Exception as e:
            from wichy.console.user import console
            console.error(f"Failed to load MCP config: {e}")
            return MCPConfig(mcpServers={})
    
    # Try environment variable
    env_config = os.environ.get("WICHY_MCP_SERVERS")
    if env_config:
        try:
            return MCPConfig.model_validate_json(env_config)
        except Exception as e:
            from wichy.console.user import console
            console.error(f"Failed to parse WICHY_MCP_SERVERS: {e}")
            return MCPConfig(mcpServers={})
    
    return MCPConfig(mcpServers={})
```

### 3.2 `errors.py` - Exception Classes

```python
class MCPError(Exception):
    """Base exception for MCP errors."""
    pass


class MCPConfigError(MCPError):
    """Configuration error."""
    pass


class MCPConnectionError(MCPError):
    """Failed to connect to MCP server."""
    pass


class MCPToolExecutionError(MCPError):
    """Tool execution failed."""
    pass


class MCPTimeoutError(MCPError):
    """Operation timed out."""
    pass
```

### 3.3 `async_bridge.py` - Sync/Async Bridge

Provides synchronous access to async fastmcp from wichy's sync tool execution.

**Key design decisions:**
- Event loop runs in a daemon thread (like BrowserManager)
- Thread lock serializes access to prevent race conditions
- Poll-based startup confirmation (proven pattern from browser.py)
- Handles loop death gracefully

```python
import asyncio
import threading
import time
from typing import TypeVar, Coroutine
from concurrent.futures import Future

T = TypeVar('T')


class MCPAsyncBridge:
    """
    Provides sync-to-async bridge for MCP operations.
    
    Runs an asyncio event loop in a daemon thread, allowing
    synchronous code to execute async MCP operations.
    """
    
    _instance = None
    _lock = threading.Lock()  # Class-level lock for thread safety
    
    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance
    
    def __init__(self):
        if self._initialized:
            return
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._initialized = True
    
    def _ensure_loop(self) -> asyncio.AbstractEventLoop:
        """Ensure event loop is running. Thread-safe via class lock."""
        if self._loop is None or self._loop.is_closed():
            self._loop = asyncio.new_event_loop()
            
            def run_loop():
                asyncio.set_event_loop(self._loop)
                self._loop.run_forever()
            
            self._thread = threading.Thread(
                target=run_loop,
                daemon=True,
                name="mcp-async-bridge"
            )
            self._thread.start()
            
            # Poll until loop is running (correct pattern from browser.py)
            while not self._loop.is_running():
                time.sleep(0.001)
        
        return self._loop
    
    def run_sync(self, coro: Coroutine[None, None, T], timeout: float = 60.0) -> T:
        """
        Run an async coroutine from sync context.
        
        Args:
            coro: The coroutine to run
            timeout: Maximum time to wait (seconds)
        
        Returns:
            The result of the coroutine
        
        Raises:
            TimeoutError: If timeout exceeded
            Exception: Any exception from the coroutine
        """
        with self._lock:  # Serialize access
            loop = self._ensure_loop()
            future: Future[T] = asyncio.run_coroutine_threadsafe(coro, loop)
            return future.result(timeout=timeout)
    
    def shutdown(self):
        """Stop the event loop."""
        if self._loop and self._loop.is_running():
            self._loop.call_soon_threadsafe(self._loop.stop)
            if self._thread:
                self._thread.join(timeout=2)
            self._loop = None
            self._thread = None


# Global singleton
mcp_async_bridge = MCPAsyncBridge()
```

### 3.4 `client.py` - Single Server Client

Wraps fastmcp.Client for a single MCP server connection.

```python
import json
from typing import Any

from .async_bridge import mcp_async_bridge
from .errors import MCPConnectionError, MCPToolExecutionError
from .config import MCPServerConfigStdio, MCPServerConfigHttp


class MCPClient:
    """Manages connection to a single MCP server."""
    
    def __init__(self, name: str, config: MCPServerConfigStdio | MCPServerConfigHttp):
        self.name = name
        self.config = config
        self._client: Any = None  # fastmcp.Client
        self._tools: list[dict] | None = None
    
    def connect(self) -> None:
        """Establish connection to MCP server."""
        from fastmcp import Client
        
        if self._client is not None:
            return
        
        try:
            if self.config.transport == "stdio":
                from fastmcp.client.transports import StdioTransport
                
                transport = StdioTransport(
                    command=self.config.command,
                    args=self.config.args,
                    env=self.config.get_interpolated_env()
                )
            else:  # http
                from fastmcp.client.transports import StreamableHttpTransport
                
                transport = StreamableHttpTransport(
                    url=self.config.url,
                    headers=self.config.get_interpolated_headers()
                )
            
            self._client = Client(transport)
            # Enter async context via bridge
            mcp_async_bridge.run_sync(self._client.__aenter__())
            
        except Exception as e:
            self._client = None
            raise MCPConnectionError(f"Failed to connect to MCP server '{self.name}': {e}")
    
    def disconnect(self) -> None:
        """Close connection to MCP server."""
        if self._client is not None:
            try:
                mcp_async_bridge.run_sync(self._client.__aexit__(None, None, None))
            except Exception:
                pass  # Best effort cleanup
            finally:
                self._client = None
    
    def list_tools(self) -> list[dict]:
        """Discover available tools from this server."""
        if self._tools is None:
            if self._client is None:
                raise MCPConnectionError(f"Not connected to '{self.name}'")
            
            try:
                result = mcp_async_bridge.run_sync(self._client.list_tools())
                self._tools = [
                    {
                        "name": t.name,
                        "description": t.description or "",
                        "inputSchema": t.model_dump().get("inputSchema", {})
                    }
                    for t in result
                ]
            except Exception as e:
                raise MCPToolExecutionError(f"Failed to list tools from '{self.name}': {e}")
        
        return self._tools
    
    def call_tool(self, tool_name: str, arguments: dict) -> str:
        """Execute a tool on this server. Returns result as string."""
        if self._client is None:
            raise MCPConnectionError(f"Not connected to '{self.name}'")
        
        try:
            result = mcp_async_bridge.run_sync(
                self._client.call_tool(tool_name, arguments),
                timeout=60.0  # TODO: make configurable via settings
            )
            return self._format_result(result)
        except Exception as e:
            # Return error string (matches native tool behavior)
            return f"[MCP Error] {self.name}/{tool_name}: {e}"
    
    def _format_result(self, result: Any) -> str:
        """Format MCP tool result to string for wichy context."""
        # Handle MCP CallToolResult with content list
        if hasattr(result, 'content'):
            parts = []
            for block in result.content:
                block_type = getattr(block, 'type', None)
                
                if block_type == 'text' or hasattr(block, 'text'):
                    parts.append(block.text)
                elif block_type == 'image':
                    mime = getattr(block, 'mimeType', 'image/png')
                    parts.append(f"[Image: {mime}]")
                elif block_type == 'audio':
                    mime = getattr(block, 'mimeType', 'audio/wav')
                    parts.append(f"[Audio: {mime}]")
                elif block_type == 'resource':
                    uri = getattr(block, 'uri', 'unknown')
                    parts.append(f"[Resource: {uri}]")
                elif isinstance(block, dict):
                    parts.append(json.dumps(block, indent=2))
                else:
                    parts.append(str(block))
            
            return "\n".join(parts)
        
        # Handle direct string
        if isinstance(result, str):
            return result
        
        # Handle dict/object
        if isinstance(result, dict):
            return json.dumps(result, indent=2)
        
        return str(result)
```

### 3.5 `manager.py` - Multi-Server Manager

Coordinates connections to multiple MCP servers.

```python
from typing import Optional
from .config import load_mcp_config
from .client import MCPClient
from .tool_proxy import MCPToolProxy
from .async_bridge import mcp_async_bridge


class MCPManager:
    """Manages connections to multiple MCP servers."""
    
    def __init__(self):
        self._config = None
        self._clients: dict[str, MCPClient] = {}
    
    def has_servers_configured(self) -> bool:
        """Check if any MCP servers are configured (doesn't check enabled)."""
        if self._config is None:
            self._config = load_mcp_config()
        return len(self._config.mcpServers) > 0
    
    def connect_all(self) -> None:
        """Connect to all configured servers. Logs errors but doesn't fail."""
        if self._config is None:
            self._config = load_mcp_config()
        
        for name, server_config in self._config.mcpServers.items():
            if server_config.disabled:
                continue
            
            try:
                client = MCPClient(name, server_config)
                client.connect()
                self._clients[name] = client
            except Exception as e:
                # Log but continue - graceful degradation
                from wichy.console.user import console
                console.error(f"Failed to connect to MCP server '{name}': {e}")
    
    def disconnect_all(self) -> None:
        """Disconnect from all servers."""
        for client in self._clients.values():
            try:
                client.disconnect()
            except Exception:
                pass  # Best effort
        self._clients.clear()
    
    def discover_all_tools(self, existing_tool_names: set[str]) -> list["MCPToolProxy"]:
        """
        Discover tools from all connected servers.
        
        Args:
            existing_tool_names: Names of native tools (for collision detection)
        
        Returns:
            List of MCPToolProxy instances (excludes colliding tools)
        """
        tools = []
        
        for server_name, client in self._clients.items():
            try:
                server_tools = client.list_tools()
                
                for tool_def in server_tools:
                    proxy = MCPToolProxy(
                        server_name=server_name,
                        client=client,
                        tool_definition=tool_def
                    )
                    
                    # Check for collision with native tools
                    if proxy.name in existing_tool_names:
                        from wichy.console.user import console
                        console.warn(
                            f"MCP tool '{proxy.name}' collides with native tool, skipping"
                        )
                        continue
                    
                    tools.append(proxy)
                    
            except Exception as e:
                from wichy.console.user import console
                console.error(f"Failed to discover tools from '{server_name}': {e}")
        
        return tools
    
    def connect_and_discover(self, existing_tool_names: set[str]) -> list["MCPToolProxy"]:
        """Convenience: connect all servers and discover tools."""
        self.connect_all()
        return self.discover_all_tools(existing_tool_names)


# Singleton
_manager: MCPManager | None = None


def get_mcp_manager() -> MCPManager:
    """Get the global MCP manager instance."""
    global _manager
    if _manager is None:
        _manager = MCPManager()
    return _manager
```

### 3.6 `tool_proxy.py` - Tool Bridge

Bridges MCP tools to wichy's BaseTool interface.

```python
from pydantic import Field, create_model
from typing import Any

from wichy.tools.base import BaseTool, ParametersModel
from .client import MCPClient
from .async_bridge import mcp_async_bridge


class MCPToolProxy(BaseTool):
    """
    Proxies a tool from an MCP server to wichy's tool system.
    
    Extends BaseTool so MCP tools get:
    - Hook system (pre/post tool hooks)
    - Result offloading (if enabled)
    - Consistent error handling
    """
    
    # Class-level defaults (instance attributes will shadow)
    description = "MCP tool"
    description_long = None
    enable_result_offload = False
    
    def __init__(
        self,
        server_name: str,
        client: MCPClient,
        tool_definition: dict
    ):
        self._server_name = server_name
        self._client = client
        self._tool_name = tool_definition["name"]
        self._input_schema = tool_definition.get("inputSchema", {})
        
        # Instance attributes (shadow class defaults)
        self.name = f"{server_name}_{tool_definition['name']}"
        self.description = tool_definition.get("description", f"Tool from {server_name}")
        self.description_long = self.description
        
        # Create parameters model from JSON Schema
        self.parameters_model = self._create_parameters_model()
    
    def _create_parameters_model(self) -> type[ParametersModel]:
        """
        Create a Pydantic model from the tool's JSON Schema.
        
        Falls back to dict parameter if schema is too complex.
        """
        schema = self._input_schema
        
        # Check for unsupported patterns
        if '$ref' in str(schema) or 'anyOf' in schema or 'oneOf' in schema:
            from wichy.console.user import console
            console.warn(
                f"Complex JSON Schema in '{self.name}', using generic parameters"
            )
            return self._fallback_model()
        
        try:
            return self._build_model_from_schema(schema)
        except Exception as e:
            from wichy.console.user import console
            console.warn(f"Schema conversion failed for '{self.name}': {e}")
            return self._fallback_model()
    
    def _build_model_from_schema(self, schema: dict) -> type[ParametersModel]:
        """Build Pydantic model from simple JSON Schema."""
        properties = schema.get("properties", {})
        required = set(schema.get("required", []))
        
        fields = {}
        annotations = {}
        
        for prop_name, prop_schema in properties.items():
            py_type = self._json_type_to_python(prop_schema)
            description = prop_schema.get("description")
            
            if prop_name in required:
                fields[prop_name] = (py_type, Field(..., description=description))
            else:
                default = prop_schema.get("default", None)
                fields[prop_name] = (py_type | None, Field(default=default, description=description))
        
        return create_model(
            f"{self.name}_Parameters",
            __base__=ParametersModel,
            **fields
        )
    
    def _fallback_model(self) -> type[ParametersModel]:
        """Fallback model for complex schemas."""
        return create_model(
            f"{self.name}_Parameters",
            __base__=ParametersModel,
            arguments=(dict, Field(default={}, description="Tool arguments (JSON)")),
        )
    
    def _json_type_to_python(self, schema: dict) -> type:
        """Map JSON Schema types to Python types."""
        type_str = schema.get("type", "string")
        
        type_map = {
            "string": str,
            "integer": int,
            "number": float,
            "boolean": bool,
            "array": list,
            "object": dict,
        }
        
        return type_map.get(type_str, str)
    
    def execute(self, **kwargs) -> str:
        """Execute the MCP tool. Returns result as string."""
        return self._client.call_tool(self._tool_name, kwargs)
    
    def info(self) -> str:
        """Return tool info for logging."""
        return f"[MCP] {self.name}"
```

### 3.7 `__init__.py` - Public API

```python
"""
MCP (Model Context Protocol) integration for wichy.

Allows wichy to act as an MCP Host, connecting to MCP servers
and using their tools as if they were native wichy tools.
"""

from .manager import get_mcp_manager, MCPManager
from .tool_proxy import MCPToolProxy
from .async_bridge import mcp_async_bridge
from .errors import (
    MCPError,
    MCPConfigError,
    MCPConnectionError,
    MCPToolExecutionError,
    MCPTimeoutError,
)

__all__ = [
    "get_mcp_manager",
    "MCPManager",
    "MCPToolProxy",
    "mcp_async_bridge",
    "discover_mcp_tools",
    "shutdown_mcp",
    "MCPError",
    "MCPConfigError",
    "MCPConnectionError",
    "MCPToolExecutionError",
    "MCPTimeoutError",
]


def discover_mcp_tools(existing_tool_names: set[str]) -> list:
    """
    Main entry point for MCP tool discovery.
    
    Called from __main__.py during startup.
    
    Args:
        existing_tool_names: Set of native tool names (for collision detection)
    
    Returns:
        List of MCPToolProxy instances
    """
    manager = get_mcp_manager()
    
    if not manager.has_servers_configured():
        return []
    
    try:
        return manager.connect_and_discover(existing_tool_names)
    except Exception as e:
        from wichy.console.user import console
        console.error(f"MCP discovery failed: {e}")
        return []


def shutdown_mcp() -> None:
    """Cleanup MCP connections. Called on exit."""
    manager = get_mcp_manager()
    manager.disconnect_all()
    mcp_async_bridge.shutdown()
```

---

## 4. Async Bridge

The async bridge is critical for making async fastmcp work with sync wichy. Key design decisions:

### 4.1 Why a Bridge is Needed

- fastmcp.Client is async (requires event loop)
- wichy's tool execution is sync (`execute(**kwargs) -> str`)
- Cannot just use `asyncio.run()` - would create new loop each call

### 4.2 Pattern from BrowserManager

The bridge follows the proven pattern from `helpers/browser.py`:

1. **Create event loop in daemon thread** - Runs forever until shutdown
2. **Use `run_coroutine_threadsafe()`** - Schedule work on the loop from any thread
3. **Poll until running** - Wait for loop to actually start before returning
4. **Thread lock for serialization** - Prevent concurrent access issues

### 4.3 Why Separate from BrowserManager

- **Isolation:** MCP failures shouldn't affect browser functionality
- **Independence:** Can enable/disable MCP without browser
- **Future flexibility:** May want different pool sizes, timeouts, etc.

The resource overhead is minimal (one daemon thread, one event loop).

---

## 5. Integration Points

### 5.1 `__main__.py` Integration

```python
# After line 255 (after initialize_tools), add:

# MCP tool discovery
from wichy.mcp import discover_mcp_tools, shutdown_mcp
import atexit

try:
    # Get native tool names for collision detection
    native_tool_names = {t.name for t in in_tools}
    
    mcp_tools = discover_mcp_tools(native_tool_names)
    
    if mcp_tools:
        console.info(f"Discovered {len(mcp_tools)} MCP tools")
    
    # Apply same filtering as native tools
    if args.tools:
        allowed = set(args.tools.split(",")) if args.tools else None
        if allowed:
            mcp_tools = [t for t in mcp_tools if t.name in allowed]
    
    if args.not_tools:
        excluded = set(args.not_tools.split(",")) if args.not_tools else None
        if excluded:
            mcp_tools = [t for t in mcp_tools if t.name not in excluded]
    
    # Merge with native tools
    in_tools = in_tools + mcp_tools
    
except Exception as e:
    console.error(f"MCP integration failed: {e}")

# Register cleanup on exit
atexit.register(shutdown_mcp)
```

### 5.2 `pyproject.toml` - Add Dependency

```toml
dependencies = [
    # ... existing dependencies ...
    "fastmcp>=2.0.0",
]
```

### 5.3 `config/settings.py` - Optional Settings

```python
class Settings(BaseSettings):
    # ... existing settings ...
    
    # MCP Configuration
    mcp_enabled: bool = True
    mcp_connection_timeout: int = 10  # seconds
    mcp_tool_timeout: int = 60  # seconds
```

---

## 6. Error Handling

### 6.1 Error Flow

```
MCP Server Error
      │
      ▼
fastmcp raises exception OR returns error result
      │
      ▼
MCPClient.call_tool() catches and returns error string
      │
      ▼
MCPToolProxy.execute() returns error string
      │
      ▼
Agent sees: "[MCP Error] server_name/tool_name: error message"
```

### 6.2 Graceful Degradation

| Scenario | Behavior |
|----------|----------|
| No config file | No MCP tools loaded, wichy starts normally |
| Invalid config JSON | Log error, no MCP tools |
| Server connection fails | Log error, skip that server, continue |
| Tool discovery fails | Log error, skip that server's tools |
| Tool execution fails | Return error string to agent (like native tools) |
| Timeout | Return timeout error string to agent |

### 6.3 Startup Errors vs Runtime Errors

**Startup (connection failure):** Log and continue. Agent can inform user that MCP server is unavailable.

**Runtime (tool execution failure):** Return error string. Agent can try alternative approach or inform user.

---

## 7. Known Limitations

### 7.1 JSON Schema Conversion

Simple schemas are handled. Complex patterns fall back to generic dict:

| Supported | Not Supported (Fallback) |
|-----------|-------------------------|
| `string`, `integer`, `number`, `boolean` | `$ref` references |
| `array` with primitive items | `anyOf` / `oneOf` unions |
| `object` with flat properties | Nested objects with `$ref` |
| `required` fields | Custom formats (`date-time`, `email`) |
| `default` values | `pattern` regex |
| `enum` arrays | Recursive schemas |

**Fallback behavior:** Tool accepts `arguments: dict` parameter.

### 7.2 Connection Management

V1 does not implement reconnection. If an MCP server dies mid-session:

- Subsequent tool calls return error string
- User must restart wichy to reconnect

**Future enhancement:** Auto-reconnect with health checks.

### 7.3 Tool Namespacing

Format: `{server_name}_{tool_name}`

If a user renames a server in config, tool names change. No stable identifiers in V1.

### 7.4 Resources and Prompts

V1 only implements MCP **Tools**. Resources and Prompts are not supported.

---

## 8. Implementation Steps

### Step 1: Pre-Implementation Verification

Before writing code:

1. [ ] `pip install fastmcp`
2. [ ] Verify imports: `from fastmcp import Client`
3. [ ] Verify transports: `from fastmcp.client.transports import StdioTransport, StreamableHttpTransport`
4. [ ] Create a simple MCP server for testing
5. [ ] Test basic connection and tool listing

### Step 2: Create Module Structure

```bash
mkdir -p src/wichy/mcp
touch src/wichy/mcp/__init__.py
touch src/wichy/mcp/errors.py
touch src/wichy/mcp/config.py
touch src/wichy/mcp/async_bridge.py
touch src/wichy/mcp/client.py
touch src/wichy/mcp/manager.py
touch src/wichy/mcp/tool_proxy.py
```

### Step 3: Implement in Order

1. `errors.py` - Exception classes
2. `config.py` - Configuration loading
3. `async_bridge.py` - Sync/async bridge
4. `client.py` - Single server client
5. `tool_proxy.py` - Tool bridge
6. `manager.py` - Multi-server manager
7. `__init__.py` - Public API

### Step 4: Integrate

1. Add `fastmcp` to `pyproject.toml`
2. Modify `__main__.py` with MCP discovery
3. Add `atexit.register(shutdown_mcp)` for cleanup

### Step 5: Test

1. Create test MCP server
2. Create test config `~/.wichy/mcp_servers.json`
3. Start wichy, verify MCP tools appear
4. Test tool execution
5. Test error handling (disconnect, timeout)

---

## 9. Testing Strategy

### 9.1 Test MCP Server (for development)

Create `test_mcp_server.py`:

```python
from fastmcp import FastMCP

mcp = FastMCP(name="Test Server")

@mcp.tool
def echo(message: str) -> str:
    """Echo back the message."""
    return f"Echo: {message}"

@mcp.tool
def add(a: int, b: int) -> int:
    """Add two numbers."""
    return a + b

@mcp.tool
def complex_args(name: str, count: int = 1, active: bool = False) -> dict:
    """Test with multiple argument types."""
    return {"name": name, "count": count, "active": active}

if __name__ == "__main__":
    mcp.run()
```

### 9.2 Test Config

```json
{
  "mcpServers": {
    "test": {
      "transport": "stdio",
      "command": "python",
      "args": ["/path/to/test_mcp_server.py"]
    }
  }
}
```

### 9.3 Test Cases

| Test | Expected |
|------|----------|
| Start wichy, list tools | See `test_echo`, `test_add` in tool list |
| Call `test_echo` with "hello" | Returns "Echo: hello" |
| Call `test_add` with 3, 5 | Returns 8 |
| Disconnect server, call tool | Returns error string |
| Invalid config JSON | wichy starts normally, logs error |
| Tool name collision with native | Warning logged, tool skipped |

---

## Appendix A: User Documentation

### Quick Start

1. Create `~/.wichy/mcp_servers.json`:

```json
{
  "mcpServers": {
    "weather": {
      "transport": "stdio",
      "command": "python",
      "args": ["/path/to/weather_server.py"]
    }
  }
}
```

2. Start wichy. MCP tools are automatically discovered.

3. Use tools like any other:

```
> What's the weather in Tokyo?
[Uses weather_get_forecast tool automatically]
```

### Configuration Examples

**Using uvx (npm-like for Python):**
```json
{
  "github": {
    "transport": "stdio",
    "command": "uvx",
    "args": ["mcp-server-github"],
    "env": {
      "GITHUB_TOKEN": "${GITHUB_TOKEN}"
    }
  }
}
```

**Using HTTP server:**
```json
{
  "api": {
    "transport": "http",
    "url": "http://localhost:3000/mcp",
    "headers": {
      "Authorization": "Bearer ${API_KEY}"
    }
  }
}
```

**Disabling a server:**
```json
{
  "weather": {
    "transport": "stdio",
    "command": "python",
    "args": ["./weather.py"],
    "disabled": true
  }
}
```