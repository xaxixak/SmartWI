"""
Workspace Intelligence Layer - Graph Store

In-memory graph storage using NetworkX for MVP.
Provides CRUD operations for nodes and edges.
"""

import json
from pathlib import Path
from typing import List, Optional, Dict, Any
from datetime import datetime, timezone

import networkx as nx

from ontology import GraphNode, GraphEdge, NodeType, EdgeType, ContextPack


class GraphStore:
    """
    In-memory graph storage using NetworkX.
    
    For MVP/prototyping. Can be swapped for Neo4j/FalkorDB in production.
    """
    
    def __init__(self):
        self.graph = nx.DiGraph()
        self._nodes: Dict[str, GraphNode] = {}
        self._edges: Dict[str, GraphEdge] = {}  # key: "{source_id}->{target_id}:{type}"
    
    # =========================================================================
    # NODE OPERATIONS
    # =========================================================================
    
    def add_node(self, node: GraphNode) -> None:
        """Add or update a node in the graph."""
        self._nodes[node.id] = node
        self.graph.add_node(
            node.id,
            type=node.type.value,
            name=node.name,
            description=node.description,
            confidence=node.confidence,
            is_stale=node.is_stale,
            **node.metadata,
        )
    
    def get_node(self, node_id: str) -> Optional[GraphNode]:
        """Retrieve a node by ID."""
        return self._nodes.get(node_id)
    
    def get_nodes_by_type(self, node_type: NodeType) -> List[GraphNode]:
        """Get all nodes of a specific type."""
        return [n for n in self._nodes.values() if n.type == node_type]
    
    def mark_stale(self, node_id: str) -> None:
        """Mark a node as stale (needs re-indexing)."""
        if node_id in self._nodes:
            self._nodes[node_id].is_stale = True
            self.graph.nodes[node_id]["is_stale"] = True
    
    def delete_node(self, node_id: str) -> None:
        """Delete a node and all its edges."""
        if node_id in self._nodes:
            del self._nodes[node_id]
            self.graph.remove_node(node_id)
            # Clean up edges
            self._edges = {
                k: v for k, v in self._edges.items() 
                if v.source_id != node_id and v.target_id != node_id
            }
    
    # =========================================================================
    # EDGE OPERATIONS
    # =========================================================================
    
    def add_edge(self, edge: GraphEdge) -> None:
        """Add or update an edge in the graph."""
        edge_key = f"{edge.source_id}->{edge.target_id}:{edge.type.value}"
        self._edges[edge_key] = edge
        self.graph.add_edge(
            edge.source_id,
            edge.target_id,
            type=edge.type.value,
            description=edge.description,
            confidence=edge.confidence,
            **edge.metadata,
        )
    
    def get_edges_from(self, node_id: str) -> List[GraphEdge]:
        """Get all edges originating from a node."""
        return [e for e in self._edges.values() if e.source_id == node_id]
    
    def get_edges_to(self, node_id: str) -> List[GraphEdge]:
        """Get all edges pointing to a node."""
        return [e for e in self._edges.values() if e.target_id == node_id]
    
    def get_edges_by_type(self, edge_type: EdgeType) -> List[GraphEdge]:
        """Get all edges of a specific type."""
        return [e for e in self._edges.values() if e.type == edge_type]
    
    # =========================================================================
    # TRAVERSAL
    # =========================================================================
    
    def get_upstream(self, node_id: str, max_depth: int = 3) -> List[GraphNode]:
        """Get all nodes that point TO this node (callers, dependencies)."""
        upstream_ids = set()
        self._traverse_upstream(node_id, 0, max_depth, upstream_ids)
        return [self._nodes[nid] for nid in upstream_ids if nid in self._nodes]
    
    def get_downstream(self, node_id: str, max_depth: int = 3) -> List[GraphNode]:
        """Get all nodes that this node points TO (callees, dependents)."""
        downstream_ids = set()
        self._traverse_downstream(node_id, 0, max_depth, downstream_ids)
        return [self._nodes[nid] for nid in downstream_ids if nid in self._nodes]
    
    def _traverse_upstream(self, node_id: str, depth: int, max_depth: int, visited: set):
        if depth >= max_depth or node_id in visited:
            return
        for pred in self.graph.predecessors(node_id):
            if pred not in visited:
                visited.add(pred)
                self._traverse_upstream(pred, depth + 1, max_depth, visited)
    
    def _traverse_downstream(self, node_id: str, depth: int, max_depth: int, visited: set):
        if depth >= max_depth or node_id in visited:
            return
        for succ in self.graph.successors(node_id):
            if succ not in visited:
                visited.add(succ)
                self._traverse_downstream(succ, depth + 1, max_depth, visited)
    
    # =========================================================================
    # CONTEXT PACK (SKILL API)
    # =========================================================================
    
    def get_context(self, scope: str, focus: str) -> ContextPack:
        """
        Generate a context pack for the Skill API.
        
        Args:
            scope: Node ID or pattern to focus on (e.g., "service:order-api")
            focus: Task description (e.g., "Refactoring database schema")
        
        Returns:
            ContextPack with relevant nodes, upstream, downstream, and risk.
        """
        # Find the target node
        target = self.get_node(scope)
        if not target:
            # Try fuzzy match on name
            matches = [n for n in self._nodes.values() if scope.lower() in n.name.lower()]
            target = matches[0] if matches else None
        
        if not target:
            return ContextPack(scope=scope, focus=focus)
        
        upstream = self.get_upstream(target.id)
        downstream = self.get_downstream(target.id)
        
        # Simple risk assessment
        risk = None
        if len(upstream) > 5:
            risk = f"High Risk: {len(upstream)} components depend on this."
        elif len(downstream) > 10:
            risk = f"Medium Risk: This touches {len(downstream)} downstream nodes."
        
        return ContextPack(
            scope=scope,
            focus=focus,
            relevant_nodes=[target],
            upstream=upstream,
            downstream=downstream,
            risk_assessment=risk,
        )
    
    # =========================================================================
    # PERSISTENCE
    # =========================================================================
    
    def save(self, filepath: str | Path) -> None:
        """Save graph to JSON file."""
        data = {
            "nodes": [n.model_dump(mode="json") for n in self._nodes.values()],
            "edges": [e.model_dump(mode="json") for e in self._edges.values()],
        }
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, default=str)
    
    def load(self, filepath: str | Path) -> None:
        """Load graph from JSON file."""
        with open(filepath, "r", encoding="utf-8") as f:
            data = json.load(f)
        
        self.graph.clear()
        self._nodes.clear()
        self._edges.clear()
        
        for node_data in data.get("nodes", []):
            node = GraphNode(**node_data)
            self.add_node(node)
        
        for edge_data in data.get("edges", []):
            edge = GraphEdge(**edge_data)
            self.add_edge(edge)
    
    # =========================================================================
    # STATS
    # =========================================================================
    
    def stats(self) -> Dict[str, Any]:
        """Return graph statistics."""
        return {
            "total_nodes": len(self._nodes),
            "total_edges": len(self._edges),
            "nodes_by_type": {
                t.value: len(self.get_nodes_by_type(t)) 
                for t in NodeType
            },
            "edges_by_type": {
                t.value: len(self.get_edges_by_type(t))
                for t in EdgeType
            },
            "stale_nodes": len([n for n in self._nodes.values() if n.is_stale]),
        }
