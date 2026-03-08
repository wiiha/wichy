"""
Naive memory implementation using DocumentStore (ChromaDB) as backend.

Stores conversation chunks (user+assistant exchanges) as individual memories.
Each memory includes:
- Content: combined user message + assistant response
- Metadata: turn_number, user_id, timestamp, etc.

Access statistics (retrieval_count, last_accessed) are tracked in metadata
and used for importance scoring.

Retrieval is vector-based using the DocumentStore's embeddings.
"""

from datetime import datetime, timezone
from typing import Dict, List, Optional

from wichy.document_store import ChromaDocumentStore
from wichy.memory.core.memory import Memory
from wichy.memory.core.note import MemoryNote


class NaiveMemory(Memory):
    """
    Naive memory implementation that wraps ChromaDocumentStore.

    This implementation stores conversation turns as memories and uses
    vector similarity for retrieval. Access statistics are tracked to
    compute importance scores.
    """

    # Internal metadata keys (prefixed to avoid collisions with user metadata)
    _KEY_RETRIEVAL_COUNT = "__wichy_retrieval_count"
    _KEY_LAST_ACCESSED = "__wichy_last_accessed"
    _KEY_TIMESTAMP = "__wichy_timestamp"  # when this memory was created

    def __init__(self, document_store: Optional[ChromaDocumentStore] = None):
        """
        Initialize NaiveMemory.

        Args:
            document_store: Optional existing ChromaDocumentStore. If None, creates a new one.
        """
        self.store = document_store or ChromaDocumentStore()

    def _document_to_note(
        self, doc_result: Dict, score: Optional[float] = None
    ) -> MemoryNote:
        """Convert ChromaDocumentStore result dict to MemoryNote."""
        metadata = doc_result["metadata"]
        retrieval_count = metadata.get(self._KEY_RETRIEVAL_COUNT, 0)
        last_accessed_str = metadata.get(self._KEY_LAST_ACCESSED)
        last_accessed = (
            datetime.fromisoformat(last_accessed_str) if last_accessed_str else None
        )
        timestamp_str = metadata.get(self._KEY_TIMESTAMP)
        created_at = datetime.fromisoformat(timestamp_str) if timestamp_str else None

        # Extract user metadata (exclude internal keys)
        user_metadata = {
            k: v
            for k, v in metadata.items()
            if k
            not in [
                self._KEY_RETRIEVAL_COUNT,
                self._KEY_LAST_ACCESSED,
                self._KEY_TIMESTAMP,
            ]
        }

        return MemoryNote(
            content=doc_result["document"],
            metadata=user_metadata,
            memory_id=doc_result["id"],
            created_at=created_at,
            retrieval_count=retrieval_count,
            last_accessed=last_accessed,
            score=score,
        )

    def add(self, content: str, metadata: Optional[dict] = None) -> str:
        """
        Store a new memory (conversation chunk).

        The content can be any text - typically a user+assistant exchange.
        Metadata can include turn_number, user_id, etc.
        System fields (timestamp, retrieval_count, last_accessed) are added automatically.

        Args:
            content: Text content to store
            metadata: Optional dict of additional metadata

        Returns:
            Memory ID
        """
        if metadata is None:
            metadata = {}
        else:
            metadata = metadata.copy()  # Don't mutate caller's dict

        # Add system fields
        metadata[self._KEY_TIMESTAMP] = datetime.now(timezone.utc).isoformat()
        metadata[self._KEY_RETRIEVAL_COUNT] = 0
        metadata[self._KEY_LAST_ACCESSED] = None

        return self.store.add_document(document=content, metadata=metadata)

    def get(self, memory_id: str) -> Optional[MemoryNote]:
        """
        Retrieve a specific memory by ID.

        Increments retrieval count and updates last_accessed.

        Args:
            memory_id: Unique identifier of the memory

        Returns:
            MemoryNote if found, None otherwise
        """
        result = self.store.get_document(memory_id)
        if not result:
            return None

        note = self._document_to_note(result)

        # Increment access stats
        try:
            self.store.update_metadata(
                memory_id,
                {
                    self._KEY_RETRIEVAL_COUNT: note.retrieval_count + 1,
                    self._KEY_LAST_ACCESSED: datetime.now(timezone.utc).isoformat(),
                },
                merge=True,
            )
        except ValueError:
            # Doc deleted between fetch and update; return None
            return None

        # Refresh note with updated stats
        updated = self.store.get_document(memory_id)
        if updated:
            note.retrieval_count = updated["metadata"].get(
                self._KEY_RETRIEVAL_COUNT, note.retrieval_count
            )
            last_accessed_str = updated["metadata"].get(self._KEY_LAST_ACCESSED)
            note.last_accessed = (
                datetime.fromisoformat(last_accessed_str) if last_accessed_str else None
            )

        return note

    def search(
        self, query: str, k: int = 5, filter_metadata: Optional[dict] = None
    ) -> List[MemoryNote]:
        """
        Search memories by semantic similarity.

        Uses ChromaDocumentStore's vector search. All returned memories have their
        access stats (retrieval_count, last_accessed) incremented.

        Args:
            query: Natural language search query
            k: Maximum number of results
            filter_metadata: Optional metadata filters passed to ChromaDB's where clause

        Returns:
            List of MemoryNote objects sorted by relevance (score descending)
        """
        results = self.store.query(
            query_texts=[query], n_results=k, where=filter_metadata
        )

        if not results["ids"][0]:
            return []

        notes = []
        memory_ids = []
        now_str = datetime.now(timezone.utc).isoformat()

        for doc_id, doc, meta, distance in zip(
            results["ids"][0],
            results["documents"][0],
            results["metadatas"][0],
            results["distances"][0],
        ):
            # Convert distance to score (higher = better)
            # ChromaDB returns L2 distances; convert to similarity
            score = 1.0 / (1.0 + distance) if distance is not None else None

            note = self._document_to_note(
                {"id": doc_id, "document": doc, "metadata": meta}, score=score
            )
            notes.append(note)
            memory_ids.append(doc_id)

        # Batch update access stats efficiently
        if memory_ids:
            # Fetch current retrieval counts for all memories in one batch
            current_docs = self.store.get_documents(memory_ids)
            current_counts = {
                doc["id"]: doc["metadata"].get(self._KEY_RETRIEVAL_COUNT, 0)
                for doc in current_docs.get("documents", [])
            }

            # Prepare batch metadata updates
            batch_updates = []
            for memory_id in memory_ids:
                current_count = current_counts.get(memory_id, 0)
                batch_updates.append(
                    {
                        "id": memory_id,
                        "metadata": {
                            self._KEY_RETRIEVAL_COUNT: current_count + 1,
                            self._KEY_LAST_ACCESSED: now_str,
                        },
                    }
                )

            # Apply batch updates
            for update in batch_updates:
                try:
                    self.store.update_metadata(
                        update["id"], update["metadata"], merge=True
                    )
                except ValueError:
                    pass  # Document was deleted before update

        return notes

    def count(self) -> int:
        """Total number of memories stored."""
        return self.store.collection.count()  # Simple proxy call

    def delete(self, memory_id: str) -> bool:
        """
        Delete a memory by ID.

        Returns:
            True if deleted, False if not found
        """
        try:
            self.store.delete_document(memory_id)
            return True
        except Exception:
            return False

    def get_important(
        self, k: int = 10, filter_metadata: Optional[dict] = None
    ) -> List[MemoryNote]:
        """
        Get memories sorted by importance (highest first).

        Importance is derived from retrieval_count, last_accessed, and age.
        This method performs a full scan (naive) and sorts in memory.
        NOTE: This does NOT update access stats (it's introspection, not retrieval).

        Args:
            k: Number of top memories to return
            filter_metadata: Optional metadata filter to narrow candidates

        Returns:
            List of MemoryNote objects with score=None, sorted by importance.
        """
        # Get all matching documents (use list with where filter)
        results = self.store.list(where=filter_metadata)

        if not results["ids"]:
            return []

        notes = [
            self._document_to_note({"id": doc_id, "document": doc, "metadata": meta})
            for doc_id, doc, meta in zip(
                results["ids"], results["documents"], results["metadatas"]
            )
        ]

        # Sort by derived importance (descending)
        notes.sort(key=lambda n: n.importance, reverse=True)
        return notes[:k]
