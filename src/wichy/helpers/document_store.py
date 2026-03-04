"""
Minimal document store that uses ChromaDB as backend.
"""

import json
from typing import Dict, List, Optional

import chromadb
from chromadb.config import Settings
from chromadb.utils.embedding_functions import SentenceTransformerEmbeddingFunction

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


class DocumentStore:
    """
    Document store that uses ChromaDB as backend.
    """

    def __init__(
        self,
        collection_name: str = "documents",
        model_name: str = "paraphrase-MiniLM-L6-v2",
        path_persistent_store: Optional[str] = None,
        allow_reset: bool = False,
    ):
        """Initialize Document Store.

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
        doc_id = gen_id()

        # Serialize metadata before storing
        serialized_metadata = _serialize_metadata(metadata)

        self.collection.add(
            documents=[document], metadatas=[serialized_metadata], ids=[doc_id]
        )

        return doc_id

    def delete_document(self, doc_id: str):
        """Delete a document from ChromaDB.

        Args:
            doc_id: ID of document to delete

        Returns:
            document: The document that was just deleted.
        """
        doc = self.collection.get(ids=[doc_id])
        self.collection.delete(ids=[doc_id])
        
        # Deserialize metadata if present
        if doc.get("metadatas") and doc["metadatas"]:
            doc["metadatas"] = [_deserialize_metadata(meta) for meta in doc["metadatas"]]
        
        return doc

    def update_document(self, doc_id: str, document: str, metadata: Dict):
        """Update a document in the store.

        Args:
            doc_id: ID of document to update
            document: New document content
            metadata: New metadata dictionary
        """
        # Delete old document and add new one
        self.delete_document(doc_id)
        serialized_metadata = _serialize_metadata(metadata)
        self.collection.add(
            documents=[document], metadatas=[serialized_metadata], ids=[doc_id]
        )

    def search(self, query: str, k: int = 5) -> Dict:
        """Search for similar documents.

        Args:
            query: Query text
            k: Number of results to return

        Returns:
            Dict with documents, metadatas, ids, and distances
        """
        results = self.collection.query(query_texts=[query], n_results=k)

        # Deserialize metadata from storage
        if "metadatas" in results and results["metadatas"]:
            results["metadatas"] = [
                [_deserialize_metadata(meta) for meta in results["metadatas"][0]]
            ]

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
