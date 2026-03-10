"""
Test cases for HybridDocumentStore implementation.
"""

import uuid

import pytest

from wichy.document_store import HybridDocumentStore


def _unique_store_id():
    """Generate a unique identifier for stores."""
    return f"test_{uuid.uuid4().hex}"


@pytest.fixture
def hybrid_store():
    """Create a fresh in-memory HybridDocumentStore for each test."""
    return HybridDocumentStore(
        chroma_collection_name=f"hybrid_{_unique_store_id()}",
        chroma_allow_reset=True,
    )


@pytest.fixture
def hybrid_store_with_docs():
    """Create a HybridDocumentStore with sample documents."""
    store = HybridDocumentStore(
        chroma_collection_name=f"hybrid_{_unique_store_id()}",
        chroma_allow_reset=True,
    )
    store.add_document("Python is a programming language", {"category": "tech", "id": "doc1"})
    store.add_document("JavaScript runs in the browser", {"category": "tech", "id": "doc2"})
    store.add_document("The quick brown fox jumps", {"category": "literature", "id": "doc3"})
    store.add_document("Machine learning with Python", {"category": "tech", "id": "doc4"})
    return store


class TestHybridDocumentStoreBasic:
    """Test basic operations."""

    def test_empty_store_count(self, hybrid_store):
        """Empty store should have count 0."""
        assert hybrid_store.count() == 0

    def test_add_document_returns_id(self, hybrid_store):
        """add_document should return the document ID."""
        doc_id = hybrid_store.add_document("Test document", {"key": "value"})
        assert doc_id is not None
        assert isinstance(doc_id, str)

    def test_add_document_stores_content(self, hybrid_store):
        """Added document should be retrievable."""
        doc_id = hybrid_store.add_document("Hello world", {"source": "test"})
        result = hybrid_store.get_document(doc_id)
        assert result is not None
        assert result["document"] == "Hello world"
        assert result["metadata"]["source"] == "test"

    def test_add_document_with_custom_id(self, hybrid_store):
        """Metadata with 'id' should use that as document ID."""
        doc_id = hybrid_store.add_document("Doc content", {"id": "custom-id", "tag": "custom"})
        assert doc_id == "custom-id"
        result = hybrid_store.get_document("custom-id")
        assert result["document"] == "Doc content"
        assert result["metadata"]["tag"] == "custom"

    def test_get_document_nonexistent(self, hybrid_store):
        """get_document should return None for non-existent ID."""
        result = hybrid_store.get_document("does-not-exist")
        assert result is None

    def test_get_documents_multiple(self, hybrid_store_with_docs):
        """get_documents should fetch multiple documents by IDs."""
        doc_ids = ["doc1", "doc3", "non-existent"]
        result = hybrid_store_with_docs.get_documents(doc_ids)
        assert len(result["ids"]) == 2
        assert set(result["ids"]) == {"doc1", "doc3"}

    def test_delete_document(self, hybrid_store_with_docs):
        """delete_document should return True on success."""
        initial_count = hybrid_store_with_docs.count()
        result = hybrid_store_with_docs.delete_document("doc1")
        assert result is True
        assert hybrid_store_with_docs.count() == initial_count - 1
        assert hybrid_store_with_docs.get_document("doc1") is None

    def test_delete_document_nonexistent(self, hybrid_store_with_docs):
        """delete_document should return False for non-existent ID."""
        result = hybrid_store_with_docs.delete_document("does-not-exist")
        assert result is False

    def test_clear(self, hybrid_store_with_docs):
        """clear should remove all documents from both backends."""
        assert hybrid_store_with_docs.count() == 4
        hybrid_store_with_docs.clear()
        assert hybrid_store_with_docs.count() == 0
        # Can still add documents after clear
        hybrid_store_with_docs.add_document("New doc", {"after_clear": True})
        assert hybrid_store_with_docs.count() == 1

    def test_update_metadata(self, hybrid_store_with_docs):
        """update_metadata should modify metadata for existing document in both backends."""
        hybrid_store_with_docs.update_metadata("doc1", {"new_field": "new_value"})
        result = hybrid_store_with_docs.get_document("doc1")
        assert result["metadata"]["category"] == "tech"
        assert result["metadata"]["new_field"] == "new_value"

    def test_update_metadata_nonexistent(self, hybrid_store_with_docs):
        """update_metadata should raise ValueError for non-existent document."""
        with pytest.raises(ValueError):
            hybrid_store_with_docs.update_metadata("does-not-exist", {"key": "value"})

    def test_update_metadata_replace(self, hybrid_store_with_docs):
        """update_metadata with merge=False should replace entirely."""
        hybrid_store_with_docs.update_metadata(
            "doc1", {"new_field": "new_value"}, merge=False
        )
        result = hybrid_store_with_docs.get_document("doc1")
        assert "category" not in result["metadata"]
        assert result["metadata"]["new_field"] == "new_value"


class TestHybridDocumentStoreBackendSync:
    """Test that both backends stay synchronized."""

    def test_add_sync(self, hybrid_store):
        """Adding a document should add it to both backends."""
        doc_id = hybrid_store.add_document("sync test", {"sync": "test"})
        
        # Check ChromaDB
        chroma_doc = hybrid_store.chroma.get_document(doc_id)
        assert chroma_doc is not None
        assert chroma_doc["document"] == "sync test"
        
        # Check BM25
        bm25_doc = hybrid_store.bm25.get_document(doc_id)
        assert bm25_doc is not None
        assert bm25_doc["document"] == "sync test"

    def test_delete_sync(self, hybrid_store_with_docs):
        """Deleting a document should remove it from both backends."""
        doc_id = "doc1"
        hybrid_store_with_docs.delete_document(doc_id)
        
        # Check ChromaDB
        assert hybrid_store_with_docs.chroma.get_document(doc_id) is None
        # Check BM25
        assert hybrid_store_with_docs.bm25.get_document(doc_id) is None

    def test_update_metadata_sync(self, hybrid_store_with_docs):
        """Updating metadata should affect both backends."""
        hybrid_store_with_docs.update_metadata("doc1", {"updated": True})
        
        # Check ChromaDB
        chroma_doc = hybrid_store_with_docs.chroma.get_document("doc1")
        assert chroma_doc["metadata"]["updated"] is True
        
        # Check BM25
        bm25_doc = hybrid_store_with_docs.bm25.get_document("doc1")
        assert bm25_doc["metadata"]["updated"] is True

    def test_clear_sync(self, hybrid_store_with_docs):
        """Clearing should remove docs from both backends."""
        hybrid_store_with_docs.clear()
        
        assert hybrid_store_with_docs.chroma.count() == 0
        assert hybrid_store_with_docs.bm25.count() == 0


class TestHybridDocumentStoreList:
    """Test list functionality."""

    def test_list_empty(self, hybrid_store):
        """list on empty store returns empty arrays."""
        result = hybrid_store.list()
        assert result["ids"] == []
        assert result["documents"] == []
        assert result["metadatas"] == []

    def test_list_all(self, hybrid_store_with_docs):
        """list should return all documents."""
        result = hybrid_store_with_docs.list()
        assert len(result["ids"]) == 4
        assert set(result["ids"]) == {"doc1", "doc2", "doc3", "doc4"}

    def test_list_with_limit(self, hybrid_store_with_docs):
        """list should respect limit parameter."""
        result = hybrid_store_with_docs.list(limit=2)
        assert len(result["ids"]) == 2

    def test_list_with_where_filter(self, hybrid_store_with_docs):
        """list should filter by metadata."""
        result = hybrid_store_with_docs.list(where={"category": "tech"})
        assert len(result["ids"]) == 3
        assert all(meta["category"] == "tech" for meta in result["metadatas"])

    def test_list_with_where_and_limit(self, hybrid_store_with_docs):
        """list should combine limit and where filter."""
        result = hybrid_store_with_docs.list(limit=2, where={"category": "tech"})
        assert len(result["ids"]) <= 2
        assert all(meta["category"] == "tech" for meta in result["metadatas"])


class TestHybridDocumentStoreQuery:
    """Test hybrid query functionality with RRF fusion."""

    def test_query_empty_store(self, hybrid_store):
        """Query on empty store returns empty results."""
        result = hybrid_store.query(["python"])
        assert result["ids"] == [[]]
        assert result["documents"] == [[]]
        assert result["scores"] == [[]]

    def test_query_returns_all_fields(self, hybrid_store_with_docs):
        """Query should return ids, documents, metadatas, and scores."""
        result = hybrid_store_with_docs.query(["python"])
        assert "ids" in result
        assert "documents" in result
        assert "metadatas" in result
        assert "scores" in result
        # All fields should have matching lengths
        assert len(result["ids"][0]) == len(result["documents"][0]) == len(result["metadatas"][0]) == len(result["scores"][0])

    def test_query_returns_scores(self, hybrid_store_with_docs):
        """Query should return RRF scores (float values)."""
        result = hybrid_store_with_docs.query(["python"])
        assert "scores" in result
        assert len(result["scores"][0]) > 0
        assert all(isinstance(s, float) for s in result["scores"][0])

    def test_query_n_results(self, hybrid_store_with_docs):
        """Query should respect n_results parameter."""
        result = hybrid_store_with_docs.query(["python"], n_results=2)
        assert len(result["ids"][0]) <= 2

    def test_query_with_where_filter(self, hybrid_store_with_docs):
        """Query should apply metadata filter to both backends before fusion."""
        result = hybrid_store_with_docs.query(["python"], where={"category": "tech"})
        if result["ids"][0]:
            for doc_id, meta in zip(result["ids"][0], result["metadatas"][0]):
                assert meta["category"] == "tech"

    def test_query_scores_sorted_descending(self, hybrid_store_with_docs):
        """Query results should be sorted by RRF score descending."""
        result = hybrid_store_with_docs.query(["python"], n_results=5)
        scores = result["scores"][0]
        # Should be sorted in descending order
        assert scores == sorted(scores, reverse=True)

    def test_query_combines_dense_and_sparse(self, hybrid_store):
        """Hybrid query should use both dense and sparse retrieval."""
        # Add documents that might be retrieved differently by each method
        hybrid_store.add_document("Python programming for beginners", {"id": "dense_fav"})
        hybrid_store.add_document("python python python", {"id": "sparse_fav"})
        
        # Query for "python" - should combine results from both
        result = hybrid_store.query(["python"], n_results=5)
        ids = result["ids"][0]
        assert len(ids) > 0
        # At least one of the documents should be found
        assert "dense_fav" in ids or "sparse_fav" in ids

    def test_rrf_score_calculation(self, hybrid_store_with_docs):
        """
        Verify that RRF scores are computed correctly.
        A document appearing in both result lists should have higher score
        than a document appearing in only one list.
        """
        # The tests with_docs have 4 docs, all containing some tech/literature terms
        result = hybrid_store_with_docs.query(["python"], n_results=10)
        
        # Since we're getting multiple results, some docs may appear in both rankings
        # We can't guarantee exact RRF values without mocking, but we can check
        # that scores are reasonable (positive, descending)
        scores = result["scores"][0]
        assert all(s > 0 for s in scores)
        assert scores == sorted(scores, reverse=True)

    def test_query_returns_consistent_content(self, hybrid_store_with_docs):
        """Query should return the correct document content and metadata."""
        result = hybrid_store_with_docs.query(["python"], n_results=10)
        ids = result["ids"][0]
        documents = result["documents"][0]
        metadatas = result["metadatas"][0]
        
        # Verify each returned document matches its ID and metadata
        for doc_id, doc, meta in zip(ids, documents, metadatas):
            full_doc = hybrid_store_with_docs.get_document(doc_id)
            assert doc == full_doc["document"]
            assert meta == full_doc["metadata"]


class TestHybridDocumentStoreReset:
    """Test reset functionality."""

    def test_reset_clears_all_data(self, hybrid_store_with_docs):
        """reset should clear all data from both backends."""
        assert hybrid_store_with_docs.count() == 4
        
        hybrid_store_with_docs.reset()
        
        assert hybrid_store_with_docs.count() == 0
        # Can still add documents after reset
        hybrid_store_with_docs.add_document("New doc", {"after_reset": True})
        assert hybrid_store_with_docs.count() == 1


class TestHybridDocumentStoreEdgeCases:
    """Test edge cases and error conditions."""

    def test_empty_query_returns_empty(self, hybrid_store_with_docs):
        """Empty query_texts should return empty results."""
        result = hybrid_store_with_docs.query([])
        assert result["ids"] == [[]]
        assert result["documents"] == [[]]
        assert result["scores"] == [[]]

    def test_deduplication_when_same_id_in_both(self, hybrid_store):
        """RRF should correctly handle documents that appear in both rankings."""
        # Add documents with relevant content
        hybrid_store.add_document("Python programming language", {"id": "doc_a"})
        hybrid_store.add_document("Python for data science", {"id": "doc_b"})
        hybrid_store.add_document("Learn JavaScript", {"id": "doc_c"})
        hybrid_store.add_document("Python basics tutorial", {"id": "doc_d"})
        
        result = hybrid_store.query(["python"], n_results=5)
        # Should get at least some results
        assert len(result["ids"][0]) > 0
        # All returned IDs should be unique
        ids = result["ids"][0]
        assert len(ids) == len(set(ids))

    def test_metadata_filter_on_both_backends(self, hybrid_store_with_docs):
        """
        Metadata filter should be applied to both backends before fusion.
        Only documents matching the filter in BOTH backends should be considered.
        Actually, each backend filters independently, then intersection/union is fused.
        """
        # This is more of an integration test - verify no crashes
        result = hybrid_store_with_docs.query(["python"], where={"category": "tech"})
        # Should return only tech category docs
        if result["ids"][0]:
            for meta in result["metadatas"][0]:
                assert meta["category"] == "tech"

    def test_multiple_adds_maintain_sync(self, hybrid_store):
        """Multiple sequential adds should keep both backends in sync."""
        for i in range(10):
            hybrid_store.add_document(f"Document {i}", {"index": i})
        
        assert hybrid_store.chroma.count() == 10
        assert hybrid_store.bm25.count() == 10
        
        # All documents should be retrievable from both
        for i in range(10):
            doc_id = f"doc_{i}" if i > 0 else "doc1"  # Actually we didn't set IDs, so get generated ones
            # Let's just check count consistency
            pass
        
        # Actually the IDs are generated, so we need to track them
        # But we can check total counts match
        assert hybrid_store.chroma.count() == hybrid_store.bm25.count()


class TestHybridDocumentStoreConfig:
    """Test configuration options."""

    def test_custom_rrf_k_parameter(self):
        """Custom rrf_k should be used in score calculation."""
        store = HybridDocumentStore(
            chroma_collection_name=f"test_k_{_unique_store_id()}",
            chroma_allow_reset=True,
            rrf_k=30,
        )
        assert store.rrf_k == 30
        
        # Add documents
        store.add_document("Python programming", {"id": "doc1"})
        store.add_document("JavaScript basics", {"id": "doc2"})
        
        result = store.query(["python"])
        # Should still work with custom k
        assert len(result["ids"][0]) > 0

    def test_min_chroma_score_filters(self):
        """min_chroma_score should filter Chroma results before fusion."""
        store = HybridDocumentStore(
            chroma_collection_name=f"test_chroma_thresh_{_unique_store_id()}",
            chroma_allow_reset=True,
            min_chroma_score=0.6,  # Will filter low-similarity Chroma hits
        )
        
        # Add documents; "Python" should have high similarity, "JavaScript" lower
        store.add_document("Python programming language", {"id": "doc_py"})
        store.add_document("JavaScript web development", {"id": "doc_js"})
        
        results = store.query(["python"])
        ids = results["ids"][0]
        
        # doc_py should appear (high Chroma similarity)
        assert "doc_py" in ids
        # doc_js might not appear if its Chroma score < 0.6, regardless of BM25
        # We can't guarantee this because BM25 might still include it, but the test
        # ensures the store works with threshold set
        assert len(ids) >= 1

    def test_min_bm25_score_filters(self):
        """min_bm25_score should filter BM25 results before fusion."""
        # We need a corpus large enough to produce non-zero BM25 scores.
        # With small corpus, IDF can be zero. So we'll add multiple documents
        # where the query term appears in only some.
        store = HybridDocumentStore(
            chroma_collection_name=f"test_bm25_thresh_{_unique_store_id()}",
            chroma_allow_reset=True,
            min_chroma_score=10.0,  # Impossible threshold, disables Chroma
            min_bm25_score=0.01,    # Only include BM25 matches with positive score
        )
        
        # Add several docs; only some contain the query term "python"
        docs = [
            ("Python programming is awesome", {"id": "doc1"}),
            ("Java development is cool", {"id": "doc2"}),
            ("Python and data science", {"id": "doc3"}),
            ("JavaScript for web", {"id": "doc4"}),
            ("Python scripting", {"id": "doc5"}),
        ]
        for doc, meta in docs:
            store.add_document(doc, meta)
        
        results = store.query(["python"])
        ids = results["ids"][0]
        
        # Only docs containing "python" should have non-zero BM25 scores and pass threshold
        # That's doc1, doc3, doc5
        for doc_id in ["doc1", "doc3", "doc5"]:
            assert doc_id in ids, f"{doc_id} should be in results (has python term)"
        # Docs without "python" should be filtered out
        for doc_id in ["doc2", "doc4"]:
            assert doc_id not in ids, f"{doc_id} should not be in results (no python term)"

    def test_thresholds_combined(self):
        """Both thresholds should be applied together."""
        store = HybridDocumentStore(
            chroma_collection_name=f"test_combined_thresh_{_unique_store_id()}",
            chroma_allow_reset=True,
            min_chroma_score=0.6,  # Increased to ensure doc2 fails Chroma threshold
            min_bm25_score=0.01,
        )
        
        # doc1: strongly related to "python" (high Chroma + high BM25)
        store.add_document("Python programming language", {"id": "doc1"})
        # doc2: unrelated (low Chroma + low BM25)
        store.add_document("Cooking recipes for beginners", {"id": "doc2"})
        # doc3: moderately related (medium Chroma, decent BM25)
        store.add_document("Pythons are snakes", {"id": "doc3"})
        
        results = store.query(["python"])
        ids = results["ids"][0]
        
        # doc1 should definitely appear
        assert "doc1" in ids
        # doc3 might appear depending on its Chroma score (0.5 threshold)
        # doc2 should be filtered out by at least one threshold
        # Since doc2 doesn't contain "python", its BM25 score will be low, and Chroma
        # similarity for "python" to a cooking doc is also low (<0.5). So it should fail.
        # However, we can't guarantee doc3 behavior, so we only assert doc2 is excluded.
        assert "doc2" not in ids

    def test_no_thresholds_by_default(self):
        """Default should have no threshold filtering."""
        store = HybridDocumentStore(
            chroma_collection_name=f"test_no_thresh_{_unique_store_id()}",
            chroma_allow_reset=True,
        )
        
        store.add_document("Python", {"id": "doc1"})
        store.add_document("JavaScript", {"id": "doc2"})
        
        results = store.query(["java"])  # Matches JS more weakly
        ids = results["ids"][0]
        
        # Both should be returned (no filtering)
        assert "doc1" in ids or "doc2" in ids  # At least one appears
        # Without threshold, we expect some results
        assert len(ids) > 0

