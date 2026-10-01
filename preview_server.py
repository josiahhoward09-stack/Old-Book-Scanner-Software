#!/usr/bin/env python3
"""Local preview server for editing and refreshing index.html."""

import json
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


ROOT = Path(__file__).resolve().parent


class PreviewHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(ROOT), **kwargs)

    def do_GET(self):
        if self.path == "/api/status":
            payload = json.dumps({"hostname": "local-preview", "pico_connected": False, "mpremote": False, "version": "local-preview"}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(payload)
            return
        super().do_GET()


if __name__ == "__main__":
    server = ThreadingHTTPServer(("127.0.0.1", 8093), PreviewHandler)
    print("Book Scanner local preview: http://127.0.0.1:8093")
    print("Edit index.html, save it, then refresh the browser.")
    server.serve_forever()
