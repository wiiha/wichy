"""
Document store package - unified interface for multiple backends.
"""

from wichy.document_store.core import DocumentStore
from wichy.document_store.chromadb.chromadb_document_store import ChromaDocumentStore
from wichy.document_store.bm25.bm25_document_store import BM25DocumentStore
from wichy.document_store.hybrid.hybrid_document_store import HybridDocumentStore

__all__ = [
    'DocumentStore',
    'ChromaDocumentStore',
    'BM25DocumentStore',
    'HybridDocumentStore',
]
