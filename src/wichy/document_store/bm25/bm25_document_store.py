"""
BM25 (sparse retrieval) document store implementation.

Uses rank_bm25 for BM25 scoring. Stores documents in memory with optional
persistence via pickle. Supports tokenization with basic preprocessing
(lowercase, stopwords optional).

Note: This is a sparse retrieval store - it does NOT use embeddings.
The `query()` method returns BM25 scores instead of vector similarity.
"""

import pickle
from pathlib import Path
from typing import Dict, List, Optional

from rank_bm25 import BM25Okapi

from wichy.document_store.core import DocumentStore
from wichy.helpers.gen_id import gen_id


class BM25DocumentStore(DocumentStore):
    """
    BM25-based document store for sparse retrieval.

    Stores documents in memory and uses BM25 algorithm for scoring.
    Supports English stopword removal and simple tokenization by default.
    """

    def __init__(
        self,
        index_path: Optional[str] = None,
        k1: float = 1.5,
        b: float = 0.75,
        tokenizer: Optional[callable] = None,
        stopwords: Optional[set] = None,
    ):
        """
        Initialize BM25DocumentStore.

        Args:
            index_path: Optional path to load/save index (persistence)
            k1: BM25 parameter (term frequency saturation, default 1.5)
            b: BM25 parameter (length normalization, default 0.75)
            tokenizer: Optional custom tokenizer function (word -> list of tokens)
            stopwords: Optional set of stopwords to remove during tokenization
        """
        # Simple default tokenizer: lowercase + split on whitespace
        self.tokenizer = tokenizer or (lambda text: text.lower().split())
        self.stopwords = stopwords or set()

        # BM25 parameters
        self.k1 = k1
        self.b = b

        # Storage
        self._documents: Dict[str, str] = {}  # id -> document
        self._metadata: Dict[str, Dict] = {}  # id -> metadata
        self._bm25: Optional[BM25Okapi] = None
        self._tokenized_corpus: List[List[str]] = []
        self._id_to_idx: Dict[str, int] = {}  # id -> index in tokenized corpus
        self._idx_to_id: List[str] = []  # index -> id

        # Persistence
        self.index_path = Path(index_path) if index_path else None
        if self.index_path and self.index_path.exists():
            self._load()
        else:
            self._rebuild_index()

    def _tokenize(self, text: str) -> List[str]:
        """Tokenize text with optional stopword removal."""
        tokens = self.tokenizer(text)
        if self.stopwords:
            tokens = [t for t in tokens if t not in self.stopwords]
        return tokens

    def _rebuild_index(self):
        """Rebuild BM25 index from current documents."""
        self._tokenized_corpus = []
        self._id_to_idx = {}
        self._idx_to_id = []

        # Sort IDs to have deterministic order
        sorted_ids = sorted(self._documents.keys())
        for idx, doc_id in enumerate(sorted_ids):
            doc = self._documents[doc_id]
            tokens = self._tokenize(doc)
            self._tokenized_corpus.append(tokens)
            self._id_to_idx[doc_id] = idx
            self._idx_to_id.append(doc_id)

        # Only create BM25 index if we have documents
        if self._tokenized_corpus:
            self._bm25 = BM25Okapi(self._tokenized_corpus, k1=self.k1, b=self.b)
        else:
            self._bm25 = None

    def _save(self):
        """Save index to disk if index_path is set."""
        if not self.index_path:
            return
        self.index_path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "documents": self._documents,
            "metadata": self._metadata,
            "tokenized_corpus": self._tokenized_corpus,
            "id_to_idx": self._id_to_idx,
            "idx_to_id": self._idx_to_id,
            "k1": self.k1,
            "b": self.b,
        }
        with open(self.index_path, "wb") as f:
            pickle.dump(data, f)

    def _load(self):
        """Load index from disk."""
        with open(self.index_path, "rb") as f:
            data = pickle.load(f)
        self._documents = data["documents"]
        self._metadata = data["metadata"]
        self._tokenized_corpus = data["tokenized_corpus"]
        self._id_to_idx = data["id_to_idx"]
        self._idx_to_id = data["idx_to_id"]
        self.k1 = data.get("k1", self.k1)
        self.b = data.get("b", self.b)
        # Only create BM25 index if we have documents
        if self._tokenized_corpus:
            self._bm25 = BM25Okapi(self._tokenized_corpus, k1=self.k1, b=self.b)
        else:
            self._bm25 = None

    def add_document(self, document: str, metadata: Dict) -> str:
        """Add a document to the store.

        Args:
            document: Text content to add
            metadata: Dictionary of metadata (may include 'id')

        Returns:
            The document ID
        """
        doc_id = metadata.get("id", gen_id())

        self._documents[doc_id] = document
        self._metadata[doc_id] = {k: v for k, v in metadata.items() if k != "id"}

        # Rebuild index (naive but simple)
        self._rebuild_index()
        self._save()

        return doc_id

    def get_document(self, doc_id: str) -> Optional[Dict]:
        """Get a single document by ID."""
        if doc_id not in self._documents:
            return None

        return {
            "id": doc_id,
            "document": self._documents[doc_id],
            "metadata": self._metadata.get(doc_id, {}),
        }

    def get_documents(self, doc_ids: List[str]) -> Dict:
        """Get multiple documents by IDs."""
        ids = []
        documents = []
        metadatas = []
        for doc_id in doc_ids:
            if doc_id in self._documents:
                ids.append(doc_id)
                documents.append(self._documents[doc_id])
                metadatas.append(self._metadata.get(doc_id, {}))

        return {"ids": ids, "documents": documents, "metadatas": metadatas}

    def update_metadata(self, doc_id: str, metadata: Dict, merge: bool = True):
        """Update metadata for a document."""
        if doc_id not in self._documents:
            raise ValueError(f"Document {doc_id} not found")

        if merge:
            self._metadata[doc_id] = {**self._metadata.get(doc_id, {}), **metadata}
        else:
            self._metadata[doc_id] = metadata

        self._save()

    def delete_document(self, doc_id: str) -> bool:
        """Delete a document from the store."""
        if doc_id in self._documents:
            del self._documents[doc_id]
            del self._metadata[doc_id]
            self._rebuild_index()
            self._save()
            return True
        return False

    def query(
        self, query_texts: List[str], n_results: int = 5, where: Optional[Dict] = None
    ) -> Dict:
        """
        Query documents by BM25 score with optional metadata filter.

        Args:
            query_texts: List of query strings (usually single query)
            n_results: Maximum number of results
            where: Optional metadata filter (applied after BM25 scoring)

        Returns:
            Dict with 'ids', 'documents', 'metadatas', 'scores'
        """
        if not query_texts:
            return {"ids": [[]], "documents": [[]], "metadatas": [[]], "scores": [[]]}

        query = query_texts[0]  # Take first query
        query_tokens = self._tokenize(query)

        # Get BM25 scores for all documents
        if not self._tokenized_corpus:
            return {"ids": [[]], "documents": [[]], "metadatas": [[]], "scores": [[]]}

        doc_scores = self._bm25.get_scores(query_tokens)

        # Pair IDs with scores
        scored_ids = []
        for idx, score in enumerate(doc_scores):
            doc_id = self._idx_to_id[idx]
            # Apply metadata filter if provided
            if where:
                meta = self._metadata.get(doc_id, {})
                match = all(meta.get(k) == v for k, v in where.items())
                if not match:
                    continue
            scored_ids.append((doc_id, score))

        # Sort by score descending
        scored_ids.sort(key=lambda x: x[1], reverse=True)
        top_k = scored_ids[:n_results]

        # Build result arrays
        ids = [[doc_id for doc_id, _ in top_k]]
        documents = [[self._documents[doc_id] for doc_id, _ in top_k]]
        metadatas = [[self._metadata.get(doc_id, {}) for doc_id, _ in top_k]]
        scores = [[score for _, score in top_k]]

        return {
            "ids": ids,
            "documents": documents,
            "metadatas": metadatas,
            "scores": scores,
        }

    def list(self, limit: Optional[int] = None, where: Optional[Dict] = None) -> Dict:
        """List documents with optional filters."""
        ids = []
        documents = []
        metadatas = []

        for doc_id in sorted(self._documents.keys()):
            meta = self._metadata.get(doc_id, {})
            if where:
                if not all(meta.get(k) == v for k, v in where.items()):
                    continue
            ids.append(doc_id)
            documents.append(self._documents[doc_id])
            metadatas.append(meta)

            if limit and len(ids) >= limit:
                break

        return {"ids": ids, "documents": documents, "metadatas": metadatas}

    def count(self) -> int:
        """Total number of documents."""
        return len(self._documents)

    def clear(self):
        """Remove all documents."""
        self._documents.clear()
        self._metadata.clear()
        self._rebuild_index()
        if self.index_path and self.index_path.exists():
            self.index_path.unlink()
        self._save()

    def reset(self):
        """Reset the store (same as clear for BM25)."""
        self.clear()
