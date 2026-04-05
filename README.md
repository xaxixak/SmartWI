# Workspace Intelligence (WI)

A semantic code analysis tool that builds knowledge graphs from your codebase. It goes beyond basic static analysis — using Tree-sitter AST parsing, regex pattern detection, and optional LLM enrichment to map how your code actually connects: endpoints, data models, events, API calls, and execution flows.

## What It Does

Point WI at any folder and it builds a graph of your code's architecture:

- **Functions, classes, imports** — extracted via Tree-sitter
- **Endpoints, models, events** — detected via regex patterns
- **Behavioral connections** — who calls what, what reads/writes the DB, what emits events
- **Execution flows** — traces entry points through call chains
- **LLM enrichment** (optional) — AI-powered semantic analysis for deeper understanding

Then explore the graph in a 3D interactive viewer.

## Quick Start

```bash
# Install dependencies
pip install -r requirements.txt

# Index a codebase
python cli.py index /path/to/your/project

# Launch the viewer
python viewer/server.py
# Open http://127.0.0.1:8080
```

## Pipeline

WI runs a 6-pass pipeline:

| Pass | What | How |
|------|------|-----|
| 0 | Project discovery | Scanner + bridge (workspace/project structure) |
| 0b | File & module nodes | Directory structure mapping |
| 1 | AST extraction | Tree-sitter (functions, classes, imports) |
| 2 | Pattern detection | Regex (endpoints, models, events) |
| 2b | Behavioral edges | CALLS, READS_DB, EMITS_EVENT connections |
| 3 | LLM enrichment | Anthropic API semantic analysis (optional, paid) |
| 4 | Validation | Confidence scoring and graph health checks |
| 5 | Flow tracing | Entry point to call chain execution flows |

## CLI Commands

```bash
python cli.py index <path>      # Full pipeline scan
python cli.py update <path>     # Incremental update (changed files only)
python cli.py watch <path>      # Real-time file monitoring
python cli.py query <name>      # Search nodes
python cli.py impact <name>     # Impact analysis
python cli.py stats             # Graph statistics
python cli.py export            # Export graph
```

## Viewer

Interactive 3D graph visualization at `localhost:8080`:

- **3D force-directed graph** with zoom, pan, rotate
- **Structure tree** with search and filtering
- **Layer controls** — filter by macro/meso/micro tiers
- **Focus mode** — double-click any node to explore its neighborhood
- **Keyboard shortcuts** — Escape, +/-, 1-4, /, f
- **Multi-source support** — load graphs from WI scans, GitNexus, or custom adapters

## LLM Enrichment (Optional)

Pass 3 uses the Anthropic API for deeper semantic analysis. To enable:

```bash
# Set your API key
export ANTHROPIC_API_KEY=sk-ant-...

# Run with LLM pass included
python cli.py index /path/to/project --passes all
```

Without an API key, WI runs passes 0-2b + 4-5 (still useful without LLM).

## MCP Server

WI includes an MCP server for AI agent integration:

```bash
python api/mcp_server.py
```

Exposes 8 tools, 5 resources, and 3 prompts for querying the code graph from any MCP-compatible client.

## Tech Stack

- **Python** — core runtime
- **Tree-sitter** — multi-language AST parsing
- **NetworkX** — graph storage and traversal
- **Pydantic** — schema validation
- **Anthropic API** — LLM enrichment (optional)
- **D3.js / 3D Force Graph** — visualization

## Credits

Author: **x3k**

Credit to **Nat Weerawan**
