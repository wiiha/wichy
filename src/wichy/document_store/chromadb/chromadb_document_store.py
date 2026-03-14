"""
ChromaDB-based document store implementation.
"""

import json
from typing import Dict, List, Optional

import chromadb
from chromadb.config import Settings
from chromadb.utils.embedding_functions import SentenceTransformerEmbeddingFunction

from wichy.document_store.core import DocumentStore
from wichy.helpers.gen_id import gen_id

# Placeholder key for empty metadata (very unlikely to conflict with user keys)
_EMPTY_METADATA_PLACEHOLDER = "__empty_metadata_placeholder__"


def _serialize_metadata(metadata: Dict) -> Dict:
    """Serialize metadata for ChromaDB storage (convert lists/dicts to JSON strings)."""
    serialized = {}
    for k, v in metadata.items():
        # Skip None values completely
        if v is None:
            continue
        serialized[k] = _serialize_value(v)
    return serialized


def _serialize_value(value):
    """Serialize a single value."""
    if isinstance(value, (list, dict)):
        return json.dumps(value)
    return value


def _deserialize_metadata(metadata: Dict) -> Dict:
    """Deserialize metadata from ChromaDB (convert JSON strings back to Python objects)."""
    return {k: _deserialize_value(v) for k, v in metadata.items()}


def _deserialize_value(value):
    """Deserialize a single value."""
    if isinstance(value, str):
        try:
            return json.loads(value)
        except (json.JSONDecodeError, TypeError):
            return value
    return value


def _ensure_non_empty_metadata(metadata: Dict) -> Dict:
    """Ensure metadata is non-empty for ChromaDB by adding a placeholder if needed."""
    # Remove None values first
    filtered = {k: v for k, v in metadata.items() if v is not None}
    if not filtered:
        # Add a unique placeholder so ChromaDB accepts it
        return {_EMPTY_METADATA_PLACEHOLDER: gen_id()}
    return filtered


def _clean_returned_metadata(metadata: Dict) -> Dict:
    """Remove placeholder key from metadata when returning to user."""
    if _EMPTY_METADATA_PLACEHOLDER in metadata:
        # Remove placeholder; if nothing left, return empty dict
        cleaned = {
            k: v for k, v in metadata.items() if k != _EMPTY_METADATA_PLACEHOLDER
        }
        return cleaned
    return metadata


class ChromaDocumentStore(DocumentStore):
    """
    ChromaDB-based document store implementation.
    """

    def __init__(
        self,
        collection_name: str = "documents_" + gen_id(),
        model_name: str = "paraphrase-MiniLM-L6-v2",
        path_persistent_store: Optional[str] = None,
        allow_reset: bool = False,
    ):
        """Initialize ChromaDocumentStore.

        Args:
            collection_name: Name of the ChromaDB collection
            model_name: model to use, can be huggingface or path to local model
            path_persistent_store: Optional: if provided will result in a persistent document store at given path
            allow_reset: Whether to allow resetting the database (default: False)
        """
        # Store initialization parameters
        self.allow_reset = allow_reset
        self._collection_name = collection_name

        # Configure settings
        settings = Settings(allow_reset=True) if allow_reset else Settings()

        # Initialize client
        if path_persistent_store:
            self.client = chromadb.PersistentClient(
                path=path_persistent_store, settings=settings
            )
        else:
            self.client = chromadb.Client(settings)

        # Initialize embedding function
        self.embedding_function = SentenceTransformerEmbeddingFunction(
            model_name=model_name
        )
        # Get or create collection
        self.collection = self.client.get_or_create_collection(
            name=self._collection_name, embedding_function=self.embedding_function
        )

    def count(self) -> int:
        """Return total number of documents in the store."""
        try:
            # Preferred method
            return self.collection.count()
        except AttributeError:
            # Fallback for older Chroma versions
            return len(self.collection.get()["ids"])

    def add_document(self, document: str, metadata: Dict) -> str:
        """Add a document to the store.

        Args:
            document: Text content to store
            metadata: Dictionary of metadata

        Returns:
            str: id for the newly generated document
        """
        doc_id = metadata.get("id", gen_id())

        # Ensure metadata is non-empty for ChromaDB compatibility
        metadata_for_storage = _ensure_non_empty_metadata(metadata)
        # Serialize metadata before storing
        serialized_metadata = _serialize_metadata(metadata_for_storage)

        self.collection.add(
            documents=[document], metadatas=[serialized_metadata], ids=[doc_id]
        )

        return doc_id

    def get_document(self, doc_id: str) -> Optional[Dict]:
        """Get a single document by ID.

        Returns:
            Dict with keys: 'id', 'document', 'metadata' (all deserialized)
            or None if not found.
        """
        result = self.collection.get(ids=[doc_id])
        if not result["ids"]:
            return None

        # Deserialize metadata if present
        metadata = result["metadatas"][0] if result["metadatas"] else {}
        metadata = _deserialize_metadata(metadata)
        # Remove placeholder if present
        metadata = _clean_returned_metadata(metadata)

        return {
            "id": result["ids"][0],
            "document": result["documents"][0],
            "metadata": metadata,
        }

    def get_documents(self, doc_ids: List[str]) -> Dict:
        """Get multiple documents by IDs.

        Args:
            doc_ids: List of document IDs to fetch

        Returns:
            Dict with keys: 'ids', 'documents', 'metadatas' (all deserialized).
            May return fewer documents than requested if some IDs don't exist.
        """
        result = self.collection.get(ids=doc_ids)

        # Deserialize metadata if present and clean placeholders
        if result.get("metadatas"):
            result["metadatas"] = [
                _clean_returned_metadata(_deserialize_metadata(m))
                for m in result["metadatas"]
            ]

        return result

    def update_metadata(self, doc_id: str, metadata: Dict, merge: bool = True):
        """Update metadata for a document without changing its content or embedding.

        Args:
            doc_id: Document ID
            metadata: New metadata dict
            merge: If True, merge with existing metadata. If False, replace entirely.

        Raises:
            ValueError: If document not found
        """
        # Get existing document to verify existence and retrieve content
        existing = self.get_document(doc_id)
        if not existing:
            raise ValueError(f"Document {doc_id} not found")

        if merge:
            # Merge with existing metadata and update
            merged_metadata = {**existing["metadata"], **metadata}
            # Ensure non-empty for ChromaDB
            metadata_for_storage = _ensure_non_empty_metadata(merged_metadata)
            serialized = _serialize_metadata(metadata_for_storage)
            self.collection.update(ids=[doc_id], metadatas=[serialized])
        else:
            # Replace entirely: delete and re-add with same content and new metadata
            # Ensure non-empty for ChromaDB
            metadata_for_storage = _ensure_non_empty_metadata(metadata)
            serialized = _serialize_metadata(metadata_for_storage)
            self.collection.delete(ids=[doc_id])
            self.collection.add(
                documents=[existing["document"]],
                metadatas=[serialized],
                ids=[doc_id],
            )

    def delete_document(self, doc_id: str) -> bool:
        """Delete a document from ChromaDB.

        Args:
          doc_id: ID of document to delete

        Returns:
            True if document was deleted, False if not found
        """
        result = self.collection.get(ids=[doc_id])
        if not result["ids"]:
            return False
        self.collection.delete(ids=[doc_id])
        return True

    def query(
        self, query_texts: List[str], n_results: int = 5, where: Optional[Dict] = None
    ) -> Dict:
        """Query with optional metadata filter.

        Returns the same format as search(): dict with 'ids', 'documents',
        'metadatas', 'scores' (distances converted to similarity scores).
        """
        results = self.collection.query(
            query_texts=query_texts, n_results=n_results, where=where
        )

        # Deserialize metadata and clean placeholders
        if "metadatas" in results and results["metadatas"]:
            results["metadatas"] = [
                [
                    _clean_returned_metadata(_deserialize_metadata(meta))
                    for meta in meta_list
                ]
                for meta_list in results["metadatas"]
            ]

        # Convert distances to scores (higher = better)
        # ChromaDB returns L2 distances; we convert to similarity score
        if "distances" in results and results["distances"]:
            scores = []
            for distance_list in results["distances"]:
                score_list = [
                    1.0 / (1.0 + d) if d is not None else None for d in distance_list
                ]
                scores.append(score_list)
            results["scores"] = scores

        return results

    def list(self, limit: Optional[int] = None, where: Optional[Dict] = None) -> Dict:
        """List documents in the store.

        Args:
            limit: Maximum number of documents to return
            where: Optional metadata filter dictionary

        Returns:
            Dict with documents, metadatas, and ids
        """
        results = self.collection.get(limit=limit, where=where)

        # Deserialize metadata from storage and clean placeholders
        if "metadatas" in results and results["metadatas"]:
            results["metadatas"] = [
                _clean_returned_metadata(_deserialize_metadata(meta))
                for meta in results["metadatas"]
            ]

        return results

    def clear(self):
        """Clear all documents from this collection.

        This deletes and recreates the current collection, effectively removing all documents.
        Note: This does not affect other collections in the database.
        """
        # Get collection name before deleting
        collection_name = self.collection.name
        # Delete the collection
        self.client.delete_collection(name=collection_name)
        # Recreate it with the same embedding function
        self.collection = self.client.get_or_create_collection(
            name=collection_name, embedding_function=self.embedding_function
        )

    def reset(self):
        """Reset the store (same as clear for ChromaDB).

        Removes all documents from this collection while preserving the collection itself.
        Requires that the DocumentStore was initialized with allow_reset=True.

        Raises:
            ValueError: If reset is not allowed (allow_reset=False in __init__)
        """
        if not self.allow_reset:
            raise ValueError(
                "Reset is not allowed. Initialize DocumentStore with allow_reset=True."
            )
        self.clear()
