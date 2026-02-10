# Wichy - Agentic LLM Framework

---

## About Wichy

Wichy is an **agentic LLM framework** designed for local execution, following Simon Willison's definition: "An LLM agent runs tools in a loop to achieve a goal." The system provides a modular architecture with specialized agents, comprehensive tools, and an artifact system for persistent knowledge sharing.

---

## Core Architecture

### 1. Agent System

Wichy features a hierarchical agent architecture with specialized root agent configurations:

- **Root Agent**: The primary interactive agent that maintains conversation context and coordinates task execution
- **Root Agent Configurations**:
  - `root-agent-basic`: Basic configuration with Ministral-3 model
  - `root-agent-code-advanced`: Advanced configuration optimized for software engineering tasks with enhanced capabilities

### 2. Tool System

A comprehensive suite of tools organized into logical categories:

#### Basic Tools

- **BashTool**: Command line execution with optional human verification
- **TodoTool**: Task management and planning

#### File System Tools

- **ListFilesTool**: Directory listing and file exploration
- **CatFileContentTool**: File content reading
- **WriteFileTool**: File writing and editing
- **SearchRecursiveTool**: Recursive file content search
- **TreeTool**: Directory tree visualization
- **GlobTool**: File pattern matching

#### Web Research Tools

- **SearchDDGTool**: DuckDuckGo web search
- **FetchWebPageTool**: Web page content fetching

#### Networking Tools

- **PingTool**: Network connectivity testing
- **ReverseDnsTool**: DNS resolution

#### Sub-Agent Tools

- **TaskAgentTool**: Multi-step task execution with autonomous agents

All tools feature:

- Pydantic-based parameter validation
- Safe execution with error handling
- Human verification for sensitive operations

### 3. Artifact System

A persistent knowledge storage system:

- **Immutable Artifacts**: Versioned knowledge objects with types (plan, research, analysis, raw_data)
- **Automatic Versioning**: New versions replace old ones with proper chaining
- **LLM-based Deduplication**: Intelligent similarity detection to avoid duplicates
- **Context Injection**: Automatic artifact retrieval and injection based on task relevance

### 4. Context Management

- Interactive conversation history with reset capabilities
- Context summarization for long conversations
- Slash commands for user control:
  - `/context reset`: Clear context completely
  - `/context reset_by_summary`: Create summary before reset
  - `/logging on/off`: Toggle logging visibility
  - `/artifacts list`: View available artifacts

---

## Technical Implementation

### LLM Backend Support

Wichy supports multiple LLM backends:

- **Ollama**: Local model execution
- **Llama.cpp**: Local inference server
- **OpenRouter**: Cloud-based model access

### Key Features

1. **Autonomous Decision Making**: RootAgent automatically selects appropriate agents and tools
2. **Modular Design**: Clear separation between agents, tools, and core systems
3. **Local-First**: Designed for local execution with privacy and control
4. **Extensible**: Easy to add new agents, tools, and artifact types
5. **Safe Execution**: Human verification for sensitive operations

### Workflow Example

```
User Input → RootAgent → Tool Execution → Response
```

---

## Usage

### Basic Usage

```bash
# Start Wichy with default model
python -m wichy

# Specify a model
python -m wichy --model-str ollama/hf.co/unsloth/Qwen3-4B-Instruct-2507-GGUF:Q4_K_M

# List available tools
python -m wichy --list-tools

# Limit available tools
python -m wichy --tools cat,bash,grep

# Allow all bash commands without human verification
python -m wichy --bash-allow-all
```

### Interactive Commands

- **Slash Commands**: Special commands starting with `/`
- **Normal Input**: Processed by RootAgent and appropriate tools
- **Context Management**: Reset or summarize conversation history

### Tool Filtering

```bash
# Include specific tools only
python -m wichy --tools cat,bash,grep

# Exclude specific tools
python -m wichy --not-tools bash,ping

# Combine inclusion and exclusion
python -m wichy --tools cat,bash,grep --not-tools bash
```

### Logging Options

```bash
# Show logs during execution
python -m wichy --show-log

# Show tool results (requires --show-log)
python -m wichy --show-log --log-tools
```

---

## Development

### Testing

Wichy includes comprehensive tests:

- Unit tests for core components
- Tool validation tests
- Artifact system tests

```bash
# Run tests
pytest tests/
```

### Extending Wichy

1. **Add New Root Agent Configurations**: Create markdown description files following existing patterns
2. **Add New Tools**: Implement BaseTool subclasses with Pydantic validation
3. **Add Tool Categories**: Extend the tool organization in tools/**init**.py

---

## Future Goals

- Expand toolset for additional use cases

---

## Philosophy

Wichy embodies several key principles:

1. **Local Execution**: All processing happens locally for privacy and control
2. **Modular Design**: Specialized components work together seamlessly
3. **Persistent Knowledge**: Artifacts enable context sharing without direct communication
4. **Autonomous Operation**: Agents make decisions based on context and capabilities
5. **Safety First**: Human verification for sensitive operations

---

## Getting Started

1. Install dependencies: `pip install -r requirements.txt`
2. Set up your LLM backend (Ollama, Llama.cpp, or OpenRouter)
3. Run Wichy: `python -m wichy`
4. Start interacting with the system

For more information, see the [README](README.md) and explore the codebase.
