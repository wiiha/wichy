"""
Abstract document store interface - defines contract for document storage backends.
"""

from typing import Dict, List, Optional, Protocol, runtime_checkable


@runtime_checkable
class DocumentStore(Protocol):
    """
    Abstract document store interface - the contract for all document storage backends.

    Implementations are responsible for:
    - Storing documents with metadata
    - Supporting vector similarity search (dense retrieval) OR sparse retrieval (BM25)
    - Providing basic CRUD operations
    - Optional: metadata filtering (where clause)
    """

    def add_document(self, document: str, metadata: Dict) -> str:
        """
        Add a document to the store.

        Args:
            document: Text content to store
            metadata: Dictionary of metadata (may include 'id' to specify ID)

        Returns:
            The document ID (generated if not provided in metadata)
        """
        ...

    def get_document(self, doc_id: str) -> Optional[Dict]:
        """
        Get a single document by ID.

        Returns:
            Dict with keys: 'id', 'document', 'metadata' (all deserialized)
            or None if not found.
        """
        ...

    def get_documents(self, doc_ids: List[str]) -> Dict:
        """
        Get multiple documents by IDs.

        Returns:
            Dict with keys: 'ids', 'documents', 'metadatas' (all deserialized).
            May return fewer documents than requested if some IDs don't exist.
        """
        ...

    def update_metadata(self, doc_id: str, metadata: Dict, merge: bool = True):
        """
        Update metadata for a document without changing its content or embedding.

        Args:
            doc_id: Document ID
            metadata: New metadata dict
            merge: If True, merge with existing metadata. If False, replace entirely.

        Raises:
            ValueError: If document not found
        """
        ...

    def delete_document(self, doc_id: str) -> bool:
        """
        Delete a document from the store.

        Returns:
            True if document was deleted, False if not found
        """
        ...

    def query(
        self, query_texts: List[str], n_results: int = 5, where: Optional[Dict] = None
    ) -> Dict:
        """
        Query documents by similarity with optional metadata filter.

        For vector stores: vector similarity search.
        For sparse stores: BM25/keyword scoring.

        Args:
            query_texts: List of query strings (usually single query)
            n_results: Maximum number of results to return
            where: Optional metadata filter dict (implementation-specific syntax)

        Returns:
            Dict with keys: 'ids', 'documents', 'metadatas', 'scores'
            - 'scores': list of relevance scores (higher = more relevant)
        """
        ...

    def list(self, limit: Optional[int] = None, where: Optional[Dict] = None) -> Dict:
        """
        List documents with optional filters.

        Args:
            limit: Maximum number of documents to return
            where: Optional metadata filter dictionary

        Returns:
            Dict with 'ids', 'documents', 'metadatas'
        """
        ...

    def count(self) -> int:
        """Return total number of documents in the store."""
        ...

    def clear(self):
        """Remove all documents from this store/collection."""
        ...

    def reset(self):
        """
        Reset the entire store (if supported).

        Raises:
            ValueError: If reset is not allowed/not supported
        """
        ...
