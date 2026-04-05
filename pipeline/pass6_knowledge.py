"""Pass 6: Knowledge Graph Scanner

Scans a project folder for knowledge files (markdown docs, principles,
patterns, learnings, decision records, ADRs) and adds them as nodes
to the graph with semantic edges.

Uses FILE node type with knowledge-specific tags (knowledge, principle,
pattern, learning, decision, runbook, doc).
"""
import re
from pathlib import Path
from typing import Dict, List, Any

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ontology import GraphNode, GraphEdge, NodeType, EdgeType, Provenance

# Patterns that identify knowledge directories
KNOWLEDGE_DIR_PATTERNS = [
    r"adr[s]?/", r"decisions?/", r"docs?/", r"documentation/",
    r"knowledge/", r"learnings?/", r"patterns?/", r"principles?/",
    r"retros?/", r"retrospectives?/", r"notes?/", r"wiki/",
    r"guides?/", r"runbooks?/", r"playbooks?/",
]

KNOWLEDGE_EXTENSIONS = {".md", ".mdx", ".txt", ".rst", ".adoc", ".org"}

TITLE_RE = re.compile(r"^#\s+(.+)$", re.MULTILINE)
TAG_RE = re.compile(
    r"(?:tags?|labels?|topics?)\s*[:=]\s*\[?([^\]\n]+)\]?",
    re.IGNORECASE,
)
CATEGORY_RE = re.compile(
    r"(?:category|type|kind|status)\s*[:=]\s*(.+)",
    re.IGNORECASE,
)
DATE_RE = re.compile(r"(\d{4}-\d{2}-\d{2})")


def _is_knowledge_path(rel_path: str) -> bool:
    lower = rel_path.replace("\\", "/").lower()
    for pattern in KNOWLEDGE_DIR_PATTERNS:
        if re.search(pattern, lower):
            return True
    return False


def _is_knowledge_file(path: Path, rel_path: str) -> bool:
    if path.suffix.lower() not in KNOWLEDGE_EXTENSIONS:
        return False
    if _is_knowledge_path(rel_path):
        return True
    name_lower = path.stem.lower()
    knowledge_names = {
        "changelog", "contributing", "architecture", "design",
        "decisions", "principles", "patterns", "runbook",
        "playbook", "postmortem", "incident", "rfc",
    }
    if name_lower in knowledge_names:
        return True
    if re.match(r"^\d{3,4}[-_]", path.name):
        return True
    return False


def _extract_metadata(content: str) -> Dict[str, Any]:
    meta: Dict[str, Any] = {}
    m = TITLE_RE.search(content[:2000])
    if m:
        meta["title"] = m.group(1).strip()
    m = CATEGORY_RE.search(content[:3000])
    if m:
        meta["category"] = m.group(1).strip().strip("\"'")
    m = TAG_RE.search(content[:3000])
    if m:
        raw = m.group(1).strip()
        tags = [t.strip().strip("\"'#") for t in re.split(r"[,;]", raw)]
        meta["tags"] = [t for t in tags if t]
    m = DATE_RE.search(content[:500])
    if m:
        meta["date"] = m.group(1)
    meta["word_count"] = len(content.split())
    return meta


def _classify_doc(rel_path: str, meta: Dict) -> str:
    lower = rel_path.lower()
    cat = meta.get("category", "").lower()
    if "principle" in lower or "principle" in cat:
        return "principle"
    if "pattern" in lower or "pattern" in cat:
        return "pattern"
    if "learning" in lower or "retro" in lower or "learning" in cat:
        return "learning"
    if "decision" in lower or "adr" in lower:
        return "decision"
    if "runbook" in lower or "playbook" in lower:
        return "runbook"
    return "doc"


def run_knowledge_scan(store, project_paths: List[str]) -> Dict[str, int]:
    """Scan project folders for knowledge files and add to graph.

    Returns dict with counts: files_scanned, nodes_created, edges_created
    """
    files_scanned = 0
    nodes_created = 0
    edges_created = 0

    for project_root in project_paths:
        root = Path(project_root)
        if not root.is_dir():
            continue

        knowledge_files = []
        for ext in KNOWLEDGE_EXTENSIONS:
            for path in root.rglob(f"*{ext}"):
                parts = path.relative_to(root).parts
                if any(p.startswith(".") or p == "node_modules" for p in parts):
                    continue
                rel = str(path.relative_to(root))
                if _is_knowledge_file(path, rel):
                    knowledge_files.append((path, rel))

        if not knowledge_files:
            continue

        all_doc_nodes = []
        for path, rel_path in knowledge_files:
            files_scanned += 1
            try:
                content = path.read_text(encoding="utf-8", errors="replace")
            except Exception:
                continue

            meta = _extract_metadata(content)
            doc_type = _classify_doc(rel_path, meta)
            node_id = f"FILE:{rel_path}"
            name = meta.get(
                "title",
                path.stem.replace("-", " ").replace("_", " ").title(),
            )

            # Build tags: always include "knowledge" + the doc classification
            tags = ["knowledge", doc_type]
            if meta.get("tags"):
                tags.extend(meta["tags"])

            node = GraphNode(
                id=node_id,
                type=NodeType.FILE,
                name=name,
                description=f"Knowledge {doc_type}: {name}",
                provenance=Provenance.SCANNER,
                confidence=0.8,
                tags=tags,
                metadata={
                    "knowledge_type": doc_type,
                    "word_count": meta.get("word_count", 0),
                    "category": meta.get("category", ""),
                    "date": meta.get("date", ""),
                    "source_file": rel_path,
                },
            )

            # Check if node already exists (from pass 0/1)
            existing = store.get_node(node_id)
            if existing:
                # Enrich existing node with knowledge tags
                existing.tags = list(set(existing.tags + tags))
                existing.metadata.update(node.metadata)
                if not existing.description:
                    existing.description = node.description
                store.add_node(existing)
            else:
                store.add_node(node)
                nodes_created += 1

            all_doc_nodes.append((node_id, doc_type, meta, rel_path))

        # Cross-link knowledge docs that share tags
        tag_index: Dict[str, List[str]] = {}
        for nid, _, meta, _ in all_doc_nodes:
            for tag in meta.get("tags", []):
                tag_index.setdefault(tag.lower(), []).append(nid)

        for tag, nids in tag_index.items():
            if len(nids) < 2:
                continue
            for i, src in enumerate(nids):
                for tgt in nids[i + 1:]:
                    edge = GraphEdge(
                        source_id=src,
                        target_id=tgt,
                        type=EdgeType.IMPORTS,  # Reuse IMPORTS for related docs
                        description=f"Shared knowledge tag: {tag}",
                        provenance=Provenance.SCANNER,
                        confidence=0.6,
                        metadata={"shared_tag": tag, "knowledge_link": True},
                    )
                    store.add_edge(edge)
                    edges_created += 1

    return {
        "files_scanned": files_scanned,
        "nodes_created": nodes_created,
        "edges_created": edges_created,
    }
