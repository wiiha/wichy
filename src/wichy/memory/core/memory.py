"""
Abstract memory interface - defines how the system perceives memory,
independent of underlying storage.

Implementations live in sibling packages (e.g., chroma, weaviate).
"""

from typing import List, Optional, Protocol, runtime_checkable

from .note import MemoryNote


@runtime_checkable
class Memory(Protocol):
    """
    Abstract memory interface - the contract for all memory backends.

    Implementations are responsible for:
    - Storing MemoryNote objects in their respective storage systems
    - Tracking retrieval_count and last_accessed on each memory
    - Updating these access stats whenever get() or search() is called
    - Supporting efficient queries for get_important() (should be optimized)
    """

    def add(self, content: str, metadata: Optional[dict] = None) -> str:
        """
        Store a new memory.

        Args:
            content: The text/content to remember
            metadata: Optional dict of key-value pairs

        Returns:
            The unique memory_id of the stored memory
        """
        ...

    def get(self, memory_id: str) -> Optional[MemoryNote]:
        """
        Retrieve a specific memory by ID.

        This access should increment retrieval_count and update last_accessed
        on that memory (if the implementation tracks usage stats).

        Args:
            memory_id: The unique identifier of the memory

        Returns:
            MemoryNote if found, None otherwise
        """
        ...

    def search(
        self, query: str, k: int = 5, filter_metadata: Optional[dict] = None
    ) -> List[MemoryNote]:
        """
        Search memories relevant to query.

        This access should increment retrieval_count and update last_accessed
        for all returned memories.

        Args:
            query: Natural language search query
            k: Maximum number of results to return
            filter_metadata: Optional dict to filter results by metadata fields

        Returns:
            List of MemoryNote objects, sorted by relevance (most relevant first).
            Each note will have its 'score' field populated with a relevance score
            (higher = more relevant).
        """
        ...

    def count(self) -> int:
        """
        Get total number of stored memories.

        Returns:
            Integer count of all memories in the store
        """
        ...

    def delete(self, memory_id: str) -> bool:
        """
        Delete a memory by ID.

        Args:
            memory_id: The unique identifier of the memory to delete

        Returns:
            True if memory was deleted, False if it didn't exist
        """
        ...

    def get_important(
        self, k: int = 10, filter_metadata: Optional[dict] = None
    ) -> List[MemoryNote]:
        """
        Get memories sorted by importance (highest first).

        Importance is derived from usage patterns (retrieval_count, last_accessed).
        This method should be optimized for efficient retrieval by importance.

        Use cases:
        - Building system prompts/context
        - Memory introspection and debugging
        - Prioritizing what to remember/emphasize

        Args:
            k: Maximum number of results to return
            filter_metadata: Optional dict to filter results by metadata fields

        Returns:
            List of MemoryNote objects, sorted by importance (most important first).
            The 'score' field will be None (not query-specific).
        """
        ...
