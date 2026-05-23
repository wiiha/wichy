"""
Hybrid document store using both ChromaDB (dense) and BM25 (sparse) retrieval.
Combines results using Reciprocal Rank Fusion (RRF) for optimal retrieval performance.
"""

from typing import Callable, Dict, List, Optional

from wichy.document_store.bm25.bm25_document_store import BM25DocumentStore
from wichy.document_store.chromadb.chromadb_document_store import ChromaDocumentStore
from wichy.document_store.core import DocumentStore
from wichy.helpers.gen_id import gen_id


class HybridDocumentStore(DocumentStore):
    """
    Hybrid document store that combines dense (ChromaDB) and sparse (BM25) retrieval.

    Uses Reciprocal Rank Fusion (RRF) to merge results from both backends:
    score = sum(1 / (k + rank)) where k=60 (standard RRF parameter)

    Optionally applies score thresholds to filter out weak matches before fusion.

    This provides:
    - Semantic search from embeddings (ChromaDB)
    - Keyword matching from BM25
    - Better recall and precision than either alone
    """

    def __init__(
        self,
        chroma_collection_name: Optional[str] = None,
        chroma_model_name: str = "paraphrase-MiniLM-L6-v2",
        chroma_path: Optional[str] = None,
        chroma_allow_reset: bool = False,
        bm25_index_path: Optional[str] = None,
        bm25_k1: float = 1.5,
        bm25_b: float = 0.75,
        bm25_tokenizer: Optional[Callable[[str], List[str]]] = None,
        bm25_stopwords: Optional[set] = None,
        rrf_k: int = 60,
        min_chroma_score: Optional[float] = 0.55,
        min_bm25_score: Optional[float] = None,
    ):
        """
        Initialize hybrid document store with both backends.

        Args:
            chroma_collection_name: Name for ChromaDB collection (auto-generated if None)
            chroma_model_name: Embedding model for ChromaDB
            chroma_path: Optional persistent path for ChromaDB
            chroma_allow_reset: Whether to allow ChromaDB reset
            bm25_index_path: Optional persistence path for BM25 index
            bm25_k1: BM25 term frequency saturation parameter
            bm25_b: BM25 length normalization parameter
            bm25_tokenizer: Custom tokenizer for BM25
            bm25_stopwords: Set of stopwords for BM25
            rrf_k: RRF parameter (k) - lower values give more weight to top ranks (default 60)
            min_chroma_score: Minimum cosine similarity score (0-1) to include from ChromaDB.
                              If None, no threshold. For sentence-transformers, try 0.5-0.6.
            min_bm25_score: Minimum BM25 score to include. If None, no threshold.
                              BM25 scores vary; tune on your corpus.
        """
        self.rrf_k = rrf_k
        self.min_chroma_score = min_chroma_score
        self.min_bm25_score = min_bm25_score

        # Initialize both backends
        self.chroma = ChromaDocumentStore(
            collection_name=chroma_collection_name or f"hybrid_chroma_{gen_id()}",
            model_name=chroma_model_name,
            path_persistent_store=chroma_path,
            allow_reset=chroma_allow_reset,
        )

        self.bm25 = BM25DocumentStore(
            index_path=bm25_index_path,
            k1=bm25_k1,
            b=bm25_b,
            tokenizer=bm25_tokenizer,
            stopwords=bm25_stopwords,
        )

    def _compute_rrf_scores(
        self, chroma_results: Dict, bm25_results: Dict, n_results: int
    ) -> List[tuple]:
        """
        Combine ChromaDB and BM25 results using Reciprocal Rank Fusion.

        RRF score = sum(1 / (k + rank)) for each retrieval method
        where k is typically 60 (prevents giving too much weight to very low ranks).

        Optional: Filters out documents that don't meet score thresholds before fusion.
        """
        # Extract IDs, scores, and build rank maps
        chroma_ids = []
        chroma_scores = {}
        if "ids" in chroma_results and chroma_results["ids"]:
            chroma_ids = chroma_results["ids"][0]  # First query's results
            chroma_scores = (
                dict(zip(chroma_ids, chroma_results["scores"][0]))
                if chroma_results.get("scores")
                else {}
            )

        bm25_ids = []
        bm25_scores = {}
        if "ids" in bm25_results and bm25_results["ids"]:
            bm25_ids = bm25_results["ids"][0]
            bm25_scores = (
                dict(zip(bm25_ids, bm25_results["scores"][0]))
                if bm25_results.get("scores")
                else {}
            )

        # Apply score thresholds BEFORE building rank maps
        if self.min_chroma_score is not None:
            chroma_ids = [
                doc_id
                for doc_id in chroma_ids
                if chroma_scores.get(doc_id, 0) >= self.min_chroma_score
            ]
        if self.min_bm25_score is not None:
            bm25_ids = [
                doc_id
                for doc_id in bm25_ids
                if bm25_scores.get(doc_id, 0) >= self.min_bm25_score
            ]

        # Create rank maps: doc_id -> rank (0-indexed)
        chroma_ranks = {doc_id: idx for idx, doc_id in enumerate(chroma_ids)}
        bm25_ranks = {doc_id: idx for idx, doc_id in enumerate(bm25_ids)}

        # Collect all unique document IDs
        all_ids = set(chroma_ranks.keys()) | set(bm25_ranks.keys())

        # Compute RRF scores
        rrf_scores = []
        for doc_id in all_ids:
            score = 0.0

            # Contribution from ChromaDB
            if doc_id in chroma_ranks:
                rank = chroma_ranks[doc_id] + 1  # Convert to 1-indexed for formula
                score += 1.0 / (self.rrf_k + rank)

            # Contribution from BM25
            if doc_id in bm25_ranks:
                rank = bm25_ranks[doc_id] + 1
                score += 1.0 / (self.rrf_k + rank)

            rrf_scores.append((doc_id, score))

        # Sort by RRF score descending
        rrf_scores.sort(key=lambda x: x[1], reverse=True)

        # Return top N IDs with scores
        return rrf_scores[:n_results]

    def add_document(self, document: str, metadata: Dict) -> str:
        """
        Add a document to both ChromaDB and BM25 stores.

        Returns the document ID (generated if not provided in metadata).
        """
        doc_id = metadata.get("id", gen_id())

        # Ensure ID is in metadata for both stores
        meta_with_id = {**metadata, "id": doc_id} if "id" not in metadata else metadata

        # Add to both backends
        self.chroma.add_document(document, meta_with_id)
        self.bm25.add_document(document, meta_with_id)

        return doc_id  # type: ignore[no-any-return]

    def get_document(self, doc_id: str) -> Optional[Dict]:
        """
        Get a document by ID.
        Returns from ChromaDB (both stores are kept in sync, so either would work).
        """
        return self.chroma.get_document(doc_id)

    def get_documents(self, doc_ids: List[str]) -> Dict:
        """
        Get multiple documents by IDs.
        Uses ChromaDB as the source (both stores are synchronized).
        """
        return self.chroma.get_documents(doc_ids)

    def update_metadata(self, doc_id: str, metadata: Dict, merge: bool = True):
        """
        Update metadata for a document in both backends.

        Raises:
            ValueError: If document not found
        """
        self.chroma.update_metadata(doc_id, metadata, merge=merge)
        self.bm25.update_metadata(doc_id, metadata, merge=merge)

    def delete_document(self, doc_id: str) -> bool:
        """
        Delete a document from both backends.

        Returns True if document was deleted, False if not found in either store.
        """
        deleted_chroma = self.chroma.delete_document(doc_id)
        deleted_bm25 = self.bm25.delete_document(doc_id)
        # Return True if at least one store had the document
        return deleted_chroma or deleted_bm25

    def query(
        self, query_texts: List[str], n_results: int = 5, where: Optional[Dict] = None
    ) -> Dict:
        """
        Hybrid query: combines dense (ChromaDB) and sparse (BM25) retrieval
        using Reciprocal Rank Fusion (RRF).

        Args:
            query_texts: List of query strings (usually single query)
            n_results: Maximum number of results to return
            where: Optional metadata filter (applied to both stores, then fused)

        Returns:
            Dict with keys: 'ids', 'documents', 'metadatas', 'scores' (RRF scores)
            - 'scores': RRF scores (higher = more relevant)
        """
        if not query_texts:
            return {"ids": [[]], "documents": [[]], "metadatas": [[]], "scores": [[]]}

        # Query both backends with the same parameters
        chroma_results = self.chroma.query(
            query_texts=query_texts,
            n_results=n_results * 2,  # Get more to allow fusion overlap
            where=where,
        )
        bm25_results = self.bm25.query(
            query_texts=query_texts, n_results=n_results * 2, where=where
        )

        # If both return empty, return empty result
        if not chroma_results["ids"][0] and not bm25_results["ids"][0]:
            return {"ids": [[]], "documents": [[]], "metadatas": [[]], "scores": [[]]}

        # Compute RRF fusion
        fused_results = self._compute_rrf_scores(
            chroma_results, bm25_results, n_results
        )

        if not fused_results:
            return {"ids": [[]], "documents": [[]], "metadatas": [[]], "scores": [[]]}

        # Build a mapping from doc_id to (doc, meta) via ChromaDB
        fused_ids = [doc_id for doc_id, _ in fused_results]
        full_data = self.chroma.get_documents(fused_ids)
        lookup = {}
        for doc_id, doc, meta in zip(
            full_data["ids"], full_data["documents"], full_data["metadatas"]
        ):
            lookup[doc_id] = (doc, meta)

        # Filter out any missing documents and pair with scores
        paired = []
        for doc_id, score in fused_results:
            if doc_id in lookup:
                doc, meta = lookup[doc_id]
                paired.append((doc_id, doc, meta, score))

        # Double-sort by score descending for safety (fused_results should already be sorted)
        paired.sort(key=lambda x: x[3], reverse=True)

        # Unpack into final structure
        ids = [[p[0] for p in paired]]
        documents = [[p[1] for p in paired]]
        metadatas = [[p[2] for p in paired]]
        scores = [[p[3] for p in paired]]

        return {
            "ids": ids,
            "documents": documents,
            "metadatas": metadatas,
            "scores": scores,
        }

    def list(self, limit: Optional[int] = None, where: Optional[Dict] = None) -> Dict:
        """
        List all documents with optional filter.
        Uses ChromaDB as source (both stores are synchronized).
        """
        return self.chroma.list(limit=limit, where=where)

    def count(self) -> int:
        """Return total number of documents (same for both stores)."""
        return self.chroma.count()

    def clear(self):
        """Remove all documents from both backends."""
        self.chroma.clear()
        self.bm25.clear()

    def reset(self):
        """Reset both backends."""
        self.chroma.reset()
        self.bm25.reset()
