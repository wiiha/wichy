# Wichy Tool Analysis: An Agentic LLM Framework

## Executive Summary

**Wichy** is a sophisticated agentic LLM framework designed for local execution, following Simon Willison's definition: *"An LLM agent runs tools in a loop to achieve a goal."* The system provides a modular architecture with specialized agents, comprehensive tools, and a sophisticated artifact system for persistent knowledge sharing across conversations.

## Core Architecture

### 1. Agent System
Wichy employs a hierarchical agent architecture:

- **Root Agent**: Primary interactive agent maintaining conversation context and coordinating task execution
- **Sub Agents**: Specialized agents for specific domains:
  - `agent-code-planner`: Strategic planning for code changes (creates plans, never implements)
  - `agent-code-implementer`: Tactical code implementation (executes plans)
  - `agent-code-reviewer`: Code quality and security review
  - `agent-web-researcher`: Comprehensive web research
  - `agent-web-researcher-lite`: Quick information gathering

### 2. Tool System
A comprehensive suite of tools that agents can utilize:

- **File Operations**: `cat`, `write_file`, `grep`, `bash`, `tree`, `ls`
- **Web Research**: `web_search`, `web_fetch`
- **Artifact Management**: `artifact_create`, `artifact_search`
- **System Tools**: `todo`, `ping`, `reverse_dns_tool`

All tools feature Pydantic-based parameter validation, safe execution with error handling, and human verification for sensitive operations.

### 3. Context Management
- Interactive conversation history with reset capabilities
- Context summarization for long conversations
- Slash commands for user control:
  - `/context reset`: Clear context completely
  - `/context reset_by_summary`: Create summary before reset
  - `/logging on/off`: Toggle logging visibility
  - `/artifacts list`: View available artifacts

## The Artifact System: Core Innovation

The artifact system is Wichy's most sophisticated component, providing persistent knowledge storage and retrieval capabilities.

### Core Components

#### 1. Artifact Model (`artifact.py`)
- **Immutable Design**: Artifacts are frozen Pydantic models ensuring data integrity
- **Structured Types**: Four supported types:
  - `plan`: Implementation plans and strategies
  - `research`: Research findings and analysis
  - `analysis`: Data analysis and insights
  - `raw_data`: Raw data and content
- **Version Control**: Built-in versioning with `replaced_by` chaining
- **Rich Metadata**: Supports custom metadata for tracking related files, confidence, etc.

#### 2. Artifact Store (`store.py`)
- **Version Management**: Handles artifact versioning and supersession chains
- **Intelligent Deduplication**: Uses LLM-based similarity detection to prevent duplicates
- **Multiple Retrieval Methods**:
  - `get(id)`: Retrieve specific artifact by ID
  - `get_latest(id)`: Get latest version in a chain
  - `all_latest()`: Get all current (unreplaced) artifacts
  - `artifacts_for_prompt()`: Select relevant artifacts for context
  - `artifacts_for_query()`: Search artifacts by query

#### 3. Storage Backend (`store_backend.py`)
- **SQLite Implementation**: Persistent storage with WAL mode for performance
- **Session Isolation**: Artifacts organized by session ID
- **Efficient Indexing**: Optimized queries with proper indexing

#### 4. Artifact Tools (`tools.py`)
Three main tools for artifact interaction:
- **NewArtifactTool**: Create new artifacts with automatic versioning
- **ArtifactByIDTool**: Retrieve specific artifacts by ID
- **ArtifactByQueryTool**: Search and filter artifacts by relevance

#### 5. LLM Integration (`helpers.py`)
- **Similarity Detection**: Uses LLM to determine if new artifacts are versions of existing ones
- **Context-Aware Selection**: Selects relevant artifacts based on prompt context
- **Error Recovery**: Handles mistyped IDs and missing artifacts gracefully

### Key Features

#### Version Control System
- Automatic versioning when similar artifacts are detected
- Chain linking via `replaced_by` field
- Latest version retrieval with chain traversal
- Semantic similarity using Levenshtein and Jaccard algorithms

#### Intelligent Deduplication
- Multi-stage similarity detection:
  1. Algorithmic pre-filtering (Levenshtein + Jaccard)
  2. LLM-based final decision with confidence scoring
  3. Human-readable motivation for version decisions

#### Context-Aware Retrieval
- **Prompt-Based Selection**: Selects artifacts relevant to current task
- **Query-Based Search**: Finds artifacts containing specific information
- **Recipient-Aware**: Considers the intended recipient for relevance
- **Precision-Focused**: Highly selective to avoid irrelevant artifacts

## Technical Implementation

### File Structure
```
src/wichy/
├── artifact/                 # Artifact system
│   ├── artifact.py          # Core artifact models
│   ├── store.py             # Storage and retrieval logic
│   ├── store_backend.py     # SQLite backend
│   ├── tools.py             # Artifact tools
│   └── helpers.py           # LLM integration helpers
├── agents/                  # Agent system
│   ├── root_agent.py        # Main coordinator
│   ├── sub_agent.py         # Base for sub-agents
│   ├── task_agent.py        # Task delegation
│   └── agent_desc_*.py      # Agent definitions
├── tools/                   # Tool implementations
├── helpers/                 # Utility functions
└── __main__.py             # Entry point
```

### Key Design Patterns
- **Immutable Data**: Artifacts use frozen Pydantic models
- **Context Management**: Rich conversation history with system prompts
- **Tool Integration**: Consistent tool interface with Pydantic validation
- **Error Handling**: Comprehensive error handling and recovery mechanisms

## Integration and Workflow

### Agent Workflow
1. **User Input** → RootAgent receives and processes user requests
2. **Agent Selection** → RootAgent selects appropriate sub-agents based on task
3. **Tool Execution** → Selected agents use tools including artifact management
4. **Artifact Creation** → Agents create artifacts to store knowledge and findings
5. **Context Injection** → Relevant artifacts automatically injected into future prompts
6. **Response Generation** → Agents provide responses with accumulated context

### Session Management
- **Session ID**: Unique identifier for each conversation session
- **Isolated Storage**: Artifacts separated by session to prevent cross-contamination
- **Cleanup**: Sessions can be reset or summarized as needed

### LLM Backend Support
- **Multiple Backends**: Supports Ollama, Llama.cpp, and OpenRouter
- **Local-First**: Designed for local execution with privacy
- **Configurable**: Easy to switch between different models and backends

## Usage Examples

### Basic Usage
```bash
# Start Wichy with default model
python -m wichy

# Specify a model
python -m wichy --model ollama/hf.co/unsloth/Qwen3-4B-Instruct-2507-GGF:Q4_K_M

# List available tools
python -m wichy --list-tools

# Limit available tools
python -m wichy --tools cat,bash,grep
```

### Artifact Creation
Agents automatically create artifacts when they generate knowledge:
- **Code Planning**: Code planner creates implementation plans
- **Research**: Web researcher creates research findings
- **Analysis**: Code reviewer creates analysis reports

### Artifact Retrieval
```bash
# View available artifacts
/artifacts list

# Artifacts are automatically injected into relevant prompts
# Search functionality available through artifact_search tool
```

## Key Innovations

### 1. Persistent Knowledge Sharing
The artifact system enables knowledge to persist across conversations without direct communication between agents, creating a form of collective memory.

### 2. Intelligent Version Control
Unlike simple key-value stores, Wichy's artifact system understands semantic relationships between knowledge pieces, automatically managing versions and preventing duplicates.

### 3. Context-Aware Retrieval
The system doesn't just store knowledge - it understands which knowledge is relevant to specific tasks and recipients, providing highly targeted context injection.

### 4. Local Privacy Focus
All processing happens locally, ensuring user privacy and control over data while maintaining sophisticated functionality.

## Testing and Quality Assurance

The framework includes comprehensive testing:
- **Unit Tests**: Complete test coverage for artifact system
- **Integration Tests**: Testing of artifact creation and retrieval
- **Error Handling**: Tests for edge cases and error conditions
- **Mock LLM**: Simulated LLM responses for consistent testing

## Future Goals and Development

### Planned Enhancements
- **Vector-Based Search**: Moving from LLM-based to vector-based similarity detection
- **Enhanced Retrieval**: Improved algorithms for artifact relevance
- **Generic Sub-Agent**: Context isolation for parallel execution
- **Expanded Toolset**: Additional tools for specialized use cases

### Current Limitations
- **LLM Dependency**: Artifact similarity detection relies on LLM availability
- **Performance**: Large artifact collections may impact performance
- **Storage**: SQLite backend may need scaling for very large projects

## Philosophy and Principles

Wichy embodies several key principles:

1. **Local Execution**: All processing happens locally for privacy and control
2. **Modular Design**: Specialized components work together seamlessly
3. **Persistent Knowledge**: Artifacts enable context sharing without direct communication
4. **Autonomous Operation**: Agents make decisions based on context and capabilities
5. **Safety First**: Human verification for sensitive operations

## Getting Started

1. **Install dependencies**: `pip install -r requirements.txt`
2. **Set up LLM backend**: Configure Ollama, Llama.cpp, or OpenRouter
3. **Run Wichy**: `python -m wichy`
4. **Start interacting**: Begin chatting with the system

## Conclusion

Wichy represents a significant advancement in agentic LLM frameworks, particularly through its sophisticated artifact system. By combining persistent knowledge storage, intelligent version control, and context-aware retrieval, it creates a system that can maintain and build upon knowledge across complex, multi-step tasks. The local-first approach ensures privacy while maintaining powerful capabilities for knowledge management and autonomous task execution.

The artifact system is central to Wichy's ability to function as a true agentic framework, enabling knowledge persistence that transcends individual conversation turns and creates a foundation for increasingly sophisticated autonomous behavior.