"""WI Code Graph Adapter - wraps existing graphs/*.json files."""
import json
from pathlib import Path
from typing import List

from .base import BaseAdapter, GraphSource, UniversalGraph

GRAPHS_DIR = Path(__file__).resolve().parent.parent.parent / "graphs"

# WI edge groups (mirrors viewer's EDGE_GROUPS)
WI_EDGE_GROUPS = {
    "structural": ["CONTAINS", "DEFINES", "IMPORTS", "IMPLEMENTS", "INHERITS"],
    "data_flow": ["READS_DB", "WRITES_DB", "MIGRATES"],
    "communication": ["CALLS_API", "CALLS_SERVICE", "CALLS", "WEBHOOK_SEND", "WEBHOOK_RECEIVE"],
    "event_async": ["EMITS_EVENT", "CONSUMES_EVENT", "ENQUEUES", "DEQUEUES", "SCHEDULES"],
    "caching": ["CACHE_READ", "CACHE_WRITE"],
    "routing": ["ROUTES_TO", "INTERCEPTS", "VALIDATES", "AUTHENTICATES"],
    "config_deploy": ["DEPENDS_ON", "DEPLOYED_BY", "CONFIGURES"],
    "quality": ["TESTS"],
}


class WIAdapter(BaseAdapter):
    def list_sources(self) -> List[GraphSource]:
        """One source per scanned graph file."""
        sources = []
        if not GRAPHS_DIR.exists():
            return sources
        for f in sorted(GRAPHS_DIR.glob("*.json")):
            size_kb = f.stat().st_size // 1024
            sources.append(GraphSource(
                id=f"wi:{f.stem}",
                name=f.stem,
                adapter="wi",
                description=f"{size_kb} KB",
                group="WI Code Graphs",
                config={"path": str(f)},
            ))
        return sources

    def load_graph(self, source_id: str) -> UniversalGraph:
        for src in self.list_sources():
            if src.id == source_id:
                graph_path = src.config["path"]
                break
        else:
            raise ValueError(f"Unknown WI source: {source_id}")

        src = GraphSource(
            id=source_id,
            name=Path(graph_path).stem,
            adapter="wi",
            group="WI Code Graphs",
            description=f"Code graph ({Path(graph_path).stat().st_size // 1024} KB)",
            config={"path": graph_path},
        )

        with open(graph_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        return UniversalGraph(
            source=src,
            nodes=data.get("nodes", []),
            edges=data.get("edges", []),
            capabilities={
                "has_hierarchy": True,
                "has_categories": False,
                "has_concepts": False,
                "has_timestamps": False,
                "has_confidence": True,
                "edge_groups": WI_EDGE_GROUPS,
                "type_colors": {},
                "type_tiers": {},
            },
        )
