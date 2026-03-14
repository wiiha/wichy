"""
Test cases for ChromaDocumentStore implementation.
"""

import tempfile
import uuid

import pytest

from wichy.document_store import ChromaDocumentStore


def _unique_collection_name():
    """Generate a truly unique collection name."""
    return f"test_{uuid.uuid4().hex}"


@pytest.fixture
def chroma_store():
    """Create a fresh in-memory ChromaDocumentStore for each test."""
    return ChromaDocumentStore(
        collection_name=_unique_collection_name(),
        allow_reset=True,
    )


@pytest.fixture
def chroma_store_with_docs():
    """Create a ChromaDocumentStore with sample documents."""
    store = ChromaDocumentStore(
        collection_name=_unique_collection_name(),
        allow_reset=True,
    )
    store.add_document(
        "Python is a programming language", {"category": "tech", "id": "doc1"}
    )
    store.add_document(
        "JavaScript runs in the browser", {"category": "tech", "id": "doc2"}
    )
    store.add_document(
        "The quick brown fox jumps", {"category": "literature", "id": "doc3"}
    )
    store.add_document(
        "Machine learning with Python", {"category": "tech", "id": "doc4"}
    )
    return store


class TestChromaDocumentStoreBasic:
    """Test basic operations."""

    def test_empty_store_count(self, chroma_store):
        """Empty store should have count 0."""
        assert chroma_store.count() == 0

    def test_add_document_returns_id(self, chroma_store):
        """add_document should return the document ID."""
        doc_id = chroma_store.add_document("Test document", {"key": "value"})
        assert doc_id is not None
        assert isinstance(doc_id, str)

    def test_add_document_stores_content(self, chroma_store):
        """Added document should be retrievable."""
        doc_id = chroma_store.add_document("Hello world", {"source": "test"})
        result = chroma_store.get_document(doc_id)
        assert result is not None
        assert result["document"] == "Hello world"
        assert result["metadata"]["source"] == "test"

    def test_add_document_with_custom_id(self, chroma_store):
        """Metadata with 'id' should use that as document ID."""
        doc_id = chroma_store.add_document(
            "Doc content", {"id": "custom-id", "tag": "custom"}
        )
        assert doc_id == "custom-id"
        result = chroma_store.get_document("custom-id")
        assert result["document"] == "Doc content"
        assert result["metadata"]["tag"] == "custom"

    def test_get_document_nonexistent(self, chroma_store):
        """get_document should return None for non-existent ID."""
        result = chroma_store.get_document("does-not-exist")
        assert result is None

    def test_get_documents_multiple(self, chroma_store_with_docs):
        """get_documents should fetch multiple documents by IDs."""
        doc_ids = ["doc1", "doc3", "non-existent"]
        result = chroma_store_with_docs.get_documents(doc_ids)
        assert len(result["ids"]) == 2
        assert set(result["ids"]) == {"doc1", "doc3"}

    def test_delete_document(self, chroma_store_with_docs):
        """delete_document should return True on success."""
        initial_count = chroma_store_with_docs.count()
        result = chroma_store_with_docs.delete_document("doc1")
        assert result is True
        assert chroma_store_with_docs.count() == initial_count - 1
        assert chroma_store_with_docs.get_document("doc1") is None

    def test_delete_document_nonexistent(self, chroma_store_with_docs):
        """delete_document should return False for non-existent ID."""
        result = chroma_store_with_docs.delete_document("does-not-exist")
        assert result is False

    def test_clear(self, chroma_store_with_docs):
        """clear should remove all documents but preserve collection."""
        count_before = chroma_store_with_docs.count()
        assert count_before == 4
        chroma_store_with_docs.clear()
        assert chroma_store_with_docs.count() == 0
        # Can still add documents after clear
        chroma_store_with_docs.add_document("New doc", {"after_clear": True})
        assert chroma_store_with_docs.count() == 1

    def test_update_metadata(self, chroma_store_with_docs):
        """update_metadata should modify metadata for existing document."""
        chroma_store_with_docs.update_metadata("doc1", {"new_field": "new_value"})
        result = chroma_store_with_docs.get_document("doc1")
        assert result["metadata"]["category"] == "tech"
        assert result["metadata"]["new_field"] == "new_value"

    def test_update_metadata_nonexistent(self, chroma_store_with_docs):
        """update_metadata should raise ValueError for non-existent document."""
        with pytest.raises(ValueError):
            chroma_store_with_docs.update_metadata("does-not-exist", {"key": "value"})

    def test_update_metadata_replace(self, chroma_store_with_docs):
        """update_metadata with merge=False should replace entirely."""
        chroma_store_with_docs.update_metadata(
            "doc1", {"new_field": "new_value"}, merge=False
        )
        result = chroma_store_with_docs.get_document("doc1")
        assert "category" not in result["metadata"]
        assert result["metadata"]["new_field"] == "new_value"


class TestChromaDocumentStoreList:
    """Test list functionality."""

    def test_list_empty(self, chroma_store):
        """list on empty store returns empty arrays."""
        result = chroma_store.list()
        assert result["ids"] == []
        assert result["documents"] == []
        assert result["metadatas"] == []

    def test_list_all(self, chroma_store_with_docs):
        """list should return all documents."""
        result = chroma_store_with_docs.list()
        assert len(result["ids"]) == 4
        assert set(result["ids"]) == {"doc1", "doc2", "doc3", "doc4"}

    def test_list_with_limit(self, chroma_store_with_docs):
        """list should respect limit parameter."""
        result = chroma_store_with_docs.list(limit=2)
        assert len(result["ids"]) == 2

    def test_list_with_where_filter(self, chroma_store_with_docs):
        """list should filter by metadata using ChromaDB's where clause."""
        result = chroma_store_with_docs.list(where={"category": "tech"})
        assert len(result["ids"]) == 3
        assert all(meta["category"] == "tech" for meta in result["metadatas"])

    def test_list_with_where_and_limit(self, chroma_store_with_docs):
        """list should combine limit and where filter."""
        result = chroma_store_with_docs.list(limit=2, where={"category": "tech"})
        assert len(result["ids"]) <= 2
        assert all(meta["category"] == "tech" for meta in result["metadatas"])


class TestChromaDocumentStoreQuery:
    """Test query functionality with vector similarity."""

    def test_query_empty_store(self, chroma_store):
        """Query on empty store returns empty results."""
        result = chroma_store.query(["python"])
        assert result["ids"] == [[]]
        assert result["documents"] == [[]]
        assert result["scores"] == [[]]

    def test_query_returns_scores(self, chroma_store_with_docs):
        """Query should return similarity scores (converted from distances)."""
        result = chroma_store_with_docs.query(["python"])
        assert "scores" in result
        assert len(result["scores"][0]) > 0
        # Scores should be between 0 and 1 (after conversion: 1/(1+d))
        assert all(0 <= s <= 1 for s in result["scores"][0])

    def test_query_n_results(self, chroma_store_with_docs):
        """Query should respect n_results parameter."""
        result = chroma_store_with_docs.query(["python"], n_results=2)
        assert len(result["ids"][0]) <= 2

    def test_query_returns_all_fields(self, chroma_store_with_docs):
        """Query should return ids, documents, metadatas, and scores."""
        result = chroma_store_with_docs.query(["python"])
        assert "ids" in result
        assert "documents" in result
        assert "metadatas" in result
        assert "scores" in result
        # All fields should have matching lengths
        assert (
            len(result["ids"][0])
            == len(result["documents"][0])
            == len(result["metadatas"][0])
            == len(result["scores"][0])
        )

    def test_query_with_where_filter(self, chroma_store_with_docs):
        """Query should apply metadata filter using ChromaDB's where."""
        result = chroma_store_with_docs.query(["the"], where={"category": "literature"})
        if result["ids"][0]:
            for doc_id, meta in zip(result["ids"][0], result["metadatas"][0]):
                assert meta["category"] == "literature"

    def test_query_scores_sorted_descending(self, chroma_store_with_docs):
        """Query results should be sorted by score descending."""
        result = chroma_store_with_docs.query(["python"], n_results=5)
        scores = result["scores"][0]
        # Should be sorted in descending order
        assert scores == sorted(scores, reverse=True)


class TestChromaDocumentStoreSerialization:
    """Test metadata serialization/deserialization."""

    def test_serialize_list_metadata(self, chroma_store):
        """Lists in metadata should be properly serialized and deserialized."""
        complex_metadata = {
            "tags": ["python", "chromadb", "testing"],
            "numbers": [1, 2, 3, 4, 5],
            "mixed": ["string", 123, True],
        }
        doc_id = chroma_store.add_document(
            document="Document with list metadata", metadata=complex_metadata
        )
        result = chroma_store.get_document(doc_id)
        retrieved_metadata = result["metadata"]
        assert retrieved_metadata["tags"] == ["python", "chromadb", "testing"]
        assert retrieved_metadata["numbers"] == [1, 2, 3, 4, 5]
        assert retrieved_metadata["mixed"] == ["string", 123, True]

    def test_serialize_dict_metadata(self, chroma_store):
        """Nested dicts in metadata should be properly serialized and deserialized."""
        complex_metadata = {
            "config": {"setting1": "value1", "setting2": 42},
            "nested": {"deep": {"deeper": "value"}},
        }
        doc_id = chroma_store.add_document(
            document="Document with dict metadata", metadata=complex_metadata
        )
        result = chroma_store.get_document(doc_id)
        retrieved_metadata = result["metadata"]
        assert retrieved_metadata["config"] == {"setting1": "value1", "setting2": 42}
        assert retrieved_metadata["nested"] == {"deep": {"deeper": "value"}}

    def test_serialize_mixed_types(self, chroma_store):
        """Mixed simple and complex types should serialize correctly."""
        complex_metadata = {
            "simple_string": "hello",
            "simple_number": 42,
            "simple_bool": True,
            "list_type": [1, 2, 3],
            "dict_type": {"key": "value"},
            "null_value": None,  # This should be skipped
        }
        doc_id = chroma_store.add_document(
            document="Document with mixed metadata types", metadata=complex_metadata
        )
        result = chroma_store.get_document(doc_id)
        retrieved_metadata = result["metadata"]
        assert retrieved_metadata["simple_string"] == "hello"
        assert retrieved_metadata["simple_number"] == 42
        assert retrieved_metadata["simple_bool"] is True
        assert retrieved_metadata["list_type"] == [1, 2, 3]
        assert retrieved_metadata["dict_type"] == {"key": "value"}
        assert "null_value" not in retrieved_metadata

    def test_serialize_through_query(self, chroma_store_with_docs):
        """Query results should contain properly deserialized metadata."""
        # Add document with complex metadata
        chroma_store_with_docs.add_document(
            document="Doc with complex metadata",
            metadata={"tags": ["a", "b"], "count": 1},
        )
        result = chroma_store_with_docs.query(["doc"])
        metadatas = result["metadatas"][0]
        for meta in metadatas:
            # All metadata should be properly deserialized
            assert isinstance(meta.get("tags"), list) if "tags" in meta else True
            assert isinstance(meta.get("count"), int) if "count" in meta else True

    def test_serialize_through_get_documents(self, chroma_store_with_docs):
        """get_documents should return deserialized metadata."""
        doc_ids = ["doc1", "doc2"]
        result = chroma_store_with_docs.get_documents(doc_ids)
        for meta in result["metadatas"]:
            # Metadata should be dict (possibly empty)
            assert isinstance(meta, dict)


class TestChromaDocumentStoreReset:
    """Test reset functionality - using persistent storage to avoid singleton conflicts."""

    def test_reset_without_allow_reset_raises_error(self):
        """reset should raise ValueError when allow_reset=False."""
        with tempfile.TemporaryDirectory() as tmpdir:
            store = ChromaDocumentStore(
                collection_name="test_reset_no_allow",
                path_persistent_store=tmpdir,
                allow_reset=False,
            )
            with pytest.raises(ValueError, match="Reset is not allowed"):
                store.reset()

    def test_reset_with_allow_reset_clears_all_data(self):
        """reset with allow_reset=True should clear all data and recreate collection."""
        with tempfile.TemporaryDirectory() as tmpdir:
            store = ChromaDocumentStore(
                collection_name="test_reset_allow",
                path_persistent_store=tmpdir,
                allow_reset=True,
            )
            # Add some documents (with non-empty metadata)
            store.add_document("Doc 1", {"source": "test"})
            store.add_document("Doc 2", {"source": "test"})
            assert store.count() == 2
            # Reset
            store.reset()
            assert store.count() == 0
            # Can still add documents after reset
            store.add_document("New doc", {"after_reset": True})
            assert store.count() == 1

    def test_reset_does_not_affect_other_collections(self):
        """Resetting one store should not affect stores with different collection names."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Two separate stores with different collection names but same persistent location
            store_a = ChromaDocumentStore(
                collection_name="collection_a",
                path_persistent_store=tmpdir,
                allow_reset=True,
            )
            store_b = ChromaDocumentStore(
                collection_name="collection_b",
                path_persistent_store=tmpdir,
                allow_reset=True,
            )

            # Add docs to both
            doc_a = store_a.add_document("Doc A", {"source": "A"})
            doc_b = store_b.add_document("Doc B", {"source": "B"})

            # Verify both have docs
            assert store_a.count() == 1
            assert store_b.count() == 1

            # Reset collection A
            store_a.reset()

            # A should be empty, B should still have its doc
            assert store_a.count() == 0
            assert store_b.count() == 1
            assert store_b.get_document(doc_b) is not None
