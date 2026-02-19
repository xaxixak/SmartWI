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


def list_wi_files():
    """Return {stem: path} for all WI graph JSON files."""
    if not GRAPHS_DIR.exists():
        return {}
    return {f.stem: str(f) for f in sorted(GRAPHS_DIR.glob("*.json"))}


class WIAdapter(BaseAdapter):
    def list_sources(self) -> List[GraphSource]:
        """Expose ONE source entry for WI code graphs (not one per file).
        Individual file selection stays in the viewer's existing Select Graph dropdown."""
        files = list_wi_files()
        if not files:
            return []
        count = len(files)
        return [GraphSource(
            id="wi",
            name="WI Code Graphs",
            adapter="wi",
            description=f"{count} scanned project{'s' if count != 1 else ''} — use Select Graph to pick",
        )]

    def load_graph(self, source_id: str) -> UniversalGraph:
        if source_id != "wi":
            # Also support legacy wi:<stem> IDs for backward compat
            if source_id.startswith("wi:"):
                stem = source_id[3:]
                files = list_wi_files()
                graph_path = files.get(stem)
                if not graph_path:
                    raise ValueError(f"WI graph not found: {stem}")
            else:
                raise ValueError(f"Unknown WI source: {source_id}")
        else:
            # "wi" with no file = load first available
            files = list_wi_files()
            if not files:
                raise ValueError("No WI graphs available")
            graph_path = next(iter(files.values()))

        src = GraphSource(
            id=source_id,
            name=Path(graph_path).stem,
            adapter="wi",
            description=f"Code graph ({Path(graph_path).stat().st_size // 1024} KB)",
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
