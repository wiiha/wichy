# Result Hijacking Feature Specification

> **Goal**: Prevent context rot in root agents by offloading large tool results to disk and enabling on-demand querying via LLM-powered summarization.

---

## Table of Contents

1. [Overview](#overview)
2. [Settings Configuration](#1-settings-configuration)
3. [Result Store (SQLite)](#2-result-store-sqlite)
4. [result_or_ref Function](#3-result_or_ref-function)
5. [Query Result Tool](#4-query-result-tool)
6. [Integration Points](#5-integration-points)
7. [Examples](#6-examples)

---

## Overview

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                         CURRENT FLOW                                         │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│   Tool.execute() ──► validate_and_execute() ──► [post hooks] ──► return res  │
│                                                                              │
│   Problem: Large results (20k+ chars) consume context window                 │
│                                                                              │
└─────────────────────────────────────────────────────────────────────────────┘

                                ▼

┌─────────────────────────────────────────────────────────────────────────────┐
│                           NEW FLOW                                           │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│   Tool.execute() ──► validate_and_execute() ──► [post hooks]                 │
│          │                                                                   │
│          ▼                                                                   │
│   ┌─────────────────────────────────────────────────────────────┐           │
│   │ OFFLOAD CHECK (result_or_ref)                               │           │
│   │                                                              │           │
│   │   if result > THRESHOLD AND within_tolerance:               │           │
│   │       return result  # pass through                          │           │
│   │   elif result > THRESHOLD AND tool.offload_enabled:          │           │
│   │       ref_id = store.save(result, tool_name, input_args)     │           │
│   │       return format_offload_response(ref_id, result)         │           │
│   │   else:                                                     │           │
│   │       return result  # normal flow                           │           │
│   └─────────────────────────────────────────────────────────────┘           │
│                                                                              │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## Directory Structure

All new components live under `src/wichy/result_offload/`:

```
src/wichy/result_offload/
├── __init__.py        # Exports: get_result_store, result_or_ref, QueryResultTool
├── store.py           # ResultStore (SQLite-backed, thread-safe singleton)
├── hijack.py          # result_or_ref function + offload decision logic
├── query_tool.py      # QueryResultTool implementation
└── validation.py      # ValidationResult + validation loop logic
```

---

## 1. Settings Configuration

**File**: `src/wichy/config/settings.py`

Add offload configuration to the existing `Settings` class:

```python
class Settings(BaseSettings):
    # ... existing settings ...
    
    # -------------------------------------------------------------------------
    # Result Offload Configuration
    # -------------------------------------------------------------------------
    
    # Character threshold above which results may be offloaded
    # Default: 8000 chars
    result_offload_threshold: int = 8000
    
    # Tolerance percentage for pass-through
    # If result size <= threshold * (1 + tolerance), pass through normally
    # Default: 0.10 (10%) - so 8000 * 1.10 = 8800 chars
    result_offload_tolerance: float = 0.10
    
    # Preview character count for offloaded results
    # Included in the reference response so agents can see beginning
    # Default: 500 chars (max 1000)
    result_offload_preview_chars: int = 500
    
    # Time-to-live for stored results (hours)
    # Default: 24 hours
    result_offload_ttl_hours: int = 24
    
    # Maximum validation retries for summarizer
    # Default: 2
    result_offload_max_validation_retries: int = 2
```

**Environment variable overrides**:
```bash
WICHY_RESULT_OFFLOAD_THRESHOLD=8000
WICHY_RESULT_OFFLOAD_TOLERANCE=0.10
WICHY_RESULT_OFFLOAD_PREVIEW_CHARS=500
WICHY_RESULT_OFFLOAD_TTL_HOURS=24
WICHY_RESULT_OFFLOAD_MAX_VALIDATION_RETRIES=2
```

---

## 2. Result Store (SQLite)

**File**: `src/wichy/result_offload/store.py`

**Purpose**: Thread-safe singleton SQLite store for offloaded tool results.

### 2.1 Why SQLite?

- **Handles high volume**: Hundreds/thousands of results efficiently
- **Built-in concurrency**: SQLite handles locking internally
- **Single file**: Easier to manage than many small JSON files
- **Fast queries**: Indexed lookups and cleanup

**Storage location**: `.wichy/results.db`

### 2.2 Data Structures

```python
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional, Dict, Any, List
from pathlib import Path
import sqlite3
import threading
from contextlib import contextmanager
from uuid import uuid4


@dataclass
class StoredResult:
    """A stored tool result with metadata."""
    ref_id: str
    content: str
    tool_name: str
    input_args: Dict[str, Any]
    char_count: int
    created_at: datetime
    expires_at: datetime
    model_str: Optional[str] = None  # Which model triggered this (for debugging)
```

### 2.3 ResultStore Implementation

```python
# Module-level singleton
_instance: Optional["ResultStore"] = None
_lock = threading.Lock()


def get_result_store() -> "ResultStore":
    """Get the singleton ResultStore instance."""
    global _instance
    with _lock:
        if _instance is None:
            _instance = ResultStore()
    return _instance


class ResultStore:
    """
    Thread-safe SQLite-backed store for offloaded tool results.
    
    All agents share the same store instance, allowing ref IDs to be
    passed between agents (e.g., from task agent to root agent).
    
    Storage location: .wichy/results.db
    
    Thread safety: Uses connection-per-operation pattern with WAL mode
    for concurrent reads. A write lock ensures serialized writes.
    """
    
    # Class-level lock for write operations
    _write_lock = threading.Lock()
    
    def __init__(self, wichy_dir: Optional[Path] = None):
        """Initialize the SQLite store."""
        from wichy.config import settings
        
        if wichy_dir is None:
            wichy_dir = Path.cwd() / ".wichy"
        
        # Ensure directory exists before creating database
        wichy_dir.mkdir(parents=True, exist_ok=True)
        
        self.db_path = wichy_dir / "results.db"
        self._init_db()
    
    @contextmanager
    def _get_conn(self, for_write: bool = False) -> sqlite3.Connection:
        """
        Create a new database connection with WAL mode.
        
        Uses connection-per-operation pattern (not connection pooling).
        Each call creates a fresh connection that's closed after use.
        WAL mode allows concurrent reads while writes are serialized.
        
        Args:
            for_write: If True, acquire the write lock before connecting.
                       This ensures only one writer at a time.
        
        Yields:
            sqlite3.Connection: A fresh database connection.
        """
        if for_write:
            # Acquire write lock to serialize writes
            self._write_lock.acquire()
        
        try:
            conn = sqlite3.connect(str(self.db_path))
            conn.execute("PRAGMA journal_mode=WAL")
            conn.row_factory = sqlite3.Row
            yield conn
        finally:
            if for_write:
                self._write_lock.release()
    
    def _init_db(self) -> None:
        """Initialize the database schema."""
        with self._get_conn() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS results (
                    ref_id TEXT PRIMARY KEY,
                    content TEXT NOT NULL,
                    tool_name TEXT,
                    input_args TEXT,
                    char_count INTEGER,
                    created_at TEXT,
                    expires_at TEXT,
                    model_str TEXT
                )
            """)
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_expires_at ON results(expires_at)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_tool_name ON results(tool_name)"
            )
            conn.commit()
    
    def _generate_ref_id(self) -> str:
        """Generate a unique reference ID."""
        return f"res_{uuid4().hex[:12]}"
    
    def _get_ttl_hours(self) -> int:
        """Get TTL from settings."""
        from wichy.config import settings
        return settings.result_offload_ttl_hours
    
    # -------------------------------------------------------------------------
    # Core Operations
    # -------------------------------------------------------------------------
    
    def save(
        self,
        content: str,
        tool_name: str,
        input_args: Dict[str, Any],
        model_str: Optional[str] = None,
    ) -> str:
        """
        Store a result and return its reference ID.
        
        Args:
            content: The tool result content
            tool_name: Name of the tool that produced this result
            input_args: The input arguments to the tool
            model_str: Optional model string (for debugging)
            
        Returns:
            ref_id: Unique reference ID for later retrieval
        """
        import json
        
        ref_id = self._generate_ref_id()
        now = datetime.utcnow()
        expires_at = now + timedelta(hours=self._get_ttl_hours())
        
        with self._get_conn(for_write=True) as conn:
            conn.execute(
                """
                INSERT INTO results (ref_id, content, tool_name, input_args, 
                                     char_count, created_at, expires_at, model_str)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    ref_id,
                    content,
                    tool_name,
                    json.dumps(input_args),
                    len(content),
                    now.isoformat(),
                    expires_at.isoformat(),
                    model_str,
                ),
            )
            conn.commit()
        
        return ref_id
    
    def load(self, ref_id: str) -> Optional[StoredResult]:
        """
        Load a stored result by reference ID.
        
        Args:
            ref_id: The reference ID
            
        Returns:
            StoredResult if found and not expired, None otherwise
        """
        import json
        
        with self._get_conn() as conn:
            cursor = conn.execute(
                "SELECT * FROM results WHERE ref_id = ?",
                (ref_id,),
            )
            row = cursor.fetchone()
            
            if row is None:
                return None
            
            # Check expiration
            expires_at = datetime.fromisoformat(row["expires_at"])
            if datetime.utcnow() > expires_at:
                self.delete(ref_id)
                return None
            
            return StoredResult(
                ref_id=row["ref_id"],
                content=row["content"],
                tool_name=row["tool_name"],
                input_args=json.loads(row["input_args"]),
                char_count=row["char_count"],
                created_at=datetime.fromisoformat(row["created_at"]),
                expires_at=expires_at,
                model_str=row["model_str"],
            )
    
    def delete(self, ref_id: str) -> bool:
        """
        Delete a stored result.
        
        Args:
            ref_id: The reference ID
            
        Returns:
            True if deleted, False if not found
        """
        with self._get_conn(for_write=True) as conn:
            cursor = conn.execute(
                "DELETE FROM results WHERE ref_id = ?",
                (ref_id,),
            )
            conn.commit()
            return cursor.rowcount > 0
    
    def cleanup_expired(self) -> int:
        """
        Remove all expired results.
        
        Returns:
            Number of expired results removed
        """
        with self._get_conn(for_write=True) as conn:
            cursor = conn.execute(
                "DELETE FROM results WHERE expires_at < ?",
                (datetime.utcnow().isoformat(),),
            )
            conn.commit()
            return cursor.rowcount
    
    def list_refs(self) -> List[Dict[str, Any]]:
        """
        List all stored result references (without content).
        
        Returns:
            List of dicts with ref_id, tool_name, char_count, created_at, expires_at
        """
        with self._get_conn() as conn:
            cursor = conn.execute(
                """
                SELECT ref_id, tool_name, char_count, created_at, expires_at
                FROM results
                ORDER BY created_at DESC
                """
            )
            rows = cursor.fetchall()
            
            return [
                {
                    "ref_id": row["ref_id"],
                    "tool_name": row["tool_name"],
                    "char_count": row["char_count"],
                    "created_at": row["created_at"],
                    "expires_at": row["expires_at"],
                }
                for row in rows
            ]
```

### 2.4 Thread Safety Notes

- **Module-level singleton**: `get_result_store()` uses a module-level lock for initialization
- **Connection-per-operation**: Each operation creates a fresh connection, closed after use
- **WAL mode**: SQLite Write-Ahead Logging allows concurrent reads
- **Write lock**: Class-level `_write_lock` serializes write operations (INSERT, DELETE)
- **Read operations**: No lock needed - WAL mode handles concurrent reads safely
- **Index on `expires_at`**: Fast cleanup queries

---

## 3. result_or_ref Function

**File**: `src/wichy/result_offload/hijack.py`

**Purpose**: Determine if a result should be offloaded and format the response accordingly.

### 3.1 Function Signature

```python
def result_or_ref(
    result: str,
    tool_name: str,
    input_args: Dict[str, Any],
    model_str: Optional[str] = None,
    enable_offload: bool = True,
) -> str:
    """
    Decide whether to offload a result and return either the original
    or a formatted reference response.
    
    Args:
        result: The tool result string
        tool_name: Name of the tool (for metadata)
        input_args: Input arguments to the tool (for metadata)
        model_str: Optional model string (for metadata)
        enable_offload: Whether offloading is enabled for this tool
        
    Returns:
        Either the original result (pass-through) or a formatted
        reference response with preview and instructions.
    """
```

### 3.2 Implementation

```python
from typing import Dict, Any, Optional
from wichy.config import settings
from wichy.result_offload.store import get_result_store


def result_or_ref(
    result: str,
    tool_name: str,
    input_args: Dict[str, Any],
    model_str: Optional[str] = None,
    enable_offload: bool = True,
) -> str:
    """
    Decide whether to offload a result and return either the original
    or a formatted reference response.
    
    The logic:
    1. Handle edge cases (None, non-string, empty) → pass through
    2. If offloading disabled for this tool → pass through
    3. If result size <= threshold → pass through
    4. If result size <= threshold * (1 + tolerance) → pass through
    5. Otherwise → offload (store + return reference)
    """
    # -------------------------------------------------------------------------
    # Handle edge cases
    # -------------------------------------------------------------------------
    # None result → return empty string
    if result is None:
        return ""
    
    # Non-string result → convert to string (e.g., bytes from read_file)
    if not isinstance(result, str):
        result = str(result)
    
    # Empty result → nothing to offload
    if not result:
        return result
    
    # -------------------------------------------------------------------------
    # Get settings
    # -------------------------------------------------------------------------
    threshold = settings.result_offload_threshold
    tolerance = settings.result_offload_tolerance
    preview_chars = settings.result_offload_preview_chars
    
    # Cap preview at 1000 chars
    preview_chars = min(preview_chars, 1000)
    
    result_len = len(result)
    
    # Check 1: Offloading disabled for this tool
    if not enable_offload:
        return result
    
    # Check 2: Result within threshold
    if result_len <= threshold:
        return result
    
    # Check 3: Result within tolerance (threshold * (1 + tolerance))
    tolerance_threshold = int(threshold * (1 + tolerance))
    if result_len <= tolerance_threshold:
        return result
    
    # Check 4: Offload!
    store = get_result_store()
    ref_id = store.save(
        content=result,
        tool_name=tool_name,
        input_args=input_args,
        model_str=model_str,
    )
    
    # Format reference response
    return _format_offload_response(
        ref_id=ref_id,
        result=result,
        tool_name=tool_name,
        preview_chars=preview_chars,
    )


def _format_offload_response(
    ref_id: str,
    result: str,
    tool_name: str,
    preview_chars: int,
) -> str:
    """
    Format the offload response with preview and instructions.
    
    Args:
        ref_id: The stored reference ID
        result: The full result (for preview)
        tool_name: Name of the tool (for context)
        preview_chars: Number of preview characters
        
    Returns:
        Formatted response string
    """
    result_len = len(result)
    
    # Create preview (first N chars)
    preview = result[:preview_chars]
    if len(result) > preview_chars:
        preview += "..."
    
    return f"""[RESULT_OFFLOADED]
Reference ID: {ref_id}
Tool: {tool_name}
Size: {result_len:,} characters

--- PREVIEW ({preview_chars} chars) ---
{preview}

--- END PREVIEW ---

This result was too large for the context window and has been stored.

To query this result, use the `query_result` tool:
  query_result(ref_ids=["{ref_id}"], query="your question here")

You can pass multiple reference IDs to query multiple stored results at once.
"""


def format_results_for_summarizer(results: list, query: str) -> str:
    """
    Format a system prompt for the summarizer LLM.
    
    This is used internally by the query_result tool.
    """
    formatted_results = "\n\n".join(
        f"""--- RESULT {i} ---
Reference ID: {r.ref_id}
Tool: {r.tool_name}
Size: {r.char_count:,} characters
Created: {r.created_at.isoformat()}

{r.content}"""
        for i, r in enumerate(results, 1)
    )
    
    return f"""You are analyzing stored tool results to answer a query.

## Stored Results

{formatted_results}

## Query

{query}

## Instructions

1. Analyze the stored results carefully
2. Answer the query using only information from the stored results
3. Be specific and reference relevant parts of the data
4. If the query cannot be answered from the data, say so clearly
5. Do not hallucinate or make up information

Provide your response below:
"""
```

### 3.3 Tolerance Logic Table

| Result Size | Threshold | Tolerance Threshold | Action |
|------------|-----------|-------------------|--------|
| 5,000 | 8000 | 8800 | Pass through (< threshold) |
| 8,000 | 8000 | 8800 | Pass through (== threshold) |
| 8,001 | 8000 | 8800 | Pass through (within tolerance) |
| 8,500 | 8000 | 8800 | Pass through (within tolerance) |
| 8,800 | 8000 | 8800 | Pass through (== tolerance threshold) |
| 8,801 | 8000 | 8800 | **OFFLOAD** (> tolerance threshold) |
| 45,000 | 8000 | 8800 | **OFFLOAD** |

---

## 4. Query Result Tool

**File**: `src/wichy/result_offload/query_tool.py`

**Purpose**: Allow agents to query stored results via LLM-powered summarization with validation.

### 4.1 Parameters Model

```python
from pydantic import Field
from typing import List
from wichy.tools.base import ParametersModel
from wichy.constants import HIDE_FROM_LLM_PREFIX


class QueryResultParameters(ParametersModel):
    """Parameters for the query_result tool."""
    
    ref_ids: List[str] = Field(
        description="List of reference IDs from offloaded results to query. "
                    "Can be a single ID or multiple IDs to query together."
    )
    
    query: str = Field(
        description="The question to answer using the stored result(s). "
                    "Be specific about what you're looking for."
    )
    
    max_tokens: int = Field(
        default=2000,
        description="Maximum response length in tokens. Default is 2000."
    )
    
    # Hidden parameter - injected by agent, not shown to LLM
    model_str: str = Field(
        default="",
        description=HIDE_FROM_LLM_PREFIX + "Model string from the calling agent"
    )
```

### 4.2 Tool Implementation

```python
from typing import Optional
from wichy.tools.base import BaseTool
from wichy.result_offload.store import get_result_store, StoredResult
from wichy.result_offload.hijack import format_results_for_summarizer, result_or_ref
from wichy.result_offload.validation import validate_summarizer_response
from wichy.llm_backend import call
from wichy.config import settings
from wichy.constants import ROLE_SYSTEM, ROLE_USER


class QueryResultTool(BaseTool):
    """Query stored tool results using natural language."""
    
    name = "query_result"
    description = "Query an offloaded tool result using natural language"
    description_long = """
Use this tool when you receive a [RESULT_OFFLOADED] reference.

Provide one or more reference IDs and your question about the data.
An LLM will analyze the stored result(s) and answer your question.

The response is validated by a separate LLM to ensure accuracy.
If validation fails, the query is retried with feedback.

Examples:
  query_result(ref_ids=["res_abc123"], query="What are the main functions?")
  query_result(ref_ids=["res_abc123", "res_def456"], query="Compare these results")
"""
    
    parameters_model = QueryResultParameters
    
    # This tool should never have its results offloaded
    enable_result_offload = False
    
    def execute(self, **kwargs) -> str:
        """Execute the query result tool."""
        params = self.parameters_model(**kwargs)
        
        ref_ids = params.ref_ids
        query = params.query
        max_tokens = params.max_tokens
        model_str = params.model_str
        
        # Validate model_str
        if not model_str:
            return "Error: model_str is required (internal error - should be injected)"
        
        # Load all requested results
        store = get_result_store()
        results: list[StoredResult] = []
        
        for ref_id in ref_ids:
            stored = store.load(ref_id)
            if stored is None:
                return f"Error: Result '{ref_id}' not found or has expired."
            results.append(stored)
        
        # Run summarizer + validator loop
        response = self._summarize_with_validation(
            results=results,
            query=query,
            model_str=model_str,
            max_tokens=max_tokens,
        )
        
        return response
    
    def _summarize_with_validation(
        self,
        results: list[StoredResult],
        query: str,
        model_str: str,
        max_tokens: int,
    ) -> str:
        """
        Run the summarize -> validate -> retry loop.
        
        Flow:
        1. Call summarizer with results + query
        2. Call validator with results + query + response
        3. If invalid, retry with feedback (max retries from settings)
        4. Return final response
        """
        max_retries = settings.result_offload_max_validation_retries
        
        # Initial summarization
        response = self._call_summarizer(
            results=results,
            query=query,
            model_str=model_str,
            max_tokens=max_tokens,
        )
        
        # Validation loop
        for attempt in range(max_retries + 1):
            validation = validate_summarizer_response(
                results=results,
                query=query,
                response=response,
                model_str=model_str,
            )
            
            if validation.is_valid:
                return response
            
            # Retry with feedback
            if attempt < max_retries:
                response = self._call_summarizer(
                    results=results,
                    query=query,
                    model_str=model_str,
                    max_tokens=max_tokens,
                    previous_feedback=validation.feedback,
                )
        
        # Max retries reached - return with warning
        return f"[Validation warning: {validation.feedback}]\n\n{response}"
    
    def _call_summarizer(
        self,
        results: list[StoredResult],
        query: str,
        model_str: str,
        max_tokens: int,
        previous_feedback: Optional[str] = None,
    ) -> str:
        """Call the summarizer LLM with error handling."""
        # Build prompt
        prompt = format_results_for_summarizer(results=results, query=query)
        
        if previous_feedback:
            prompt += f"""

Note: A previous attempt at answering was flagged as incomplete:
{previous_feedback}

Please address this feedback in your response.
"""
        
        # Build context for LLM call
        context = [
            {"role": ROLE_SYSTEM, "content": "You are a helpful assistant that analyzes data and answers questions accurately."},
            {"role": ROLE_USER, "content": prompt},
        ]
        
        # Call LLM with error handling
        try:
            llm_response = call(
                context=context,
                tool_defs=None,
                model_str=model_str,
                extra_args={"max_tokens": max_tokens},
            )
            
            if llm_response is None or llm_response.message is None:
                return f"Error: LLM returned no response for query '{query}'"
            
            return llm_response.message.content
            
        except Exception as e:
            return f"Error querying stored result: {e}"
```

### 4.3 Validation Module

**File**: `src/wichy/result_offload/validation.py`

```python
from dataclasses import dataclass
from typing import List
from wichy.result_offload.store import StoredResult
from wichy.result_offload.hijack import format_results_for_summarizer
from wichy.llm_backend import call
from wichy.constants import ROLE_SYSTEM, ROLE_USER


@dataclass
class ValidationResult:
    """Result of validating a summarizer response."""
    is_valid: bool
    feedback: str


def validate_summarizer_response(
    results: List[StoredResult],
    query: str,
    response: str,
    model_str: str,
) -> ValidationResult:
    """
    Validate a summarizer response against the data and query.
    
    Args:
        results: The stored results being queried
        query: The original query
        response: The summarizer's response to validate
        model_str: Model string for LLM call
        
    Returns:
        ValidationResult with is_valid and feedback
    """
    # Format results for validation prompt
    formatted_results = format_results_for_summarizer(results=results, query=query)
    
    validation_prompt = f"""You are a validator. Your job is to determine if a response reasonably answers a query given the available data.

{formatted_results}

## Query
{query}

## Response to Validate
{response}

## Task
Answer: Is this response reasonable given the data and query?

Consider:
1. Does the response address the query?
2. Is the response supported by the data?
3. Are there significant omissions or hallucinations?

Respond in this exact format:
VALID: <brief justification>
OR
INVALID: <specific feedback for improvement>
"""
    
    context = [
        {"role": ROLE_SYSTEM, "content": "You are a validator that ensures responses are accurate and complete."},
        {"role": ROLE_USER, "content": validation_prompt},
    ]
    
    # Call LLM with error handling
    try:
        llm_response = call(
            context=context,
            tool_defs=None,
            model_str=model_str,
            extra_args={"max_tokens": 500},
        )
        
        if llm_response is None or llm_response.message is None:
            # LLM call failed - treat as valid to avoid blocking
            return ValidationResult(
                is_valid=True,
                feedback="Validation skipped due to LLM error"
            )
        
        response_text = llm_response.message.content.strip()
        
    except Exception as e:
        # Error during validation - treat as valid to avoid blocking
        return ValidationResult(
            is_valid=True,
            feedback=f"Validation skipped due to error: {e}"
        )
    
    # Parse validation response
    if response_text.startswith("VALID:"):
        return ValidationResult(
            is_valid=True,
            feedback=response_text[6:].strip()
        )
    elif response_text.startswith("INVALID:"):
        return ValidationResult(
            is_valid=False,
            feedback=response_text[8:].strip()
        )
    else:
        # Ambiguous response - treat as valid with caution
        return ValidationResult(
            is_valid=True,
            feedback="Ambiguous validation response"
        )
```

### 4.4 Package Exports

**File**: `src/wichy/result_offload/__init__.py`

```python
"""
Result offloading system for preventing context rot.

This module provides:
- ResultStore: SQLite-backed storage for large tool results
- result_or_ref: Decision logic for offloading results
- QueryResultTool: Tool for querying stored results via LLM
"""

from wichy.result_offload.store import ResultStore, StoredResult, get_result_store
from wichy.result_offload.hijack import result_or_ref, _format_offload_response
from wichy.result_offload.query_tool import QueryResultTool
from wichy.result_offload.validation import ValidationResult, validate_summarizer_response

__all__ = [
    "ResultStore",
    "StoredResult",
    "get_result_store",
    "result_or_ref",
    "_format_offload_response",
    "QueryResultTool",
    "ValidationResult",
    "validate_summarizer_response",
]
```

### 4.5 Validation Flow Diagram

```
┌────────────────────────────────────────────────────────────────────┐
│                    QUERY_RESULT TOOL FLOW                          │
├────────────────────────────────────────────────────────────────────┤
│                                                                    │
│  1. Receive query(ref_ids=["res_123"], query="What is X?")         │
│                                                                    │
│  2. Load stored result(s) from ResultStore (SQLite)              │
│                                                                    │
│  3. Format results for summarizer                                  │
│                                                                    │
│  4. ┌──────────────────────────────────────────────────────────┐  │
│     │                 SUMMARIZE + VALIDATE LOOP                 │  │
│     │                                                           │  │
│     │   ┌───────────────┐     ┌───────────────┐                │  │
│     │   │   Summarizer  │────►│   Validator   │                │  │
│     │   │     LLM       │     │     LLM       │                │  │
│     │   └───────────────┘     └───────┬───────┘                │  │
│     │                                 │                        │  │
│     │                     ┌───────────┴───────────┐            │  │
│     │                     │                       │            │  │
│     │                   VALID                  INVALID         │  │
│     │                     │                       │            │  │
│     │                     ▼                       ▼            │  │
│     │              Return response        Retry with feedback   │  │
│     │                                        (max 2 retries)   │  │
│     └──────────────────────────────────────────────────────────┘  │
│                                                                    │
│  5. Return final response to agent                                 │
│                                                                    │
└────────────────────────────────────────────────────────────────────┘
```

---

## 5. Integration Points

### 5.1 BaseTool Modification

**File**: `src/wichy/tools/base.py`

Add the `enable_result_offload` property to `BaseTool`:

```python
class BaseTool(ABC, metaclass=ToolMeta):
    """Base class for all tools in the agent system."""
    
    name: str
    description: str
    description_long: Optional[str] = None
    parameters_model: Type[ParametersModel]
    
    # -------------------------------------------------------------------------
    # NEW: Result offload control
    # -------------------------------------------------------------------------
    # Set to False to opt out of result offloading for this tool
    # Default is True (offloading enabled)
    enable_result_offload: bool = True
    
    # ... rest of class ...
```

### 5.2 validate_and_execute Modification

**File**: `src/wichy/tools/base.py`

Modify `validate_and_execute` method to call `result_or_ref` after post hooks:

```python
def validate_and_execute(self, **kwargs) -> str:
    """Validate parameters using Pydantic model and execute."""
    # ... existing validation, pre-hooks, execution, post-hooks ...
    
    # Run post-tool hooks (even on exception for logging/monitoring)
    post_result = HookExecutor.run_post_hooks(
        self, self.name, final_args, res, error=execution_error
    )
    
    # Use modified output if hooks changed it
    if post_result.modified_output:
        res = post_result.modified_output
    
    # -------------------------------------------------------------------------
    # NEW: Result offload check
    # -------------------------------------------------------------------------
    # Only consider offloading if execution was successful
    if not execution_error:
        from wichy.result_offload import result_or_ref, get_result_store
        
        # Clean up expired results periodically (1% chance per call)
        import random
        if random.random() < 0.01:
            store = get_result_store()
            store.cleanup_expired()
        
        # Apply offload logic
        res = result_or_ref(
            result=res,
            tool_name=self.name,
            input_args=final_args,
            model_str=kwargs.get("model_str"),  # May be None
            enable_offload=self.enable_result_offload,
        )
    
    # ... existing error handling and logging ...
    
    return res
```

### 5.3 Model String Injection

**File**: `src/wichy/agent/core.py`

Modify `_tool_call` to inject `model_str` into tool parameters:

```python
def _tool_call(
    self, tools: List[BaseTool], item, inject_model_str: bool = False
) -> Tuple[Dict, Optional[List[Dict]]]:
    """Execute a tool call and return the result message."""
    # ... existing code to find tool and parse args ...
    
    # -------------------------------------------------------------------------
    # NEW: Inject model_str if requested (for result offload and query_result)
    # -------------------------------------------------------------------------
    if inject_model_str:
        # Check if tool has model_str parameter with HIDE_FROM_LLM prefix
        schema = tool.parameters_model.model_json_schema()
        if "properties" in schema and "model_str" in schema["properties"]:
            args["model_str"] = self.model_str
    
    # Execute tool
    result = tool.validate_and_execute(**args)
    
    # ... rest of method ...
```

### 5.4 Tool Registration

**File**: `src/wichy/tools/__init__.py` or relevant registry

Register `query_result` tool:

```python
from wichy.result_offload import QueryResultTool

# Add to tool list
ALL_TOOLS = [
    # ... existing tools ...
    QueryResultTool,
]
```

---

## 6. Examples

### 6.1 Basic Offload Flow

```
User: "Read the file src/main.py and tell me what it does"

Root Agent calls: read_file(path="src/main.py")

Tool result: 45,000 characters

Offload check:
  - len(45000) > 8000 ✓
  - len(45000) > 8800 ✓
  - enable_result_offload = True ✓
  → OFFLOAD

Store saves to SQLite: .wichy/results.db (ref_id: res_a1b2c3)

Returned to agent:
┌─────────────────────────────────────────────────────────────┐
│ [RESULT_OFFLOADED]                                          │
│ Reference ID: res_a1b2c3                                    │
│ Tool: read_file                                             │
│ Size: 45,000 characters                                     │
│                                                             │
│ --- PREVIEW (500 chars) ---                                 │
│ """Main application module.                                 │
│                                                             │
│ This module contains the core logic for...                  │
│ """import os                                                │
│ from typing import Optional                                 │
│ ...                                                         │
│ --- END PREVIEW ---                                         │
│                                                             │
│ To query this result, use the `query_result` tool:          │
│   query_result(ref_ids=["res_a1b2c3"], query="...")         │
└─────────────────────────────────────────────────────────────┘

Root Agent receives reference, then calls:
query_result(ref_ids=["res_a1b2c3"], query="What does this file do?")

Summarizer LLM analyzes stored content + query
Validator LLM validates response
→ Valid response returned to Root Agent
Root Agent provides answer to user
```

### 6.2 Tolerance Pass-Through

```
Tool result: 8,500 characters (within +10% tolerance)

Offload check:
  - len(8500) > 8000 ✓
  - len(8500) <= 8800 ✓ (within tolerance)
  → PASS THROUGH (return original result)
```

### 6.3 Tool Opt-Out

```
class CriticalTool(BaseTool):
    name = "critical_tool"
    enable_result_offload = False  # Always return full result
    ...

Tool result: 50,000 characters

Offload check:
  - enable_result_offload = False
  → PASS THROUGH (offloading disabled for this tool)
```

### 6.4 Multi-Result Query

```
User: "Compare the structure of these two files"

Root Agent reads file1 → offloaded → res_abc123
Root Agent reads file2 → offloaded → res_def456

Root Agent calls:
query_result(
    ref_ids=["res_abc123", "res_def456"],
    query="Compare the structure of these two files. What are the main differences?"
)

Summarizer receives both stored results and compares them.
```

---

## Summary

| Component | File | Purpose |
|-----------|------|---------|
| Settings | `config/settings.py` | Configurable thresholds and limits |
| ResultStore | `result_offload/store.py` | SQLite-backed thread-safe singleton |
| result_or_ref | `result_offload/hijack.py` | Offload decision logic |
| QueryResultTool | `result_offload/query_tool.py` | LLM-powered querying with validation |
| validation | `result_offload/validation.py` | Validation loop for summarizer responses |
| BaseTool modification | `tools/base.py` | `enable_result_offload` property |
| validate_and_execute | `tools/base.py` | Call `result_or_ref` after post hooks |
| _tool_call | `agent/core.py` | Inject `model_str` for query_result |