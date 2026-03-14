"""
Test cases for BM25DocumentStore implementation.
"""

import os
import tempfile
from pathlib import Path

import pytest

from wichy.document_store import BM25DocumentStore


@pytest.fixture
def bm25_store():
    """Create a fresh in-memory BM25DocumentStore for each test."""
    return BM25DocumentStore()


@pytest.fixture
def bm25_store_with_docs():
    """Create a BM25DocumentStore with sample documents."""
    store = BM25DocumentStore()
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


class TestBM25DocumentStoreBasic:
    """Test basic operations."""

    def test_empty_store_count(self, bm25_store):
        """Empty store should have count 0."""
        assert bm25_store.count() == 0

    def test_add_document_returns_id(self, bm25_store):
        """add_document should return the document ID."""
        doc_id = bm25_store.add_document("Test document", {"key": "value"})
        assert doc_id is not None
        assert isinstance(doc_id, str)

    def test_add_document_stores_content(self, bm25_store):
        """Added document should be retrievable."""
        doc_id = bm25_store.add_document("Hello world", {"source": "test"})
        result = bm25_store.get_document(doc_id)
        assert result is not None
        assert result["document"] == "Hello world"
        assert result["metadata"]["source"] == "test"

    def test_add_document_with_custom_id(self, bm25_store):
        """Metadata with 'id' should use that as document ID."""
        doc_id = bm25_store.add_document(
            "Doc content", {"id": "custom-id", "tag": "custom"}
        )
        assert doc_id == "custom-id"
        result = bm25_store.get_document("custom-id")
        assert result["document"] == "Doc content"
        assert result["metadata"]["tag"] == "custom"

    def test_get_document_nonexistent(self, bm25_store):
        """get_document should return None for non-existent ID."""
        result = bm25_store.get_document("does-not-exist")
        assert result is None

    def test_get_documents_multiple(self, bm25_store_with_docs):
        """get_documents should fetch multiple documents by IDs."""
        doc_ids = ["doc1", "doc3", "non-existent"]
        result = bm25_store_with_docs.get_documents(doc_ids)
        assert len(result["ids"]) == 2
        assert set(result["ids"]) == {"doc1", "doc3"}

    def test_delete_document(self, bm25_store_with_docs):
        """delete_document should return True on success."""
        initial_count = bm25_store_with_docs.count()
        result = bm25_store_with_docs.delete_document("doc1")
        assert result is True
        assert bm25_store_with_docs.count() == initial_count - 1
        assert bm25_store_with_docs.get_document("doc1") is None

    def test_delete_document_nonexistent(self, bm25_store_with_docs):
        """delete_document should return False for non-existent ID."""
        result = bm25_store_with_docs.delete_document("does-not-exist")
        assert result is False

    def test_clear(self, bm25_store_with_docs):
        """clear should remove all documents."""
        assert bm25_store_with_docs.count() == 4
        bm25_store_with_docs.clear()
        assert bm25_store_with_docs.count() == 0
        assert bm25_store_with_docs.list()["ids"] == []

    def test_reset(self, bm25_store_with_docs):
        """reset should be same as clear for BM25."""
        assert bm25_store_with_docs.count() == 4
        bm25_store_with_docs.reset()
        assert bm25_store_with_docs.count() == 0

    def test_update_metadata(self, bm25_store_with_docs):
        """update_metadata should modify metadata for existing document."""
        bm25_store_with_docs.update_metadata("doc1", {"new_field": "new_value"})
        result = bm25_store_with_docs.get_document("doc1")
        assert result["metadata"]["category"] == "tech"
        assert result["metadata"]["new_field"] == "new_value"

    def test_update_metadata_nonexistent(self, bm25_store_with_docs):
        """update_metadata should raise ValueError for non-existent document."""
        with pytest.raises(ValueError):
            bm25_store_with_docs.update_metadata("does-not-exist", {"key": "value"})

    def test_update_metadata_replace(self, bm25_store_with_docs):
        """update_metadata with merge=False should replace entirely."""
        bm25_store_with_docs.update_metadata(
            "doc1", {"new_field": "new_value"}, merge=False
        )
        result = bm25_store_with_docs.get_document("doc1")
        assert "category" not in result["metadata"]
        assert result["metadata"]["new_field"] == "new_value"


class TestBM25DocumentStoreList:
    """Test list functionality."""

    def test_list_empty(self, bm25_store):
        """list on empty store returns empty arrays."""
        result = bm25_store.list()
        assert result["ids"] == []
        assert result["documents"] == []
        assert result["metadatas"] == []

    def test_list_all(self, bm25_store_with_docs):
        """list should return all documents."""
        result = bm25_store_with_docs.list()
        assert len(result["ids"]) == 4
        assert set(result["ids"]) == {"doc1", "doc2", "doc3", "doc4"}

    def test_list_with_limit(self, bm25_store_with_docs):
        """list should respect limit parameter."""
        result = bm25_store_with_docs.list(limit=2)
        assert len(result["ids"]) == 2

    def test_list_with_where_filter(self, bm25_store_with_docs):
        """list should filter by metadata."""
        result = bm25_store_with_docs.list(where={"category": "tech"})
        assert len(result["ids"]) == 3
        assert all(meta["category"] == "tech" for meta in result["metadatas"])

    def test_list_with_where_and_limit(self, bm25_store_with_docs):
        """list should combine limit and where filter."""
        result = bm25_store_with_docs.list(limit=2, where={"category": "tech"})
        assert len(result["ids"]) <= 2
        assert all(meta["category"] == "tech" for meta in result["metadatas"])


class TestBM25DocumentStoreQuery:
    """Test query functionality with BM25 scoring."""

    def test_query_empty_store(self, bm25_store):
        """Query on empty store returns empty results."""
        result = bm25_store.query(["python"])
        assert result["ids"] == [[]]
        assert result["documents"] == [[]]
        assert result["scores"] == [[]]

    def test_query_single_term(self, bm25_store_with_docs):
        """Query should return relevant documents with scores."""
        result = bm25_store_with_docs.query(["python"])
        ids = result["ids"][0]
        assert len(ids) > 0
        # "Python is a programming language" and "Machine learning with Python" should score higher
        assert "doc1" in ids or "doc4" in ids
        # Check scores are descending
        scores = result["scores"][0]
        assert scores == sorted(scores, reverse=True)

    def test_query_multiple_terms(self, bm25_store_with_docs):
        """Query with multiple terms should work."""
        result = bm25_store_with_docs.query(["machine", "python"])
        ids = result["ids"][0]
        assert "doc4" in ids  # Contains both terms

    def test_query_n_results(self, bm25_store_with_docs):
        """Query should respect n_results parameter."""
        result = bm25_store_with_docs.query(["the"], n_results=1)
        assert len(result["ids"][0]) <= 1

    def test_query_returns_scores(self, bm25_store_with_docs):
        """Query should return BM25 scores."""
        result = bm25_store_with_docs.query(["quick"])
        assert "scores" in result
        assert len(result["scores"][0]) > 0
        assert all(isinstance(s, (int, float)) for s in result["scores"][0])

    def test_query_with_where_filter(self, bm25_store_with_docs):
        """Query should apply metadata filter after scoring."""
        result = bm25_store_with_docs.query(["the"], where={"category": "literature"})
        if result["ids"][0]:  # If any results
            for doc_id, meta in zip(result["ids"][0], result["metadatas"][0]):
                assert meta["category"] == "literature"


class TestBM25DocumentStorePersistence:
    """Test persistence functionality."""

    def test_save_and_load(self):
        """Store should save to and load from disk."""
        with tempfile.TemporaryDirectory() as tmpdir:
            index_path = Path(tmpdir) / "bm25_index.pkl"

            # Create and populate first store
            store1 = BM25DocumentStore(index_path=str(index_path))
            store1.add_document("First document", {"source": "test"})
            store1.add_document("Second document", {"source": "test"})

            # Create new store from same index
            store2 = BM25DocumentStore(index_path=str(index_path))

            # Verify data persisted
            assert store2.count() == 2
            results = store2.list()
            assert set(results["documents"]) == {"First document", "Second document"}

            # Verify query works
            result = store2.query(["first"])
            assert len(result["ids"][0]) > 0

    def test_load_nonexistent_index(self):
        """Loading from non-existent path creates empty store."""
        with tempfile.TemporaryDirectory() as tmpdir:
            index_path = Path(tmpdir) / "nonexistent.pkl"
            store = BM25DocumentStore(index_path=str(index_path))
            assert store.count() == 0

    def test_add_after_load_updates_index(self):
        """Adding documents after loading should work correctly."""
        with tempfile.TemporaryDirectory() as tmpdir:
            index_path = Path(tmpdir) / "test.pkl"

            # Create initial store
            store1 = BM25DocumentStore(index_path=str(index_path))
            store1.add_document("Doc 1", {})
            store1.add_document("Doc 2", {})

            # Create new store that loads from same index
            store2 = BM25DocumentStore(index_path=str(index_path))
            assert store2.count() == 2

            # Add new doc to store2
            store2.add_document("Doc 3", {})

            # Both should now have 3 docs when reloaded
            result = store2.query(["doc"])
            assert len(result["ids"][0]) == 3
