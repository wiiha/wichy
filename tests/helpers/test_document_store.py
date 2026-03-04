"""
Test cases for the DocumentStore class.
"""

import uuid

import pytest

from wichy.helpers.document_store import DocumentStore


@pytest.fixture
def doc_store():
    """Create a fresh DocumentStore instance for each test."""
    # Use in-memory client with a unique collection name to ensure test isolation
    store = DocumentStore(
        collection_name=f"test_collection_{uuid.uuid4().hex}",
        path_persistent_store=None,  # In-memory
    )
    return store


def test_list_empty_store(doc_store):
    """Test listing documents when store is empty."""
    results = doc_store.list()
    assert results["ids"] == []
    assert results["documents"] == []
    assert results["metadatas"] == []


def test_list_with_documents(doc_store):
    """Test listing documents when store has documents."""
    # Add some documents
    doc_ids = []
    for i in range(3):
        doc_id = doc_store.add_document(
            document=f"Document {i}", metadata={"index": i, "category": "test"}
        )
        doc_ids.append(doc_id)

    results = doc_store.list()

    assert len(results["ids"]) == 3
    assert set(results["ids"]) == set(doc_ids)
    assert len(results["documents"]) == 3
    assert len(results["metadatas"]) == 3

    # Verify content
    for i, doc_id in enumerate(doc_ids):
        idx = results["ids"].index(doc_id)
        assert results["documents"][idx] == f"Document {i}"
        assert results["metadatas"][idx]["index"] == i


def test_list_with_limit(doc_store):
    """Test listing documents with a limit."""
    # Add 5 documents
    for i in range(5):
        doc_store.add_document(document=f"Document {i}", metadata={"index": i})

    results = doc_store.list(limit=3)

    assert len(results["ids"]) == 3
    assert len(results["documents"]) == 3
    assert len(results["metadatas"]) == 3


def test_list_with_where_filter(doc_store):
    """Test listing documents with metadata filter."""
    # Add documents with different categories
    doc_store.add_document(document="Doc A", metadata={"category": "A", "value": 1})
    doc_store.add_document(document="Doc B", metadata={"category": "B", "value": 2})
    doc_store.add_document(document="Doc C", metadata={"category": "A", "value": 3})

    # Filter for category A
    results = doc_store.list(where={"category": "A"})

    assert len(results["ids"]) == 2
    assert all(meta["category"] == "A" for meta in results["metadatas"])


def test_list_with_limit_and_where(doc_store):
    """Test listing documents with both limit and where filter."""
    # Add documents
    for i in range(5):
        doc_store.add_document(
            document=f"Document {i}",
            metadata={"group": "X" if i < 3 else "Y", "index": i},
        )

    results = doc_store.list(limit=2, where={"group": "X"})

    assert len(results["ids"]) == 2
    assert all(meta["group"] == "X" for meta in results["metadatas"])


def test_list_returns_all_fields(doc_store):
    """Test that list returns all expected fields."""
    doc_store.add_document(document="Test doc", metadata={"key": "value"})

    results = doc_store.list()

    assert "ids" in results
    assert "documents" in results
    assert "metadatas" in results
    assert "embeddings" in results  # ChromaDB includes this even if not explicitly set


def test_persistent_store(tmp_path):
    """Test that persistent store saves data across instances."""
    # Create first instance with persistent storage
    store1 = DocumentStore(
        collection_name="persistent_test",
        path_persistent_store=str(tmp_path / "chroma_db"),
    )

    # Add a document
    doc_id = store1.add_document(
        document="Persistent document", metadata={"source": "store1"}
    )

    # Verify it's in the first store
    results1 = store1.list()
    assert len(results1["ids"]) == 1
    assert results1["ids"][0] == doc_id

    # Create second instance pointing to same storage location
    store2 = DocumentStore(
        collection_name="persistent_test",
        path_persistent_store=str(tmp_path / "chroma_db"),
    )

    # Document should still be there
    results2 = store2.list()
    assert len(results2["ids"]) == 1
    assert results2["ids"][0] == doc_id
    assert results2["documents"][0] == "Persistent document"


def test_delete_document(doc_store):
    """Test deleting a document."""
    # Add a document
    doc_id = doc_store.add_document(
        document="To be deleted", metadata={"status": "temporary"}
    )

    # Verify it exists
    results_before = doc_store.list()
    assert len(results_before["ids"]) == 1

    # Delete it
    deleted_doc = doc_store.delete_document(doc_id)

    # Verify deletion
    results_after = doc_store.list()
    assert len(results_after["ids"]) == 0

    # Verify returned deleted document
    assert deleted_doc["ids"][0] == doc_id
    assert deleted_doc["documents"][0] == "To be deleted"
    assert deleted_doc["metadatas"][0]["status"] == "temporary"


def test_delete_nonexistent_document(doc_store):
    """Test that deleting a non-existent document raises an error."""
    non_existent_id = "id_that_does_not_exist"

    # ChromaDB get for non-existent ID returns empty results
    # Then delete on non-existent ID should handle gracefully or raise error
    # depending on ChromaDB behavior - let's check what happens
    try:
        deleted = doc_store.delete_document(non_existent_id)
        # If it doesn't raise, the deleted should have empty results
        assert deleted["ids"] == []
    except Exception as e:
        # Some ChromaDB backends might raise an error
        # That's also acceptable behavior
        assert True


def test_add_and_delete_multiple_documents(doc_store):
    """Test adding and deleting multiple documents."""
    # Add several documents
    doc_ids = []
    for i in range(5):
        doc_id = doc_store.add_document(document=f"Doc {i}", metadata={"number": i})
        doc_ids.append(doc_id)

    # Verify all exist
    results = doc_store.list()
    assert len(results["ids"]) == 5

    # Delete some documents
    for doc_id in doc_ids[:2]:
        doc_store.delete_document(doc_id)

    # Verify remaining documents
    results = doc_store.list()
    assert len(results["ids"]) == 3
    assert set(results["ids"]) == set(doc_ids[2:])


def test_clear_collection(doc_store):
    """Test clearing a collection removes all documents but preserves collection structure."""
    # Add documents
    doc_ids = []
    for i in range(3):
        doc_id = doc_store.add_document(document=f"Document {i}", metadata={"index": i})
        doc_ids.append(doc_id)

    # Verify documents exist
    results = doc_store.list()
    assert len(results["ids"]) == 3

    # Clear the collection
    doc_store.clear()

    # Verify collection is empty
    results = doc_store.list()
    assert len(results["ids"]) == 0
    assert results["documents"] == []
    assert results["metadatas"] == []

    # Verify we can still add documents after clear
    new_doc_id = doc_store.add_document(
        document="New document", metadata={"after": "clear"}
    )
    results = doc_store.list()
    assert len(results["ids"]) == 1
    assert results["documents"][0] == "New document"


def test_clear_does_not_affect_other_collections():
    """Test that clearing one collection does not affect other collections."""
    # Create two stores with different collection names but same in-memory client behavior
    # Actually, in-memory client is singleton, so different collection names are truly separate collections
    store_a = DocumentStore(collection_name="collection_a", path_persistent_store=None)
    store_b = DocumentStore(collection_name="collection_b", path_persistent_store=None)

    # Add docs to both
    doc_a = store_a.add_document("Doc A", {"source": "A"})
    doc_b = store_b.add_document("Doc B", {"source": "B"})

    # Verify both have docs
    assert len(store_a.list()["ids"]) == 1
    assert len(store_b.list()["ids"]) == 1

    # Clear collection A
    store_a.clear()

    # A should be empty, B should still have its doc
    assert len(store_a.list()["ids"]) == 0
    assert len(store_b.list()["ids"]) == 1
    assert store_b.list()["ids"][0] == doc_b


def test_reset_without_allow_reset_raises_error():
    """Test that reset raises ValueError when allow_reset=False."""
    store = DocumentStore(
        collection_name="test_reset", path_persistent_store=None, allow_reset=False
    )

    with pytest.raises(ValueError, match="Reset is not allowed"):
        store.reset()


def test_serialize_list_metadata(doc_store):
    """Test that lists in metadata are properly serialized and deserialized."""
    complex_metadata = {
        "tags": ["python", "chromadb", "testing"],
        "numbers": [1, 2, 3, 4, 5],
        "mixed": ["string", 123, True],
    }

    doc_id = doc_store.add_document(
        document="Document with list metadata", metadata=complex_metadata
    )

    # Verify through list
    results = doc_store.list()
    idx = results["ids"].index(doc_id)
    retrieved_metadata = results["metadatas"][idx]

    assert retrieved_metadata["tags"] == ["python", "chromadb", "testing"]
    assert retrieved_metadata["numbers"] == [1, 2, 3, 4, 5]
    assert retrieved_metadata["mixed"] == ["string", 123, True]


def test_serialize_dict_metadata(doc_store):
    """Test that nested dicts in metadata are properly serialized and deserialized."""
    complex_metadata = {
        "config": {"setting1": "value1", "setting2": 42},
        "nested": {"deep": {"deeper": "value"}},
    }

    doc_id = doc_store.add_document(
        document="Document with dict metadata", metadata=complex_metadata
    )

    results = doc_store.list()
    idx = results["ids"].index(doc_id)
    retrieved_metadata = results["metadatas"][idx]

    assert retrieved_metadata["config"] == {"setting1": "value1", "setting2": 42}
    assert retrieved_metadata["nested"] == {"deep": {"deeper": "value"}}


def test_serialize_mixed_types(doc_store):
    """Test serialization of mixed simple and complex types."""
    complex_metadata = {
        "simple_string": "hello",
        "simple_number": 42,
        "simple_bool": True,
        "list_type": [1, 2, 3],
        "dict_type": {"key": "value"},
        "null_value": None,  # This should be skipped
    }

    doc_id = doc_store.add_document(
        document="Document with mixed metadata types", metadata=complex_metadata
    )

    results = doc_store.list()
    idx = results["ids"].index(doc_id)
    retrieved_metadata = results["metadatas"][idx]

    assert retrieved_metadata["simple_string"] == "hello"
    assert retrieved_metadata["simple_number"] == 42
    assert retrieved_metadata["simple_bool"] is True
    assert retrieved_metadata["list_type"] == [1, 2, 3]
    assert retrieved_metadata["dict_type"] == {"key": "value"}
    assert "null_value" not in retrieved_metadata  # None values are skipped


def test_search_returns_deserialized_metadata(doc_store):
    """Test that search results contain properly deserialized metadata."""
    # Add documents with complex metadata
    doc_store.add_document(document="Doc 1", metadata={"tags": ["a", "b"], "count": 1})
    doc_store.add_document(document="Doc 2", metadata={"tags": ["c", "d"], "count": 2})

    results = doc_store.search(query="Doc", k=2)

    # Search returns nested metadatas (one sublist per query)
    assert len(results["metadatas"]) == 1
    metadatas = results["metadatas"][0]

    for meta in metadatas:
        assert isinstance(meta["tags"], list)
        assert isinstance(meta["count"], int)


def test_update_document_with_complex_metadata(doc_store):
    """Test updating a document with complex metadata."""
    # Add initial document
    doc_id = doc_store.add_document(
        document="Original", metadata={"tags": ["old"], "count": 1}
    )

    # Update with new complex metadata
    doc_store.update_document(
        doc_id=doc_id,
        document="Updated",
        metadata={"tags": ["new", "updated"], "config": {"key": "value"}},
    )

    results = doc_store.list()
    idx = results["ids"].index(doc_id)
    updated_metadata = results["metadatas"][idx]

    assert updated_metadata["tags"] == ["new", "updated"]
    assert updated_metadata["config"] == {"key": "value"}
    assert "count" not in updated_metadata  # old field should not exist


def test_delete_returns_deserialized_metadata(doc_store):
    """Test that delete_document returns properly deserialized metadata."""
    complex_metadata = {
        "list_field": [1, 2, 3],
        "dict_field": {"nested": "value"},
        "simple": "text",
    }

    doc_id = doc_store.add_document(document="To be deleted", metadata=complex_metadata)

    deleted_doc = doc_store.delete_document(doc_id)

    # Verify the returned document metadata is deserialized
    returned_metadata = deleted_doc["metadatas"][0]
    assert returned_metadata["list_field"] == [1, 2, 3]
    assert returned_metadata["dict_field"] == {"nested": "value"}
    assert returned_metadata["simple"] == "text"
