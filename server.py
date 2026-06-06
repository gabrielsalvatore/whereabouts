#!/usr/bin/env python3
"""
server.py — Local server for the travel dashboard.
Notes are persisted to notes.json on disk.

Usage:
    python3 server.py

Then open: http://localhost:8765
Press Ctrl+C to stop.
"""
import json
import webbrowser
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import urlparse

BASE       = Path(__file__).parent
NOTES_FILE = BASE / "notes.json"
DASHBOARD  = BASE / "output" / "travel_dashboard.html"
PORT       = 8765


class Handler(BaseHTTPRequestHandler):

    def log_message(self, *_):
        pass  # silence request logs; errors still print

    # ── helpers ──────────────────────────────────────────────────────────────

    def _json(self, data, status=200):
        body = json.dumps(data, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", len(body))
        self.end_headers()
        self.wfile.write(body)

    def _file(self, path: Path, ctype="text/html; charset=utf-8"):
        body = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", len(body))
        self.end_headers()
        self.wfile.write(body)

    def _read_body(self):
        n = int(self.headers.get("Content-Length", 0))
        return self.rfile.read(n)

    # ── GET ───────────────────────────────────────────────────────────────────

    def do_GET(self):
        p = urlparse(self.path).path
        if p in ("/", "/dashboard"):
            self._file(DASHBOARD)
        elif p == "/api/notes":
            if NOTES_FILE.exists():
                self._json(json.loads(NOTES_FILE.read_text("utf-8")))
            else:
                self._json({"trips": {}, "people": {}})
        else:
            self.send_response(404); self.end_headers()

    # ── POST ──────────────────────────────────────────────────────────────────

    def do_POST(self):
        p = urlparse(self.path).path
        if p == "/api/notes":
            data = json.loads(self._read_body())
            NOTES_FILE.write_text(
                json.dumps(data, indent=2, ensure_ascii=False), "utf-8"
            )
            self._json({"ok": True})
        else:
            self.send_response(404); self.end_headers()


if __name__ == "__main__":
    httpd = HTTPServer(("localhost", PORT), Handler)
    url   = f"http://localhost:{PORT}"
    print(f"\n  ✈️  Travel Dashboard")
    print(f"  ─────────────────────────────────")
    print(f"  URL   : {url}")
    print(f"  Notes : {NOTES_FILE}")
    print(f"\n  Open the URL above, or it will open automatically.")
    print(f"  Press Ctrl+C to stop.\n")
    webbrowser.open(url)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n  Stopped.")
