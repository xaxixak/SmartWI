# Workspace Intelligence Layer - Research Synthesis

## Date: 2026-02-09
## Agents: 3 parallel research agents (Code Intelligence Systems, Ontology Design, Pipeline Strategy)

---

## Executive Summary

After deep research into Sourcegraph SCIP, Aider, Cursor, Continue.dev, CodeQL, Tree-sitter Stack Graphs, Blar, CodePrism, LocAgent, and GraphGen4Code, the findings converge on one key insight:

**No open-source project combines all of:**
1. Behavioral edge types (db_read, api_call, cache_write)
2. Cross-service architectural graph
3. Natural-language "stories" for LLM consumption
4. Self-healing auto-updating metadata
5. MCP-exposed AI-agent-consumable format

This is the market gap. Our system fills it.

---

## 1. Competitive Landscape

### Tier 1: Embedding/Similarity (market is saturated)
- **Cursor**: Merkle tree change detection, AST chunking, Turbopuffer vector DB
- **Continue.dev**: Tree-sitter AST chunking, LanceDB local vectors, all-MiniLM-L6-v2
- **Limitation**: No relationships, no structure, no architecture awareness

### Tier 2: Structural Graph (growing)
- **SCIP/Sourcegraph**: Human-readable symbol IDs, cross-repo references, 10+ language indexers
- **Aider**: Tree-sitter + NetworkX + PageRank ranking, token budget optimization
- **Blar**: SCIP + Tree-sitter + Neo4j, AI debugging agent
- **CodePrism**: Rust-based, 1000 files/second, MCP server with 20 tools
- **LocAgent (Yale)**: Graph-guided LLM navigation, 92.7% accuracy
- **Limitation**: No behavioral semantics (can't distinguish db_read from api_call)

### Tier 3: Semantic/Behavioral (our target)
- **Thorbit (inspiration)**: 12 node types, 9 behavioral edge types, self-healing
- **CodeQL**: Deep data flow + taint tracking, but no incremental updates, heavy
- **Our system**: Should combine Tier 2 structural analysis with Tier 3 behavioral semantics

---

## 2. Key Techniques to Adopt

| Technique | Source | Benefit |
|-----------|--------|---------|
| Human-readable symbol IDs | SCIP | Cross-repo globally unique identifiers |
| PageRank importance ranking | Aider | Not all code is equally important |
| Merkle tree change detection | Cursor | O(log n) detection of what changed |
| Content-hash caching | Cursor | Skip re-analysis of unchanged code |
| AST-guided chunking | Cursor/Continue | Semantic splits at function/class boundaries |
| Independent file analysis | Stack Graphs | Analyze files independently, merge at query time |
| SCIP + Tree-sitter hybrid | Blar | SCIP for references, Tree-sitter classifies type |
| Graph traversal tools for LLM | LocAgent | SearchEntity, TraverseGraph, RetrieveEntity |
| MCP server exposure | CodePrism/Augment | Any AI agent can consume the intelligence |
| Prompt caching | Anthropic | 10x cheaper on repeated system prompts |

---

## 3. Optimal Ontology (v2)

### Node Types: 20 (was 14, +6)

**Tier 1 - Macro (Architecture level): 5 types**
- WORKSPACE, PROJECT, SERVICE, RESOURCE, EXTERNAL_API

**Tier 2 - Meso (Component level): 6 types**
- MODULE, FILE, ROUTER (new), COLLECTION (new), INFRA_CONFIG, QUEUE (new)

**Tier 3 - Micro (Code element level): 9 types**
- ENDPOINT, FUNCTION, ASYNC_HANDLER, DATA_MODEL, EVENT
- MIDDLEWARE (new), TYPE_DEF (new), CACHE_KEY, ENV_VAR (new)

### Tags (NOT types): 4 concepts kept as tags
- TEST → Function with tags: ["test"]
- SCRIPT → Function with tags: ["script", "entry-point"]
- UTILITY → Function with tags: ["utility"]
- MIGRATION → Function with tags: ["migration"]

Decision rule: A concept becomes a TYPE only if it has unique edge semantics.

### Edge Types: 27 (was 15, +12)

**Structural (5):** CONTAINS, DEFINES, IMPORTS, IMPLEMENTS*, INHERITS*
**Data Flow (3):** READS_DB, WRITES_DB, MIGRATES*
**Communication (5):** CALLS_API, CALLS_SERVICE, CALLS*, WEBHOOK_SEND, WEBHOOK_RECEIVE
**Event/Async (5):** EMITS_EVENT, CONSUMES_EVENT, ENQUEUES*, DEQUEUES*, SCHEDULES*
**Caching (2):** CACHE_READ, CACHE_WRITE
**Routing (4):** ROUTES_TO*, INTERCEPTS*, VALIDATES*, AUTHENTICATES*
**Config/Deploy (3):** DEPENDS_ON, DEPLOYED_BY, CONFIGURES*
**Quality (1):** TESTS*

(* = new)

### Model Improvements

**GraphNode +7 fields:**
- provenance (scanner/llm/human/import/runtime)
- source_hash (SHA-256 for change detection)
- language (multi-language support)
- tags (flexible classification)
- parent_id (fast containment lookup)
- version (monotonic change counter)
- tier (computed from type)

**GraphEdge +5 fields:**
- provenance, is_stale, weight, conditional, location

**ContextPack +8 fields:**
- relevant_edges, related_files, code_snippets, invariants
- patterns, stale_warnings, depth, total_nodes_in_scope

---

## 4. Pipeline Architecture

### The 80/20 Rule: 80% of nodes cost $0

| Pass | What | Cost | Time | Confidence |
|------|------|------|------|------------|
| 0: Scanner | Projects, Infra | $0 | <1s | 0.95-1.0 |
| 1: Tree-sitter | Files, Functions, Imports | $0 | 2-5s | 0.85-0.95 |
| 2: Pattern Match | Endpoints, Models, Events | $0 | 1-3s | 0.70-0.90 |
| 3: LLM Semantic | Operational edges, stories | ~$1.85 | 5-30min | 0.60-0.85 |
| 4: Validation | Cross-reference check | ~$0.50 | 1-5min | boost/penalty |

**Total for 500 files: ~$2.35 (or ~$0.68 with caching + batch API)**

### LLM Model Routing

| Task | Model | Why |
|------|-------|-----|
| File classification | Haiku 3.5 | Simple, cheap |
| Service classification | Haiku 3.5 | Pattern recognition |
| Data flow edges | Sonnet 4.5 | Multi-file reasoning |
| Cross-service comms | Sonnet 4.5 | Complex tracing |
| Conflict resolution | Opus 4.6 | Hard cases only |

### Output Format
Anthropic tool_use with schema enforcement. No JSON parsing needed.

---

## 5. Self-Healing Design

### Change Detection Flow
```
Git commit/hook → git diff --name-status
→ identify changed files
→ find all nodes sourced from those files
→ mark nodes STALE
→ cascade staleness 2 hops:
  - Event stale → consumers stale
  - DataModel stale → readers/writers stale
  - Endpoint stale → callers stale
→ re-run Passes 1-3 on stale files only
→ re-run Pass 4 on affected subgraph
```

### Incremental Cost
| Change | Files Re-analyzed | Cost | Time |
|--------|------------------|------|------|
| 1 file | 1-5 | ~$0.01 | <10s |
| 10 files | 15-30 | ~$0.10 | <30s |
| 50 files | 80-120 | ~$0.40 | 1-2min |

### Techniques Combined
1. Merkle tree (Cursor) for file-level change detection
2. Content-hash (Cursor/Aider) for chunk-level caching
3. Independent analysis (Stack Graphs) for parallelism
4. source_hash on GraphNode for fingerprint validation

---

## 6. Module Structure

```
workspace-intelligence/
    ontology.py              # [EXISTS] → upgrade to v2 (20 nodes, 27 edges)
    scanner.py               # [EXISTS] → enhance docker-compose/CI parsing
    graph_store.py           # [EXISTS] → add batch ops, path finding, subgraph

    pipeline/
        orchestrator.py      # Main coordinator with JSON checkpoint resume
        pass1_treesitter.py  # Tree-sitter AST extraction (FREE)
        pass2_patterns.py    # 15+ framework regex patterns (FREE)
        pass3_llm.py         # Async LLM analysis (Haiku/Sonnet routing)
        pass4_validation.py  # Cross-reference + hallucination detection
        chunker.py           # AST-guided file splitting for large files

    llm/
        client.py            # Anthropic API + prompt caching + retries + cost tracking
        prompts.py           # System prompts + tool_use schema definitions
        model_router.py      # Haiku/Sonnet/Opus selection logic
        batch.py             # Batch API for initial full-index (50% cheaper)

    incremental/
        change_detector.py   # Git diff parsing
        staleness.py         # 2-hop cascade propagation
        selective_reindex.py # Targeted re-analysis of stale subgraphs

    merge/
        result_merger.py     # Parallel result reconciliation
        conflict_resolver.py # Confidence + specificity based resolution

    api/
        mcp_server.py        # MCP tools for AI agent consumption
        fastapi_app.py       # HTTP API for visualization/dashboard

    config.py                # API keys, model choices, thresholds
    state.py                 # Pipeline state for crash recovery
    cli.py                   # CLI entry point
```

---

## 7. Build Priority

### Phase 1: Foundation (make the graph populate)
1. Upgrade ontology.py to v2 (20 nodes, 27 edges, improved models)
2. Build Pass 1: Tree-sitter extraction (60-70% of nodes, FREE)
3. Build Pass 2: Pattern matching (reclassify + discover, FREE)
4. Bridge scanner → graph (convert DiscoveredProject → GraphNode)

### Phase 2: Intelligence (add the "story")
5. Build Pass 3: LLM semantic analysis (async, batched)
6. Build LLM client with prompt caching + cost tracking
7. Build orchestrator with JSON checkpoint resume

### Phase 3: Self-Healing
8. Build change detector (git diff based)
9. Build staleness propagation (2-hop cascade)
10. Build selective re-indexer

### Phase 4: Consumption
11. Build MCP server (SearchEntity, TraverseGraph, GetContext, ImpactAnalysis)
12. Enhance ContextPack with edges, snippets, invariants, patterns
13. Token budget system for context window management

### Phase 5: Quality & Scale
14. Build Pass 4: Validation
15. Add LanceDB for vector search
16. Build visualization (graph viewer like original screenshot)

---

## 8. What NOT to Build

- LangGraph (direct API calls are simpler for this use case)
- Runtime tracing (this is static analysis + LLM, not APM)
- Test file LLM analysis (skip in Pass 3, waste of tokens)
- Full Neo4j for MVP (NetworkX is sufficient under 10K nodes)
- Custom embedding model (use off-the-shelf MiniLM or Voyage)

---

## Sources

### Code Intelligence Systems
- [SCIP Protocol](https://github.com/sourcegraph/scip)
- [Aider Repo Map](https://aider.chat/2023/10/22/repomap.html)
- [Continue.dev Codebase Retrieval](https://docs.continue.dev/features/codebase-embeddings)
- [Cursor Indexing Architecture](https://read.engineerscodex.com/p/how-cursor-indexes-codebases-fast)
- [CodeQL Data Flow Analysis](https://codeql.github.com/docs/writing-codeql-queries/about-data-flow-analysis/)
- [GitHub Stack Graphs](https://github.blog/open-source/introducing-stack-graphs/)
- [Blar Code Graph](https://blar.io/blog/how-we-built-a-tool-to-turn-any-code-base-into-a-graph-of-its-relationships)
- [CodePrism Architecture](https://rustic-ai.github.io/codeprism/blog/graph-based-code-analysis-engine/)
- [LocAgent (Yale, ACL 2025)](https://arxiv.org/abs/2503.09089)
- [Augment Code MCP](https://www.augmentcode.com/)

### Ontology & Knowledge Graphs
- [Joern Code Property Graph](https://cpg.joern.io/)
- [Software Archaeology Ontology](https://bennycheung.github.io/ontology-the-queryable-brain-of-software-archaeology)
- [GraphGen4Code (IBM WALA)](https://wala.github.io/graph4code/)
- [Neo4j Codebase Knowledge Graph](https://neo4j.com/blog/developer/codebase-knowledge-graph/)

### Pipeline & Cost
- [Anthropic Prompt Caching](https://docs.anthropic.com/en/docs/build-with-claude/prompt-caching)
- [Anthropic Batch API](https://docs.anthropic.com/en/docs/build-with-claude/batch-processing)
- [code-graph-rag](https://github.com/vitali87/code-graph-rag)
