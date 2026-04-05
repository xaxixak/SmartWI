"""
Workspace Intelligence Layer - Hybrid Search (Story 5.3 v2)
============================================================

Hybrid search engine combining three strategies via Reciprocal Rank Fusion:
  1. BM25 fulltext search   (via rank_bm25 library)
  2. Semantic embedding search (via sentence-transformers)
  3. Exact substring match  (boosted to top)

RRF formula: score(d) = sum(1 / (k + rank_i(d))) for each ranker i
This naturally combines rankings without needing score normalization.

The index is built lazily on first search if not already built.
Call build_index() explicitly after bulk graph mutations.

Inspired by GitNexus's hybrid search architecture.
"""

from __future__ import annotations

import math
import re
import sys
import warnings
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

# ---------------------------------------------------------------------------
# Project imports
# ---------------------------------------------------------------------------
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from graph_store import GraphStore  # noqa: E402
from ontology import GraphNode, NodeType, Tier  # noqa: E402


# =============================================================================
# OPTIONAL DEPENDENCIES (graceful degradation)
# =============================================================================

_HAS_BM25 = False
_HAS_EMBEDDINGS = False

try:
    from rank_bm25 import BM25Okapi
    _HAS_BM25 = True
except ImportError:
    pass

try:
    from sentence_transformers import SentenceTransformer
    import numpy as np
    _HAS_EMBEDDINGS = True
except ImportError:
    pass


# =============================================================================
# CONSTANTS
# =============================================================================

# RRF constant k — standard value from the original RRF paper
RRF_K = 60

# Embedding model — small, fast, good for code
EMBEDDING_MODEL = "all-MiniLM-L6-v2"

# Maximum nodes to embed in one batch
EMBEDDING_BATCH_SIZE = 256

STOPWORDS: Set[str] = {
    "the", "a", "an", "is", "are", "was", "were", "in", "on", "at", "to",
    "for", "of", "with", "and", "or", "not", "this", "that", "it", "from",
    "by", "as",
}


# =============================================================================
# DATA MODEL
# =============================================================================

@dataclass
class SearchResult:
    """A single search result with scoring metadata."""
    node: GraphNode
    score: float          # 0.0 to 1.0, higher = better match
    match_type: str       # "exact", "bm25", "semantic", "keyword", "rrf"
    matched_field: str    # "name", "description", "tags", "metadata"


# =============================================================================
# TOKENIZER
# =============================================================================

_TOKEN_RE = re.compile(r"[a-zA-Z0-9]+")


def tokenize(text: str) -> List[str]:
    """Split text into lowercase tokens, handling camelCase and snake_case."""
    expanded = re.sub(r"([a-z])([A-Z])", r"\1 \2", text)
    expanded = expanded.replace("_", " ").replace("-", " ")
    tokens = _TOKEN_RE.findall(expanded.lower())
    return [t for t in tokens if t not in STOPWORDS and len(t) > 1]


# =============================================================================
# NODE TEXT EXTRACTION
# =============================================================================

def _node_text(node: GraphNode) -> str:
    """Concatenate all searchable text fields of a node."""
    parts: List[str] = [node.name]
    if node.description:
        parts.append(node.description)
    if node.tags:
        parts.extend(node.tags)
    for key in ("http_method", "http_path", "framework", "orm", "table_name",
                "trigger", "prefix"):
        val = node.metadata.get(key)
        if val and isinstance(val, str):
            parts.append(val)
    return " ".join(parts)


def _node_tokens(node: GraphNode) -> List[str]:
    """Tokenize all searchable text fields of a node."""
    return tokenize(_node_text(node))


# =============================================================================
# TF-IDF ENGINE (stdlib fallback when rank_bm25 not installed)
# =============================================================================

class _TfidfEngine:
    """Minimal TF-IDF implementation using only stdlib."""

    def __init__(self) -> None:
        self._vocab: Dict[str, int] = {}
        self._idf: Dict[int, float] = {}
        self._doc_vectors: List[Dict[int, float]] = []
        self._doc_norms: List[float] = []

    def fit(self, corpus: List[List[str]]) -> None:
        n_docs = len(corpus)
        if n_docs == 0:
            return

        df: Counter = Counter()
        vocab_set: Dict[str, int] = {}
        idx = 0

        for tokens in corpus:
            seen: Set[str] = set()
            for token in tokens:
                if token not in vocab_set:
                    vocab_set[token] = idx
                    idx += 1
                if token not in seen:
                    df[token] += 1
                    seen.add(token)

        self._vocab = vocab_set
        self._idf = {
            vocab_set[term]: math.log((n_docs + 1) / (count + 1)) + 1.0
            for term, count in df.items()
        }

        self._doc_vectors = []
        self._doc_norms = []

        for tokens in corpus:
            tf = Counter(tokens)
            vec: Dict[int, float] = {}
            for term, count in tf.items():
                tidx = vocab_set[term]
                tf_weight = 1.0 + math.log(count) if count > 0 else 0.0
                vec[tidx] = tf_weight * self._idf.get(tidx, 0.0)
            self._doc_vectors.append(vec)
            norm = math.sqrt(sum(v * v for v in vec.values()))
            self._doc_norms.append(norm)

    def query(self, tokens: List[str], limit: int = 10) -> List[Tuple[int, float]]:
        if not self._doc_vectors or not tokens:
            return []

        tf = Counter(tokens)
        q_vec: Dict[int, float] = {}
        for term, count in tf.items():
            tidx = self._vocab.get(term)
            if tidx is not None:
                tf_weight = 1.0 + math.log(count) if count > 0 else 0.0
                q_vec[tidx] = tf_weight * self._idf.get(tidx, 0.0)

        if not q_vec:
            return []

        q_norm = math.sqrt(sum(v * v for v in q_vec.values()))
        if q_norm == 0.0:
            return []

        results: List[Tuple[int, float]] = []
        for doc_idx, doc_vec in enumerate(self._doc_vectors):
            d_norm = self._doc_norms[doc_idx]
            if d_norm == 0.0:
                continue
            dot = sum(q_vec.get(k, 0) * doc_vec.get(k, 0) for k in q_vec)
            sim = dot / (q_norm * d_norm)
            if sim > 0.0:
                results.append((doc_idx, sim))

        results.sort(key=lambda x: x[1], reverse=True)
        return results[:limit]


# =============================================================================
# SEARCH INDEX
# =============================================================================

class SearchIndex:
    """
    Hybrid search index over a GraphStore.

    Uses available backends in order of preference:
      - BM25 (rank_bm25) > TF-IDF (stdlib fallback) for keyword search
      - Sentence-transformers > skip for semantic search
      - Reciprocal Rank Fusion to combine all rankings
    """

    def __init__(self, store: GraphStore, enable_embeddings: bool = True) -> None:
        self.store = store
        self._enable_embeddings = enable_embeddings and _HAS_EMBEDDINGS

        # Index state
        self._node_ids: List[str] = []
        self._node_token_cache: Dict[str, List[str]] = {}
        self._corpus: List[List[str]] = []
        self._built = False

        # BM25 engine (or TF-IDF fallback)
        self._bm25: Optional[BM25Okapi] = None
        self._tfidf: Optional[_TfidfEngine] = None

        # Embedding engine
        self._embedder: Optional[SentenceTransformer] = None
        self._embeddings: Optional[object] = None  # numpy array
        self._node_texts: List[str] = []

    @property
    def search_backend(self) -> str:
        """Return name of active keyword search backend."""
        if self._bm25 is not None:
            return "bm25"
        if self._tfidf is not None:
            return "tfidf"
        return "none"

    @property
    def has_embeddings(self) -> bool:
        """Whether semantic embedding search is active."""
        return self._embeddings is not None

    # --------------------------------------------------------------------- #
    # Index lifecycle
    # --------------------------------------------------------------------- #

    def build_index(self) -> None:
        """Build (or rebuild) the search index from all nodes in the store."""
        nodes = self.store.get_all_nodes()
        self._node_ids = [n.id for n in nodes]
        self._corpus = []
        self._node_token_cache.clear()
        self._node_texts = []

        for node in nodes:
            tokens = _node_tokens(node)
            self._corpus.append(tokens)
            self._node_token_cache[node.id] = tokens
            self._node_texts.append(_node_text(node))

        # Build BM25 index (preferred) or TF-IDF fallback
        if _HAS_BM25 and self._corpus:
            self._bm25 = BM25Okapi(self._corpus)
            self._tfidf = None
        elif self._corpus:
            engine = _TfidfEngine()
            engine.fit(self._corpus)
            self._tfidf = engine
            self._bm25 = None

        # Build embedding index (if enabled and available)
        if self._enable_embeddings and _HAS_EMBEDDINGS and self._node_texts:
            self._build_embedding_index()

        self._built = True

    def _build_embedding_index(self) -> None:
        """Build sentence-transformer embedding index."""
        try:
            if self._embedder is None:
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore")
                    self._embedder = SentenceTransformer(EMBEDDING_MODEL)

            # Encode all node texts in batches
            self._embeddings = self._embedder.encode(
                self._node_texts,
                batch_size=EMBEDDING_BATCH_SIZE,
                show_progress_bar=False,
                normalize_embeddings=True,  # L2-normalize for cosine sim via dot product
            )
        except Exception as e:
            print(f"[WARN] Embedding index build failed: {e}")
            self._embeddings = None

    def _ensure_index(self) -> None:
        """Build the index lazily if it hasn't been built yet."""
        if not self._built:
            self.build_index()

    # --------------------------------------------------------------------- #
    # Public search API
    # --------------------------------------------------------------------- #

    def search(
        self,
        query: str,
        limit: int = 10,
        type_filter: Optional[NodeType] = None,
        tier_filter: Optional[Tier] = None,
    ) -> List[SearchResult]:
        """
        Hybrid search with Reciprocal Rank Fusion.

        Combines results from all available search strategies using RRF
        to produce a unified ranking without needing score normalization.
        """
        self._ensure_index()

        if not query or not query.strip():
            return []

        # Collect ranked lists from each strategy
        # Each list: [(node_id, match_type), ...]
        ranked_lists: List[List[Tuple[str, str]]] = []

        # 1. Exact substring match (always available)
        exact_ids = self._exact_search_ids(query)
        if exact_ids:
            ranked_lists.append([(nid, "exact") for nid in exact_ids])

        # 2. BM25 or TF-IDF keyword search
        keyword_match_type = "bm25" if self._bm25 else "keyword"
        keyword_ids = self._keyword_search_ids(query, limit=limit * 5)
        if keyword_ids:
            ranked_lists.append([(nid, keyword_match_type) for nid in keyword_ids])

        # 3. Semantic embedding search
        if self._embeddings is not None:
            semantic_ids = self._semantic_search_ids(query, limit=limit * 5)
            if semantic_ids:
                ranked_lists.append([(nid, "semantic") for nid in semantic_ids])

        # Apply RRF to combine rankings
        rrf_scores: Dict[str, float] = {}
        rrf_match_types: Dict[str, str] = {}

        for ranked_list in ranked_lists:
            for rank, (nid, match_type) in enumerate(ranked_list):
                rrf_scores[nid] = rrf_scores.get(nid, 0.0) + 1.0 / (RRF_K + rank + 1)
                # Track which strategy contributed most
                if nid not in rrf_match_types:
                    rrf_match_types[nid] = match_type

        # Boost exact matches significantly
        for nid in (exact_ids[:3] if exact_ids else []):
            if nid in rrf_scores:
                rrf_scores[nid] *= 2.0

        # Normalize scores to [0, 1]
        if rrf_scores:
            max_score = max(rrf_scores.values())
            if max_score > 0:
                rrf_scores = {nid: s / max_score for nid, s in rrf_scores.items()}

        # Build results
        results: List[SearchResult] = []
        for nid, score in sorted(rrf_scores.items(), key=lambda x: -x[1]):
            node = self.store.get_node(nid)
            if node is None:
                continue

            # Apply filters
            if type_filter is not None and node.type != type_filter:
                continue
            if tier_filter is not None and node.tier != tier_filter:
                continue

            match_type = rrf_match_types.get(nid, "rrf")
            matched_field = _best_matched_field(node, set(tokenize(query)))

            results.append(SearchResult(
                node=node,
                score=round(score, 4),
                match_type=match_type,
                matched_field=matched_field,
            ))

            if len(results) >= limit:
                break

        return results

    # --------------------------------------------------------------------- #
    # Individual search strategies (return ranked node ID lists)
    # --------------------------------------------------------------------- #

    def _exact_search_ids(self, query: str) -> List[str]:
        """Exact substring match on node name — returns ranked node IDs."""
        q_lower = query.lower().strip()
        name_hits = []
        desc_hits = []

        for node in self.store.get_all_nodes():
            if q_lower in node.name.lower():
                name_hits.append(node.id)
            elif node.description and q_lower in node.description.lower():
                desc_hits.append(node.id)

        # Name hits first, then description hits
        return name_hits + desc_hits

    def _keyword_search_ids(self, query: str, limit: int = 50) -> List[str]:
        """BM25 or TF-IDF keyword search — returns ranked node IDs."""
        query_tokens = tokenize(query)
        if not query_tokens:
            return []

        if self._bm25 is not None:
            # BM25 search
            scores = self._bm25.get_scores(query_tokens)
            # Get top indices sorted by score descending
            indexed_scores = [(i, float(scores[i])) for i in range(len(scores)) if scores[i] > 0]
            indexed_scores.sort(key=lambda x: -x[1])
            return [self._node_ids[i] for i, _ in indexed_scores[:limit]]

        elif self._tfidf is not None:
            # TF-IDF fallback
            results = self._tfidf.query(query_tokens, limit=limit)
            return [self._node_ids[doc_idx] for doc_idx, _ in results]

        return []

    def _semantic_search_ids(self, query: str, limit: int = 50) -> List[str]:
        """Sentence-transformer embedding search — returns ranked node IDs."""
        if self._embedder is None or self._embeddings is None:
            return []

        try:
            query_embedding = self._embedder.encode(
                [query],
                normalize_embeddings=True,
                show_progress_bar=False,
            )
            # Cosine similarity via dot product (embeddings are L2-normalized)
            similarities = np.dot(self._embeddings, query_embedding.T).flatten()

            # Get top indices
            top_indices = np.argsort(similarities)[::-1][:limit]
            return [
                self._node_ids[int(i)]
                for i in top_indices
                if similarities[i] > 0.1  # minimum similarity threshold
            ]
        except Exception:
            return []

    # --------------------------------------------------------------------- #
    # Legacy API (backward compatible)
    # --------------------------------------------------------------------- #

    def _exact_search(self, query: str) -> List[SearchResult]:
        """Legacy: exact search returning SearchResult objects."""
        results = []
        for nid in self._exact_search_ids(query):
            node = self.store.get_node(nid)
            if node:
                results.append(SearchResult(
                    node=node, score=1.0,
                    match_type="exact", matched_field="name",
                ))
        return results

    def _keyword_search(self, query: str) -> List[SearchResult]:
        """Legacy: keyword search returning SearchResult objects."""
        results = []
        for nid in self._keyword_search_ids(query):
            node = self.store.get_node(nid)
            if node:
                results.append(SearchResult(
                    node=node, score=0.85,
                    match_type="keyword", matched_field="name",
                ))
        return results


# =============================================================================
# HELPERS
# =============================================================================

def _best_matched_field(node: GraphNode, matched_tokens: Set[str]) -> str:
    """Determine which node field contributed the most matched tokens."""
    name_tokens = set(tokenize(node.name))
    if matched_tokens & name_tokens:
        return "name"

    if node.description:
        desc_tokens = set(tokenize(node.description))
        if matched_tokens & desc_tokens:
            return "description"

    if node.tags:
        tag_tokens = set(tokenize(" ".join(node.tags)))
        if matched_tokens & tag_tokens:
            return "tags"

    return "metadata"


# =============================================================================
# CLI / DEMO
# =============================================================================

if __name__ == "__main__":
    from ontology import Provenance

    print("=" * 70)
    print("  Hybrid Search Demo (BM25 + Semantic + RRF)")
    print("=" * 70)

    print(f"\nBackends available:")
    print(f"  BM25: {_HAS_BM25}")
    print(f"  Embeddings: {_HAS_EMBEDDINGS}")

    # --- Build a small demo graph ------------------------------------------
    store = GraphStore()

    demo_nodes = [
        GraphNode(
            id="service:order-api",
            type=NodeType.SERVICE,
            name="Order API",
            description="REST API for order management and checkout flow",
            tags=["critical-path"],
            metadata={"framework": "express"},
        ),
        GraphNode(
            id="function:process-payment",
            type=NodeType.FUNCTION,
            name="processPayment",
            description="Handles payment processing via Stripe integration",
            tags=["critical-path", "sensitive-data"],
            metadata={"framework": "express"},
        ),
        GraphNode(
            id="endpoint:post-orders",
            type=NodeType.ENDPOINT,
            name="POST /orders",
            description="Creates a new order, validates inventory, and initiates payment",
            metadata={"http_method": "POST", "http_path": "/api/v1/orders"},
        ),
        GraphNode(
            id="collection:orders",
            type=NodeType.COLLECTION,
            name="orders",
            description="Orders table storing all customer orders with status tracking",
            metadata={"engine": "postgres", "table_name": "orders"},
        ),
        GraphNode(
            id="collection:payments",
            type=NodeType.COLLECTION,
            name="payments",
            description="Payment records linked to orders with Stripe transaction IDs",
            metadata={"engine": "postgres", "table_name": "payments"},
        ),
        GraphNode(
            id="function:validate-inventory",
            type=NodeType.FUNCTION,
            name="validateInventory",
            description="Checks product availability and reserves stock for an order",
            tags=["utility"],
        ),
        GraphNode(
            id="function:send-order-email",
            type=NodeType.FUNCTION,
            name="sendOrderConfirmationEmail",
            description="Sends order confirmation email to customer after successful checkout",
            tags=["notification"],
        ),
        GraphNode(
            id="data-model:user",
            type=NodeType.DATA_MODEL,
            name="User",
            description="User entity with authentication credentials and profile information",
            tags=["auth-required"],
            metadata={"orm": "prisma"},
        ),
        GraphNode(
            id="middleware:auth",
            type=NodeType.MIDDLEWARE,
            name="authMiddleware",
            description="JWT authentication middleware that validates bearer tokens",
            tags=["auth-required", "rate-limited"],
        ),
        GraphNode(
            id="event:order-created",
            type=NodeType.EVENT,
            name="ORDER_CREATED",
            description="Domain event emitted when a new order is successfully placed",
        ),
        GraphNode(
            id="external-api:stripe",
            type=NodeType.EXTERNAL_API,
            name="Stripe API",
            description="Payment processing gateway for credit card and subscription billing",
        ),
        GraphNode(
            id="queue:email-queue",
            type=NodeType.QUEUE,
            name="email-queue",
            description="Message queue for asynchronous email delivery",
        ),
    ]

    for node in demo_nodes:
        store.add_node(node)

    print(f"\nLoaded {len(demo_nodes)} demo nodes into GraphStore.")

    # --- Build search index ------------------------------------------------
    index = SearchIndex(store, enable_embeddings=True)
    index.build_index()
    print(f"Search backend: {index.search_backend}")
    print(f"Embeddings active: {index.has_embeddings}")
    print()

    # --- Run demo queries --------------------------------------------------
    queries = [
        "payment",
        "find payment code",
        "order checkout",
        "email notification",
        "authentication JWT",
        "stripe",
        "inventory",
        "processPayment",
        "how does checkout work",  # semantic query
        "what handles money",     # semantic query
    ]

    for q in queries:
        print(f'--- Query: "{q}" ---')
        results = index.search(q, limit=5)
        if not results:
            print("  (no results)")
        for r in results:
            print(f"  {r.score:.4f}  [{r.match_type:<8}]  [{r.matched_field:<12}]  "
                  f"{r.node.type.value:<14}  {r.node.name}")
        print()

    print("Done.")
