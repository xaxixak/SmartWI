"""
Workspace Intelligence - Graph Viewer HTTP Server

Simple HTTP server that serves the graph viewer UI and provides
the graph JSON data via a REST endpoint.

Usage:
    python server.py
    python server.py --port 9090
    python server.py --graph path/to/workspace_graph.json
    python server.py --no-open
"""

import http.server
import json
import argparse
import webbrowser
import threading
from pathlib import Path
from urllib.parse import urlparse


# Default locations to search for graph JSON
DEFAULT_GRAPH_PATHS = [
    Path(__file__).resolve().parent.parent / "workspace_graph.json",
    Path(__file__).resolve().parent.parent / "output" / "workspace_graph.json",
    Path.cwd() / "workspace_graph.json",
]

VIEWER_DIR = Path(__file__).resolve().parent


def find_graph_file(explicit_path: str | None = None) -> Path | None:
    """Locate the workspace graph JSON file."""
    if explicit_path:
        p = Path(explicit_path)
        if p.is_file():
            return p
        print(f"WARNING: Specified graph file not found: {explicit_path}")
        return None

    for candidate in DEFAULT_GRAPH_PATHS:
        if candidate.is_file():
            return candidate

    return None


def make_handler(graph_path: Path | None):
    """Create a request handler class with the graph path baked in."""

    class ViewerHandler(http.server.SimpleHTTPRequestHandler):
        """Serves the viewer UI and graph JSON API."""

        def __init__(self, *args, **kwargs):
            # Serve files from the viewer directory
            super().__init__(*args, directory=str(VIEWER_DIR), **kwargs)

        def do_GET(self):
            parsed = urlparse(self.path)
            path = parsed.path

            if path == "/api/graph":
                self._serve_graph()
            elif path == "/" or path == "/index.html":
                self._serve_file("index.html", "text/html")
            else:
                # Fall back to default static file serving
                super().do_GET()

        def _serve_graph(self):
            """Serve the graph JSON data."""
            if graph_path is None or not graph_path.is_file():
                # Return empty graph with helpful message
                data = {
                    "nodes": [{
                        "id": "info:no-data",
                        "type": "Project",
                        "name": "No graph data found",
                        "description": (
                            "Run the workspace-intelligence pipeline to generate "
                            "workspace_graph.json, then restart the viewer."
                        ),
                        "tier": "macro",
                        "confidence": 1.0,
                        "provenance": "human",
                        "is_stale": False,
                        "tags": [],
                        "metadata": {},
                        "version": 1,
                    }],
                    "edges": [],
                }
                self._send_json(data)
                return

            try:
                raw = graph_path.read_text(encoding="utf-8")
                data = json.loads(raw)
                self._send_json(data)
            except (json.JSONDecodeError, OSError) as e:
                self._send_error(500, f"Error reading graph file: {e}")

        def _serve_file(self, filename: str, content_type: str):
            """Serve a specific file from the viewer directory."""
            filepath = VIEWER_DIR / filename
            if not filepath.is_file():
                self._send_error(404, f"File not found: {filename}")
                return

            content = filepath.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", f"{content_type}; charset=utf-8")
            self.send_header("Content-Length", str(len(content)))
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            self.wfile.write(content)

        def _send_json(self, data: dict):
            """Send a JSON response."""
            body = json.dumps(data).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            self.wfile.write(body)

        def _send_error(self, code: int, message: str):
            """Send an error JSON response."""
            body = json.dumps({"error": message}).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format, *args):
            """Quieter logging -- only show non-200 or API requests."""
            status = args[1] if len(args) > 1 else ""
            if "/api/" in (args[0] if args else "") or str(status) != "200":
                super().log_message(format, *args)

    return ViewerHandler


def main():
    parser = argparse.ArgumentParser(
        description="Workspace Intelligence Graph Viewer"
    )
    parser.add_argument(
        "--port", type=int, default=8080,
        help="HTTP server port (default: 8080)",
    )
    parser.add_argument(
        "--graph", type=str, default=None,
        help="Path to workspace_graph.json (auto-detected if not specified)",
    )
    parser.add_argument(
        "--no-open", action="store_true",
        help="Do not auto-open browser",
    )
    args = parser.parse_args()

    graph_path = find_graph_file(args.graph)

    if graph_path:
        print(f"Graph data: {graph_path}")
        print(f"  Nodes: loading from file...")
    else:
        print("Graph data: NOT FOUND (viewer will show placeholder)")
        print("  Run the pipeline first, or use --graph to specify the path.")

    handler_class = make_handler(graph_path)
    server = http.server.HTTPServer(("127.0.0.1", args.port), handler_class)

    url = f"http://127.0.0.1:{args.port}"
    print(f"Server running at {url}")
    print("Press Ctrl+C to stop.\n")

    if not args.no_open:
        # Open browser after a short delay so server is ready
        threading.Timer(0.5, lambda: webbrowser.open(url)).start()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down.")
        server.shutdown()


if __name__ == "__main__":
    main()
