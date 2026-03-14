"""
Context management for conversation history.

This package provides the ContextHandler class for managing conversation
context as JSONL files, with file-based locking and change detection.
"""

from wichy.context.handler import ContextHandler, MESSAGE_TYPE, LOG_TYPE
from wichy.context.file_lock import FileLock, FileLockError

__all__ = ["ContextHandler", "MESSAGE_TYPE", "LOG_TYPE", "FileLock", "FileLockError"]
