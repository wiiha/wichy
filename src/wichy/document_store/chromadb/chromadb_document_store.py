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
        settings = Settings(allow_reset=True) if allow_reset else Settings()

        if path_persistent_store:
            self.client = chromadb.PersistentClient(
                path=path_persistent_store, settings=settings
            )
        else:
            self.client = chromadb.Client(settings)

        self.embedding_function = SentenceTransformerEmbeddingFunction(
            model_name=model_name
        )
        self.collection = self.client.get_or_create_collection(
            name=collection_name, embedding_function=self.embedding_function
        )

    def add_document(self, document: str, metadata: Dict) -> str:
        """Add a document to the store.

        Args:
            document: Text content to add
            metadata: Dictionary of metadata

        Returns:
            str: id for the newly generated document
        """
        doc_id = metadata.get("id", gen_id())

        # Serialize metadata before storing
        serialized_metadata = _serialize_metadata(metadata)

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

        # Deserialize metadata if present
        if result.get("metadatas"):
            result["metadatas"] = [
                _deserialize_metadata(m) for m in result["metadatas"]
            ]

        return result

    def update_metadata(self, doc_id: str, metadata: Dict, merge: bool = True):
        """Update metadata for a document without changing its content or embedding.

        Args:
            doc_id: Document ID
            metadata: New metadata dict (will be merged with existing if merge=True)
            merge: If True, merge with existing metadata. If False, replace entirely.
        """
        # Get existing document to preserve content and merge metadata
        existing = self.get_document(doc_id)
        if not existing:
            raise ValueError(f"Document {doc_id} not found")

        if merge:
            merged_metadata = {**existing["metadata"], **metadata}
        else:
            merged_metadata = metadata

        # Serialize before storing
        serialized = _serialize_metadata(merged_metadata)

        self.collection.update(ids=[doc_id], metadatas=[serialized])

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

        # Deserialize metadata (same as in search())
        if "metadatas" in results and results["metadatas"]:
            results["metadatas"] = [
                [_deserialize_metadata(meta) for meta in meta_list]
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

        # Deserialize metadata from storage
        if "metadatas" in results and results["metadatas"]:
            results["metadatas"] = [
                _deserialize_metadata(meta) for meta in results["metadatas"]
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
        """Reset the entire ChromaDB database.

        This removes ALL collections and data from the database.
        Requires that the DocumentStore was initialized with allow_reset=True.
        After reset, the current collection will be automatically recreated.

        Raises:
            ValueError: If reset is not allowed (allow_reset=False in __init__)
        """
        if not getattr(self.client, "_settings", None) or not getattr(
            self.client._settings, "allow_reset", False
        ):
            raise ValueError(
                "Reset is not allowed. Initialize DocumentStore with allow_reset=True."
            )
        collection_name = self.collection.name
        self.client.reset()
        # Recreate our collection after reset
        self.collection = self.client.get_or_create_collection(
            name=collection_name, embedding_function=self.embedding_function
        )
