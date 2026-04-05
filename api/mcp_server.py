"""
Workspace Intelligence - MCP Server (Story 4.3 + Phase 5 Enhancement)
=====================================================================

Stdio-based MCP server exposing graph intelligence to AI agents.

Protocol: JSON-RPC 2.0 over stdin/stdout (MCP spec 2024-11-05)
Transport: stdio (newline-delimited JSON)

Tools (8):
  1. search_entity    - Search nodes by name/type/tag (BM25+RRF hybrid)
  2. traverse_graph   - Walk upstream/downstream from a node
  3. get_context      - Generate a ContextPack for AI consumption
  4. impact_analysis  - Show blast radius of a node
  5. get_stats        - Graph statistics
  6. list_flows       - List detected execution flows
  7. list_clusters    - List community clusters (Leiden)
  8. detect_changes   - Git diff → affected graph nodes

Resources (5):
  - wi://repos                   - List indexed workspaces
  - wi://repo/{name}/stats       - Graph statistics
  - wi://repo/{name}/clusters    - Community clusters
  - wi://repo/{name}/flows       - Execution flows
  - wi://repo/{name}/schema      - Ontology schema

Prompts (3):
  - detect_impact   - Pre-commit change analysis
  - explore_area    - Navigate unfamiliar code
  - generate_map    - Architecture documentation

Usage:
  python -m api.mcp_server --graph workspace_graph.json
"""

import json
import subprocess
import sys
import argparse
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.request import Request, urlopen
from urllib.error import URLError

# Add project root to path so we can import graph_store / ontology
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from graph_store import GraphStore
from ontology import GraphNode, GraphEdge, NodeType, EdgeType, Tier, ContextPack


# =============================================================================
# TOOL DEFINITIONS (MCP schema)
# =============================================================================

TOOLS: List[Dict[str, Any]] = [
    {
        "name": "search_entity",
        "description": (
            "Search for entities in the code knowledge graph by name. "
            "Returns matching nodes with id, type, name, description, tier, tags, and confidence. "
            "Useful for finding functions, endpoints, services, data models, etc."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Search query (case-insensitive substring match on node name)",
                },
                "type_filter": {
                    "type": "string",
                    "description": (
                        "Filter by NodeType. Valid values: Workspace, Project, Service, "
                        "Resource, ExternalAPI, Module, File, Router, Collection, InfraConfig, "
                        "Queue, Endpoint, Function, AsyncHandler, DataModel, Event, Middleware, "
                        "TypeDef, CacheKey, EnvVar"
                    ),
                },
                "limit": {
                    "type": "integer",
                    "description": "Maximum number of results to return (default: 10)",
                    "default": 10,
                },
            },
            "required": ["query"],
        },
    },
    {
        "name": "traverse_graph",
        "description": (
            "Traverse the knowledge graph from a starting node. "
            "Follow edges upstream (callers/dependents), downstream (callees/dependencies), "
            "or both directions. Returns the traversed nodes and connecting edges."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "node_id": {
                    "type": "string",
                    "description": "ID of the starting node (e.g., 'endpoint:user-api:POST:/users')",
                },
                "direction": {
                    "type": "string",
                    "enum": ["upstream", "downstream", "both"],
                    "description": (
                        "Traversal direction. 'upstream' = who calls/depends on this node, "
                        "'downstream' = what this node calls/depends on, 'both' = both directions"
                    ),
                },
                "depth": {
                    "type": "integer",
                    "description": "Maximum traversal depth in hops (default: 3)",
                    "default": 3,
                },
                "edge_type_filter": {
                    "type": "string",
                    "description": (
                        "Filter edges by type (e.g., 'CALLS', 'READS_DB', 'CONTAINS'). "
                        "If omitted, all edge types are included."
                    ),
                },
            },
            "required": ["node_id", "direction"],
        },
    },
    {
        "name": "get_context",
        "description": (
            "Generate a ContextPack for AI consumption. Provides architectural context "
            "around a scope (node ID or name) for a given task focus. Includes relevant nodes, "
            "edges, upstream/downstream dependencies, stale warnings, and risk assessment. "
            "Use detail_level to control verbosity and token budget."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "scope": {
                    "type": "string",
                    "description": (
                        "Node ID or name to center the context on "
                        "(e.g., 'service:order-api' or 'OrderService')"
                    ),
                },
                "focus": {
                    "type": "string",
                    "description": "Task description (e.g., 'Refactoring database schema')",
                },
                "depth": {
                    "type": "integer",
                    "description": "Traversal depth in hops (default: 3)",
                    "default": 3,
                },
                "detail_level": {
                    "type": "string",
                    "enum": ["L1", "L2", "L3"],
                    "description": (
                        "Verbosity level. L1: names only (~200 tokens), "
                        "L2: names + descriptions (~1K tokens), "
                        "L3: full detail + code snippets (~4K tokens). Default: L2"
                    ),
                    "default": "L2",
                },
            },
            "required": ["scope", "focus"],
        },
    },
    {
        "name": "impact_analysis",
        "description": (
            "Analyze the blast radius of a node. Shows what depends on this node "
            "(upstream callers/consumers) and what it depends on (downstream). "
            "Returns upstream nodes, downstream nodes, risk assessment, and blast radius count. "
            "Essential before making changes to understand what could break."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "node_id": {
                    "type": "string",
                    "description": "ID of the node to analyze impact for",
                },
                "depth": {
                    "type": "integer",
                    "description": "How many hops to traverse for impact (default: 3)",
                    "default": 3,
                },
            },
            "required": ["node_id"],
        },
    },
    {
        "name": "get_stats",
        "description": (
            "Get graph statistics: total nodes, total edges, breakdown by type and tier, "
            "stale node/edge counts. Useful for understanding the overall graph health and size."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "list_flows",
        "description": (
            "List detected execution flows in the codebase. Each flow traces a path "
            "from an entry point (route handler, main function, event handler) through "
            "the call graph to terminal nodes. Shows flow name, type, step count, and "
            "entry/terminal node IDs."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "flow_type": {
                    "type": "string",
                    "enum": ["route", "event", "startup", "general"],
                    "description": "Filter by flow type (optional)",
                },
                "limit": {
                    "type": "integer",
                    "description": "Maximum number of flows to return (default: 20)",
                    "default": 20,
                },
            },
        },
    },
    {
        "name": "list_clusters",
        "description": (
            "List community clusters detected via Leiden algorithm. Each cluster is a "
            "group of closely related code entities (functions, classes, endpoints) that "
            "work together. Shows cluster label, member count, cohesion score, and members."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "min_size": {
                    "type": "integer",
                    "description": "Minimum cluster size to include (default: 2)",
                    "default": 2,
                },
                "include_members": {
                    "type": "boolean",
                    "description": "Include member node IDs in each cluster (default: true)",
                    "default": True,
                },
                "limit": {
                    "type": "integer",
                    "description": "Maximum number of clusters to return (default: 20)",
                    "default": 20,
                },
            },
        },
    },
    {
        "name": "detect_changes",
        "description": (
            "Detect which graph nodes are affected by recent git changes. "
            "Runs git diff to find changed files, then maps those files to nodes in the graph. "
            "Useful for pre-commit impact analysis and understanding the blast radius of a change."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "repo_path": {
                    "type": "string",
                    "description": "Path to the git repository (default: current directory)",
                },
                "diff_target": {
                    "type": "string",
                    "description": (
                        "What to diff against. 'staged' = staged changes only, "
                        "'working' = all working directory changes, 'HEAD~1' = last commit, "
                        "or any git ref. Default: 'working'"
                    ),
                    "default": "working",
                },
            },
        },
    },
]


# =============================================================================
# TOOL IMPLEMENTATIONS
# =============================================================================

def _serialize_node(node: GraphNode, detail: str = "L2") -> Dict[str, Any]:
    """Serialize a GraphNode at the requested detail level."""
    if detail == "L1":
        return {
            "id": node.id,
            "type": node.type.value,
            "name": node.name,
            "tier": node.tier.value,
        }
    elif detail == "L2":
        return {
            "id": node.id,
            "type": node.type.value,
            "name": node.name,
            "description": node.description,
            "tier": node.tier.value,
            "tags": node.tags,
            "confidence": node.confidence,
            "is_stale": node.is_stale,
        }
    else:  # L3
        return node.model_dump(mode="json")


def _serialize_edge(edge: GraphEdge, detail: str = "L2") -> Dict[str, Any]:
    """Serialize a GraphEdge at the requested detail level."""
    if detail == "L1":
        return {
            "source_id": edge.source_id,
            "target_id": edge.target_id,
            "type": edge.type.value,
        }
    elif detail == "L2":
        return {
            "source_id": edge.source_id,
            "target_id": edge.target_id,
            "type": edge.type.value,
            "description": edge.description,
            "confidence": edge.confidence,
            "conditional": edge.conditional,
        }
    else:  # L3
        return edge.model_dump(mode="json")


def tool_search_entity(store: GraphStore, arguments: Dict[str, Any]) -> Dict[str, Any]:
    """Search nodes by name, optionally filtered by type."""
    query = arguments.get("query", "").lower()
    type_filter = arguments.get("type_filter")
    limit = arguments.get("limit", 10)

    # Get candidate nodes
    if type_filter:
        try:
            node_type = NodeType(type_filter)
            candidates = store.get_nodes_by_type(node_type)
        except ValueError:
            return {
                "error": f"Unknown NodeType: '{type_filter}'. Valid types: {[t.value for t in NodeType]}",
                "results": [],
            }
    else:
        candidates = list(store._nodes.values())

    # Filter by substring match on name (case-insensitive)
    matches = [n for n in candidates if query in n.name.lower()]

    # Sort by confidence descending, then name
    matches.sort(key=lambda n: (-n.confidence, n.name))

    # Apply limit
    matches = matches[:limit]

    return {
        "total_matches": len(matches),
        "results": [_serialize_node(n, "L2") for n in matches],
    }


def tool_traverse_graph(store: GraphStore, arguments: Dict[str, Any]) -> Dict[str, Any]:
    """Traverse the graph from a starting node."""
    node_id = arguments.get("node_id", "")
    direction = arguments.get("direction", "both")
    depth = arguments.get("depth", 3)
    edge_type_filter = arguments.get("edge_type_filter")

    # Validate starting node
    start_node = store.get_node(node_id)
    if not start_node:
        return {"error": f"Node not found: '{node_id}'", "nodes": [], "edges": []}

    # Collect traversed nodes
    upstream_nodes: List[GraphNode] = []
    downstream_nodes: List[GraphNode] = []

    if direction in ("upstream", "both"):
        upstream_nodes = store.get_upstream(node_id, depth)
    if direction in ("downstream", "both"):
        downstream_nodes = store.get_downstream(node_id, depth)

    # Collect all relevant node IDs
    all_ids = {node_id}
    all_ids.update(n.id for n in upstream_nodes)
    all_ids.update(n.id for n in downstream_nodes)

    # Gather edges between traversed nodes
    edges = [
        e for e in store._edges.values()
        if e.source_id in all_ids and e.target_id in all_ids
    ]

    # Apply edge type filter if specified
    if edge_type_filter:
        try:
            et = EdgeType(edge_type_filter)
            edges = [e for e in edges if e.type == et]
        except ValueError:
            return {
                "error": f"Unknown EdgeType: '{edge_type_filter}'. Valid types: {[t.value for t in EdgeType]}",
                "nodes": [],
                "edges": [],
            }

    # Combine all nodes (deduplicated)
    all_nodes_map: Dict[str, GraphNode] = {node_id: start_node}
    for n in upstream_nodes + downstream_nodes:
        all_nodes_map[n.id] = n

    return {
        "start_node": _serialize_node(start_node, "L2"),
        "direction": direction,
        "depth": depth,
        "total_nodes": len(all_nodes_map),
        "total_edges": len(edges),
        "nodes": [_serialize_node(n, "L2") for n in all_nodes_map.values()],
        "edges": [_serialize_edge(e, "L2") for e in edges],
    }


def tool_get_context(store: GraphStore, arguments: Dict[str, Any]) -> Dict[str, Any]:
    """Generate a ContextPack for AI consumption."""
    scope = arguments.get("scope", "")
    focus = arguments.get("focus", "")
    depth = arguments.get("depth", 3)
    detail_level = arguments.get("detail_level", "L2")

    # Validate detail level
    if detail_level not in ("L1", "L2", "L3"):
        detail_level = "L2"

    # Map detail level to token budget
    token_budgets = {"L1": 200, "L2": 1000, "L3": 4000}
    max_tokens = token_budgets.get(detail_level, 1000)

    # Get context pack from the store with token budget
    context = store.get_context(scope, focus, max_depth=depth, max_tokens=max_tokens)

    # Serialize at the requested detail level
    result: Dict[str, Any] = {
        "scope": context.scope,
        "focus": context.focus,
        "depth": context.depth,
        "total_nodes_in_scope": context.total_nodes_in_scope,
    }

    if detail_level == "L1":
        # Names only (~200 tokens)
        result["relevant_nodes"] = [
            {"id": n.id, "type": n.type.value, "name": n.name}
            for n in context.relevant_nodes
        ]
        result["upstream"] = [
            {"id": n.id, "type": n.type.value, "name": n.name}
            for n in context.upstream
        ]
        result["downstream"] = [
            {"id": n.id, "type": n.type.value, "name": n.name}
            for n in context.downstream
        ]
        result["edges"] = [
            {"source_id": e.source_id, "target_id": e.target_id, "type": e.type.value}
            for e in context.relevant_edges
        ]

    elif detail_level == "L2":
        # Names + descriptions (~1K tokens)
        result["relevant_nodes"] = [_serialize_node(n, "L2") for n in context.relevant_nodes]
        result["upstream"] = [_serialize_node(n, "L2") for n in context.upstream]
        result["downstream"] = [_serialize_node(n, "L2") for n in context.downstream]
        result["edges"] = [_serialize_edge(e, "L2") for e in context.relevant_edges]
        result["stale_warnings"] = context.stale_warnings
        result["risk_assessment"] = context.risk_assessment
        result["patterns"] = context.patterns
        result["invariants"] = context.invariants

    else:  # L3
        # Full detail + code snippets (~4K tokens)
        result["relevant_nodes"] = [_serialize_node(n, "L3") for n in context.relevant_nodes]
        result["upstream"] = [_serialize_node(n, "L3") for n in context.upstream]
        result["downstream"] = [_serialize_node(n, "L3") for n in context.downstream]
        result["edges"] = [_serialize_edge(e, "L3") for e in context.relevant_edges]
        result["stale_warnings"] = context.stale_warnings
        result["risk_assessment"] = context.risk_assessment
        result["patterns"] = context.patterns
        result["invariants"] = context.invariants
        result["related_files"] = [
            loc.model_dump(mode="json") for loc in context.related_files
        ]
        result["code_snippets"] = context.code_snippets

    return result


def tool_impact_analysis(store: GraphStore, arguments: Dict[str, Any]) -> Dict[str, Any]:
    """Analyze the blast radius of a node."""
    node_id = arguments.get("node_id", "")
    depth = arguments.get("depth", 3)

    # Validate node
    node = store.get_node(node_id)
    if not node:
        return {"error": f"Node not found: '{node_id}'"}

    # Get upstream (who depends on this) and downstream (what this depends on)
    upstream = store.get_upstream(node_id, depth)
    downstream = store.get_downstream(node_id, depth)

    # Classify upstream by tier for risk assessment
    upstream_by_tier: Dict[str, int] = {"macro": 0, "meso": 0, "micro": 0}
    for n in upstream:
        upstream_by_tier[n.tier.value] += 1

    # Build risk assessment
    blast_radius = len(upstream) + len(downstream)
    if len(upstream) > 10:
        risk_level = "CRITICAL"
        risk_summary = (
            f"CRITICAL: {len(upstream)} components depend on this node. "
            f"Changes here have a wide blast radius ({blast_radius} total nodes affected)."
        )
    elif len(upstream) > 5:
        risk_level = "HIGH"
        risk_summary = (
            f"HIGH RISK: {len(upstream)} upstream dependencies. "
            f"Blast radius: {blast_radius} nodes."
        )
    elif len(upstream) > 2:
        risk_level = "MEDIUM"
        risk_summary = (
            f"MEDIUM RISK: {len(upstream)} upstream dependencies. "
            f"Blast radius: {blast_radius} nodes."
        )
    else:
        risk_level = "LOW"
        risk_summary = (
            f"LOW RISK: {len(upstream)} upstream dependencies. "
            f"Blast radius: {blast_radius} nodes."
        )

    # Check for stale nodes in the impact zone
    stale_in_zone = [n for n in upstream + downstream if n.is_stale]
    stale_warning = None
    if stale_in_zone:
        stale_warning = (
            f"{len(stale_in_zone)} node(s) in the impact zone are stale and may have "
            f"outdated information: {[n.name for n in stale_in_zone[:5]]}"
        )

    # Get edges directly connected to this node
    edges_from = store.get_edges_from(node_id)
    edges_to = store.get_edges_to(node_id)

    return {
        "node": _serialize_node(node, "L2"),
        "risk_level": risk_level,
        "risk_summary": risk_summary,
        "blast_radius": blast_radius,
        "upstream_count": len(upstream),
        "downstream_count": len(downstream),
        "upstream_by_tier": upstream_by_tier,
        "upstream": [_serialize_node(n, "L2") for n in upstream],
        "downstream": [_serialize_node(n, "L2") for n in downstream],
        "direct_edges_in": [_serialize_edge(e, "L2") for e in edges_to],
        "direct_edges_out": [_serialize_edge(e, "L2") for e in edges_from],
        "stale_warning": stale_warning,
    }


def tool_get_stats(store: GraphStore, arguments: Dict[str, Any]) -> Dict[str, Any]:
    """Return graph statistics."""
    stats = store.stats()

    # Filter out zero-count entries for cleaner output
    stats["nodes_by_type"] = {
        k: v for k, v in stats["nodes_by_type"].items() if v > 0
    }
    stats["edges_by_type"] = {
        k: v for k, v in stats["edges_by_type"].items() if v > 0
    }
    stats["nodes_by_tier"] = {
        k: v for k, v in stats["nodes_by_tier"].items() if v > 0
    }

    return stats


def tool_list_flows(store: GraphStore, arguments: Dict[str, Any]) -> Dict[str, Any]:
    """List execution flows detected in the graph (from Pass 5)."""
    flow_type_filter = arguments.get("flow_type")
    limit = arguments.get("limit", 20)

    # Flow nodes are Event nodes tagged with "flow"
    flow_nodes = [
        n for n in store._nodes.values()
        if n.type == NodeType.EVENT and "flow" in n.tags
    ]

    # Apply flow_type filter
    if flow_type_filter:
        flow_nodes = [
            n for n in flow_nodes
            if n.metadata.get("flow_type") == flow_type_filter
        ]

    # Sort by step count descending (most interesting flows first)
    flow_nodes.sort(key=lambda n: -(n.metadata.get("step_count", 0)))

    flows = []
    for n in flow_nodes[:limit]:
        meta = n.metadata
        flows.append({
            "id": n.id,
            "name": n.name,
            "flow_type": meta.get("flow_type", "general"),
            "step_count": meta.get("step_count", 0),
            "entry_point_id": meta.get("entry_point_id"),
            "terminal_id": meta.get("terminal_id"),
            "steps": meta.get("steps", []),
            "description": n.description,
        })

    return {
        "total_flows": len(flow_nodes),
        "returned": len(flows),
        "flows": flows,
    }


def tool_list_clusters(store: GraphStore, arguments: Dict[str, Any]) -> Dict[str, Any]:
    """List community clusters (Leiden/Louvain) from the graph."""
    min_size = arguments.get("min_size", 2)
    include_members = arguments.get("include_members", True)
    limit = arguments.get("limit", 20)

    # Run Leiden clustering via intelligence module
    # GraphIntelligence needs a file path — find the matching graph file
    try:
        from intelligence import GraphIntelligence
        # Try to find the graph file for this store
        graph_file = _find_graph_file_for_store(store)
        if graph_file is None:
            return {"error": "No graph file found for current store. Save graph first.", "clusters": []}
        intel = GraphIntelligence(str(graph_file))
        leiden_result = intel.leiden_communities()
    except Exception as exc:
        return {"error": f"Clustering failed: {str(exc)}", "clusters": []}

    cluster_info = leiden_result.get("cluster_info", [])

    # Filter by min_size
    cluster_info = [c for c in cluster_info if c.get("size", 0) >= min_size]

    # Sort by size descending
    cluster_info.sort(key=lambda c: -c.get("size", 0))

    clusters = []
    for c in cluster_info[:limit]:
        entry = {
            "id": c.get("id"),
            "label": c.get("label", f"Cluster {c.get('id')}"),
            "size": c.get("size", 0),
            "cohesion": round(c.get("cohesion", 0), 3),
        }
        if include_members:
            entry["members"] = c.get("members", [])
        clusters.append(entry)

    return {
        "total_clusters": len(leiden_result.get("cluster_info", [])),
        "returned": len(clusters),
        "backend": "leiden" if _has_leiden() else "louvain",
        "clusters": clusters,
    }


def _has_leiden() -> bool:
    """Check if leidenalg is available."""
    try:
        import leidenalg  # noqa: F401
        return True
    except ImportError:
        return False


def _find_graph_file_for_store(store: GraphStore) -> Optional[Path]:
    """Find the graph file on disk that matches this store's content."""
    # Check if the store was loaded from a file (stash the path at load time)
    if hasattr(store, '_loaded_from') and store._loaded_from:
        p = Path(store._loaded_from)
        if p.exists():
            return p
    # Fallback: scan graphs/ directory for a file with matching node count
    if not GRAPHS_DIR.exists():
        return None
    node_count = len(store._nodes)
    for f in GRAPHS_DIR.glob("*_graph.json"):
        try:
            size = f.stat().st_size
            # Quick heuristic: larger graphs have more nodes
            # Just return first match for now
            import json as _json
            with open(f, "r", encoding="utf-8") as fh:
                data = _json.load(fh)
            if len(data.get("nodes", [])) == node_count:
                return f
        except Exception:
            continue
    return None


def tool_detect_changes(store: GraphStore, arguments: Dict[str, Any]) -> Dict[str, Any]:
    """Map git changes to affected graph nodes."""
    repo_path = arguments.get("repo_path", ".")
    diff_target = arguments.get("diff_target", "working")

    # Build git diff command
    if diff_target == "staged":
        cmd = ["git", "diff", "--cached", "--name-only"]
    elif diff_target == "working":
        cmd = ["git", "diff", "--name-only", "HEAD"]
    else:
        cmd = ["git", "diff", "--name-only", diff_target]

    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, cwd=repo_path, timeout=10,
        )
        if result.returncode != 0:
            return {"error": f"git diff failed: {result.stderr.strip()}"}
        changed_files = [f.strip() for f in result.stdout.strip().split("\n") if f.strip()]
    except FileNotFoundError:
        return {"error": "git not found in PATH"}
    except subprocess.TimeoutExpired:
        return {"error": "git diff timed out"}

    if not changed_files:
        return {"changed_files": [], "affected_nodes": [], "message": "No changes detected"}

    # Map changed files to graph nodes
    affected_nodes = []
    for node in store._nodes.values():
        node_path = (
            node.metadata.get("path", "")
            or node.metadata.get("file_path", "")
            or ""
        )
        if not node_path:
            continue
        # Normalize paths for comparison
        node_path_norm = node_path.replace("\\", "/").lower()
        for changed in changed_files:
            changed_norm = changed.replace("\\", "/").lower()
            if node_path_norm.endswith(changed_norm) or changed_norm.endswith(node_path_norm):
                affected_nodes.append({
                    "id": node.id,
                    "type": node.type.value,
                    "name": node.name,
                    "file": node_path,
                    "tier": node.tier.value,
                })
                break

    # Also get blast radius for affected nodes
    blast_radius_ids = set()
    for an in affected_nodes:
        upstream = store.get_upstream(an["id"], max_depth=2)
        for u in upstream:
            blast_radius_ids.add(u.id)

    return {
        "changed_files": changed_files,
        "affected_nodes": affected_nodes,
        "affected_count": len(affected_nodes),
        "blast_radius_count": len(blast_radius_ids),
        "blast_radius_node_ids": list(blast_radius_ids)[:50],
    }


# =============================================================================
# TOOL DISPATCH
# =============================================================================

TOOL_HANDLERS = {
    "search_entity": tool_search_entity,
    "traverse_graph": tool_traverse_graph,
    "get_context": tool_get_context,
    "impact_analysis": tool_impact_analysis,
    "get_stats": tool_get_stats,
    "list_flows": tool_list_flows,
    "list_clusters": tool_list_clusters,
    "detect_changes": tool_detect_changes,
}


# =============================================================================
# MCP RESOURCES
# =============================================================================

GRAPHS_DIR = Path(__file__).resolve().parent.parent / "graphs"


def _list_graph_names() -> List[str]:
    """List available graph names from the graphs/ directory."""
    if not GRAPHS_DIR.exists():
        return []
    return sorted([
        f.stem.replace("_graph", "")
        for f in GRAPHS_DIR.glob("*_graph.json")
    ])


RESOURCE_TEMPLATES = [
    {
        "uriTemplate": "wi://repos",
        "name": "Indexed Repositories",
        "description": "List all indexed workspaces/repos with graph files",
        "mimeType": "application/json",
    },
    {
        "uriTemplate": "wi://repo/{name}/stats",
        "name": "Repository Statistics",
        "description": "Node/edge counts, type breakdown, health metrics for a repo",
        "mimeType": "application/json",
    },
    {
        "uriTemplate": "wi://repo/{name}/clusters",
        "name": "Community Clusters",
        "description": "Leiden/Louvain community clusters for a repo",
        "mimeType": "application/json",
    },
    {
        "uriTemplate": "wi://repo/{name}/flows",
        "name": "Execution Flows",
        "description": "Detected execution flows (entry points → terminal nodes)",
        "mimeType": "application/json",
    },
    {
        "uriTemplate": "wi://repo/{name}/schema",
        "name": "Ontology Schema",
        "description": "WI ontology: all node types, edge types, and tiers",
        "mimeType": "application/json",
    },
]


def _load_graph_for_resource(name: str) -> Optional[GraphStore]:
    """Load a graph by repo name for resource reading."""
    graph_file = GRAPHS_DIR / f"{name}_graph.json"
    if not graph_file.exists():
        return None
    s = GraphStore()
    s.load(graph_file)
    return s


def read_resource(uri: str, current_store: GraphStore) -> Dict[str, Any]:
    """Read a resource by URI."""
    if uri == "wi://repos":
        repos = []
        for name in _list_graph_names():
            graph_file = GRAPHS_DIR / f"{name}_graph.json"
            size_kb = graph_file.stat().st_size // 1024 if graph_file.exists() else 0
            repos.append({"name": name, "graph_file": str(graph_file), "size_kb": size_kb})
        return {"repos": repos, "count": len(repos)}

    # Parse wi://repo/{name}/... URIs
    if uri.startswith("wi://repo/"):
        parts = uri[len("wi://repo/"):].split("/")
        if len(parts) < 2:
            return {"error": f"Invalid resource URI: {uri}"}
        name = parts[0]
        resource_type = parts[1]

        s = _load_graph_for_resource(name)
        if s is None:
            return {"error": f"No graph found for repo '{name}'. Available: {_list_graph_names()}"}

        if resource_type == "stats":
            stats = s.stats()
            stats["nodes_by_type"] = {k: v for k, v in stats["nodes_by_type"].items() if v > 0}
            stats["edges_by_type"] = {k: v for k, v in stats["edges_by_type"].items() if v > 0}
            stats["nodes_by_tier"] = {k: v for k, v in stats["nodes_by_tier"].items() if v > 0}
            return stats

        elif resource_type == "clusters":
            return tool_list_clusters(s, {"min_size": 2, "include_members": True, "limit": 50})

        elif resource_type == "flows":
            return tool_list_flows(s, {"limit": 50})

        elif resource_type == "schema":
            return {
                "node_types": [
                    {"name": t.value, "tier": Tier(NODE_TIER[t]).value if t in NODE_TIER else "micro"}
                    for t in NodeType
                ],
                "edge_types": [{"name": t.value} for t in EdgeType],
                "tiers": [{"name": t.value, "description": t.name} for t in Tier],
            }

        return {"error": f"Unknown resource type: {resource_type}"}

    return {"error": f"Unknown resource URI: {uri}"}


# Import NODE_TIER for schema resource
from ontology import NODE_TIER


# =============================================================================
# MCP PROMPTS
# =============================================================================

PROMPTS = [
    {
        "name": "detect_impact",
        "description": (
            "Pre-commit change analysis: identify which files changed, map them to graph nodes, "
            "trace upstream dependencies, and assess the blast radius before committing."
        ),
        "arguments": [
            {
                "name": "file_or_function",
                "description": "The file path or function name you're about to change",
                "required": True,
            },
        ],
    },
    {
        "name": "explore_area",
        "description": (
            "Navigate unfamiliar code: start from a node, explore its neighborhood, "
            "understand what it connects to, what calls it, and what it depends on."
        ),
        "arguments": [
            {
                "name": "starting_point",
                "description": "Function name, file name, or node ID to start exploring from",
                "required": True,
            },
        ],
    },
    {
        "name": "generate_map",
        "description": (
            "Generate architecture documentation: list top-level services, key endpoints, "
            "data flows, and community clusters. Produces a structured overview."
        ),
        "arguments": [],
    },
]


def get_prompt_messages(name: str, arguments: Dict[str, str]) -> List[Dict[str, Any]]:
    """Generate prompt messages for a given prompt name."""
    if name == "detect_impact":
        target = arguments.get("file_or_function", "unknown")
        return [
            {
                "role": "user",
                "content": {
                    "type": "text",
                    "text": (
                        f"I'm about to change '{target}'. Help me understand the impact:\n\n"
                        "1. Use `search_entity` to find the node in the graph\n"
                        "2. Use `impact_analysis` on that node to see the blast radius\n"
                        "3. Use `list_clusters` to see if this node is part of a tightly-coupled cluster\n"
                        "4. Use `detect_changes` if there are already staged git changes\n\n"
                        "Summarize: what could break, what tests to run, and risk level."
                    ),
                },
            },
        ]

    elif name == "explore_area":
        start = arguments.get("starting_point", "unknown")
        return [
            {
                "role": "user",
                "content": {
                    "type": "text",
                    "text": (
                        f"I want to understand the code area around '{start}':\n\n"
                        "1. Use `search_entity` to find it in the graph\n"
                        "2. Use `traverse_graph` with direction='both' and depth=2 to see its neighborhood\n"
                        "3. Use `list_flows` to see if it's part of any execution flows\n"
                        "4. Use `get_context` with focus='understanding code structure' for full context\n\n"
                        "Explain: what this code does, what calls it, what it depends on, "
                        "and which execution flows it participates in."
                    ),
                },
            },
        ]

    elif name == "generate_map":
        return [
            {
                "role": "user",
                "content": {
                    "type": "text",
                    "text": (
                        "Generate an architecture overview of this codebase:\n\n"
                        "1. Use `get_stats` to get the graph shape (node/edge counts by type)\n"
                        "2. Use `list_clusters` to find community groups\n"
                        "3. Use `list_flows` to find execution paths\n"
                        "4. Use `search_entity` with type_filter='Endpoint' to find API surface\n"
                        "5. Use `search_entity` with type_filter='Service' to find services\n\n"
                        "Produce a structured architecture doc with:\n"
                        "- Services and their responsibilities\n"
                        "- Key endpoints and their flows\n"
                        "- Community clusters and what they represent\n"
                        "- Data models and their relationships"
                    ),
                },
            },
        ]

    return [{"role": "user", "content": {"type": "text", "text": f"Unknown prompt: {name}"}}]


# =============================================================================
# VIEWER BROADCAST
# =============================================================================

_VIEWER_URL = "http://127.0.0.1:8080/api/agent-activity"

def _broadcast_activity(tool_name: str, arguments: Dict[str, Any], result_summary: str):
    """Broadcast agent activity to viewer (fire-and-forget, non-blocking)."""
    def _send():
        try:
            # Extract the focus target from arguments
            focus = (
                arguments.get("node_id")
                or arguments.get("query")
                or arguments.get("start_node_id")
                or "graph"
            )
            payload = json.dumps({
                "type": "agent_activity",
                "tool": tool_name,
                "focus": focus,
                "args": {k: v for k, v in arguments.items() if k != "token_budget"},
                "summary": result_summary,
                "source": "mcp_server",
            }).encode("utf-8")
            req = Request(_VIEWER_URL, data=payload, method="POST")
            req.add_header("Content-Type", "application/json")
            urlopen(req, timeout=2)
        except (URLError, OSError):
            pass  # Viewer not running — that's fine
    threading.Thread(target=_send, daemon=True).start()


def call_tool(store: GraphStore, tool_name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
    """Dispatch a tool call to the appropriate handler."""
    handler = TOOL_HANDLERS.get(tool_name)
    if not handler:
        return {"error": f"Unknown tool: '{tool_name}'. Available: {list(TOOL_HANDLERS.keys())}"}
    try:
        result = handler(store, arguments)
        # Broadcast activity to viewer (Oracle v2 pattern: agent uses tool → WI sees it)
        summary = ""
        if "matches" in result:
            summary = f"{len(result['matches'])} matches"
        elif "nodes" in result:
            summary = f"{len(result['nodes'])} nodes"
        elif "total_nodes" in result:
            summary = f"{result['total_nodes']} nodes, {result['total_edges']} edges"
        elif "affected_nodes" in result:
            summary = f"{len(result['affected_nodes'])} affected"
        _broadcast_activity(tool_name, arguments, summary)
        return result
    except Exception as exc:
        return {"error": f"Tool '{tool_name}' failed: {str(exc)}"}


# =============================================================================
# JSON-RPC REQUEST HANDLING
# =============================================================================

def handle_request(store: GraphStore, request: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """
    Handle a single JSON-RPC 2.0 request.

    Returns a response dict, or None for notifications (requests without an id).
    """
    method = request.get("method")
    params = request.get("params", {})
    req_id = request.get("id")

    log(f"<-- {method}" + (f" (id={req_id})" if req_id is not None else " (notification)"))

    # Notifications (no id) don't need a response
    is_notification = req_id is None

    try:
        result = _dispatch_method(store, method, params)
    except Exception as exc:
        if is_notification:
            log(f"Error handling notification '{method}': {exc}")
            return None
        return _error_response(req_id, -32603, f"Internal error: {str(exc)}")

    if is_notification:
        return None

    return {
        "jsonrpc": "2.0",
        "id": req_id,
        "result": result,
    }


def _dispatch_method(store: GraphStore, method: str, params: Dict[str, Any]) -> Any:
    """Route a method call to the appropriate handler."""

    if method == "initialize":
        return {
            "protocolVersion": "2024-11-05",
            "capabilities": {
                "tools": {},
                "resources": {},
                "prompts": {},
            },
            "serverInfo": {
                "name": "workspace-intelligence",
                "version": "0.2.0",
            },
        }

    elif method == "notifications/initialized":
        # Client acknowledges initialization -- nothing to return
        return None

    # --- Tools ---
    elif method == "tools/list":
        return {"tools": TOOLS}

    elif method == "tools/call":
        tool_name = params.get("name")
        arguments = params.get("arguments", {})
        result = call_tool(store, tool_name, arguments)
        return {
            "content": [
                {
                    "type": "text",
                    "text": json.dumps(result, indent=2, default=str),
                }
            ],
        }

    # --- Resources ---
    elif method == "resources/list":
        return {"resourceTemplates": RESOURCE_TEMPLATES}

    elif method == "resources/read":
        uri = params.get("uri", "")
        data = read_resource(uri, store)
        return {
            "contents": [
                {
                    "uri": uri,
                    "mimeType": "application/json",
                    "text": json.dumps(data, indent=2, default=str),
                }
            ],
        }

    # --- Prompts ---
    elif method == "prompts/list":
        return {"prompts": PROMPTS}

    elif method == "prompts/get":
        prompt_name = params.get("name", "")
        arguments = params.get("arguments", {})
        messages = get_prompt_messages(prompt_name, arguments)
        return {"messages": messages}

    elif method == "ping":
        return {}

    else:
        raise ValueError(f"Unknown method: '{method}'")


def _error_response(req_id: Any, code: int, message: str) -> Dict[str, Any]:
    """Build a JSON-RPC error response."""
    return {
        "jsonrpc": "2.0",
        "id": req_id,
        "error": {
            "code": code,
            "message": message,
        },
    }


# =============================================================================
# LOGGING (stderr only -- stdout is the protocol channel)
# =============================================================================

def log(message: str) -> None:
    """Log to stderr. Never write logs to stdout (that's the MCP channel)."""
    print(f"[mcp-server] {message}", file=sys.stderr, flush=True)


# =============================================================================
# MAIN LOOP
# =============================================================================

def run_server(graph_path: str) -> None:
    """
    Main server loop.

    Reads newline-delimited JSON-RPC from stdin, writes responses to stdout.
    """
    store = GraphStore()

    # Load graph if the file exists
    graph_file = Path(graph_path)
    if graph_file.exists():
        log(f"Loading graph from {graph_file}")
        store.load(graph_file)
        store._loaded_from = str(graph_file.resolve())
        stats = store.stats()
        log(f"Loaded {stats['total_nodes']} nodes, {stats['total_edges']} edges")
    else:
        log(f"Graph file not found: {graph_file} -- starting with empty graph")

    log("MCP server ready (stdio transport)")

    # Read from stdin line by line
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue

        # Parse JSON-RPC request
        try:
            request = json.loads(line)
        except json.JSONDecodeError as exc:
            error = _error_response(None, -32700, f"Parse error: {str(exc)}")
            _write_response(error)
            continue

        # Handle the request
        response = handle_request(store, request)

        # Only write a response for requests (not notifications)
        if response is not None:
            _write_response(response)


def _write_response(response: Dict[str, Any]) -> None:
    """Write a JSON-RPC response to stdout."""
    output = json.dumps(response, default=str)
    sys.stdout.write(output + "\n")
    sys.stdout.flush()


# =============================================================================
# ENTRY POINT
# =============================================================================

def main():
    parser = argparse.ArgumentParser(
        description="Workspace Intelligence MCP Server",
    )
    parser.add_argument(
        "--graph",
        type=str,
        default="workspace_graph.json",
        help="Path to the graph JSON file (default: workspace_graph.json)",
    )
    args = parser.parse_args()
    run_server(args.graph)


if __name__ == "__main__":
    main()
