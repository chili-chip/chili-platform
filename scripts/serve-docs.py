#!/usr/bin/env python3
"""Serve /docs locally, including GitHub Pages-style 404.html."""

from __future__ import annotations

from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent / "docs"
HOST = "127.0.0.1"
PORT = 4173


class DocsHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(ROOT), **kwargs)

    def _missing_path(self) -> bool:
        relative = self.path.split("?", 1)[0].split("#", 1)[0]
        target = Path(self.translate_path(relative))
        if target.is_file():
            return False
        if target.is_dir() and (target / "index.html").is_file():
            return False
        return True

    def _send_not_found(self, include_body: bool) -> None:
        body = (ROOT / "404.html").read_bytes()
        self.send_response(404)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if include_body:
            self.wfile.write(body)

    def do_HEAD(self):
        if self._missing_path():
            self._send_not_found(include_body=False)
            return
        super().do_HEAD()

    def do_GET(self):
        if self._missing_path():
            self._send_not_found(include_body=True)
            return
        super().do_GET()


if __name__ == "__main__":
    print(f"Serving API docs at http://{HOST}:{PORT}")
    ThreadingHTTPServer((HOST, PORT), DocsHandler).serve_forever()
