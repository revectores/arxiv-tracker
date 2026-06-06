#!/usr/bin/env python3
"""Local server for paper tracker. Serves files and accepts PUT to update JSON files.

Usage: python3 serve.py [port]   (default port: 8765)
Then open: http://localhost:8765/collections/LLM_MEMORY.html
"""
import http.server
import json
import os
import sys

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8765
ROOT = os.path.abspath(".")


class Handler(http.server.SimpleHTTPRequestHandler):
    def do_PUT(self):
        target = os.path.abspath(self.translate_path(self.path))
        if not target.startswith(ROOT) or not target.endswith(".json"):
            self.send_error(403)
            return
        length = int(self.headers.get("Content-Length", 0))
        data = self.rfile.read(length)
        try:
            json.loads(data)
        except json.JSONDecodeError:
            self.send_error(400, "Invalid JSON")
            return
        tmp = target + ".tmp"
        with open(tmp, "wb") as f:
            f.write(data)
        os.replace(tmp, target)
        self.send_response(204)
        self.end_headers()

    def log_message(self, fmt, *args):
        if args and len(args) >= 2 and "PUT" in str(args[0]):
            super().log_message(fmt, *args)


if __name__ == "__main__":
    os.chdir(ROOT)
    with http.server.HTTPServer(("", PORT), Handler) as httpd:
        print(f"Serving at http://localhost:{PORT}")
        httpd.serve_forever()
