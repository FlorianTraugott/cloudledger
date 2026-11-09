"""
Run CloudLedger on your laptop - no AWS account needed.

    python3 local/local_server.py            # then open http://localhost:8000

It serves the frontend/ folder and forwards /api/* requests to the SAME Lambda
handler that runs on AWS (backend/src/app.py), building an API Gateway-style event.
Data is kept in local/.data/ instead of DynamoDB / S3.
"""

import json
import os
import re
import sys
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qsl, urlsplit

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FRONTEND = os.path.join(ROOT, "frontend")
DATA_DIR = os.path.join(ROOT, "local", ".data")

os.environ.setdefault("STORAGE", "local")
os.environ.setdefault("LOCAL_DATA_DIR", DATA_DIR)
sys.path.insert(0, os.path.join(ROOT, "backend", "src"))
import app  # noqa: E402  (the real Lambda code)

ROUTE_PATTERNS = [(re.compile("^" + re.sub(r"\{(\w+)\}", r"(?P<\1>[^/]+)", r.split(" ")[1]) + "$"), r)
                  for r in app.ROUTES]
RECEIPTS = os.path.join(DATA_DIR, "receipts")


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *a, **kw):
        super().__init__(*a, directory=FRONTEND, **kw)

    def log_message(self, fmt, *args):
        sys.stderr.write("  %s\n" % (fmt % args))

    # -- local stand-in for the S3 receipts bucket ----------------------- #
    def _receipt_path(self):
        key = self.path.split("/local-receipts/", 1)[1]
        return os.path.join(RECEIPTS, key.replace("/", "_"))

    def _send(self, status, body, ctype="application/json"):
        data = body if isinstance(body, bytes) else body.encode()
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    # -- Lambda bridge ---------------------------------------------------- #
    def _lambda(self, method):
        parts = urlsplit(self.path)
        path = parts.path[len("/api"):] or "/"
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length).decode() if length else None
        for pattern, route in ROUTE_PATTERNS:
            m = pattern.match(path)
            if m and route.startswith(method + " "):
                event = {
                    "routeKey": route,
                    "rawPath": path,
                    "headers": {k.lower(): v for k, v in self.headers.items()},
                    "queryStringParameters": dict(parse_qsl(parts.query)) or None,
                    "pathParameters": m.groupdict() or None,
                    "body": body,
                    "isBase64Encoded": False,
                }
                res = app.lambda_handler(event, None)
                return self._send(res["statusCode"], res["body"])
        self._send(404, json.dumps({"error": f"No route for {method} {path}"}))

    def do_GET(self):
        if self.path.startswith("/api/"):
            return self._lambda("GET")
        if self.path == "/config.js":
            return self._send(200, 'window.APP_CONFIG = { API_URL: "/api" };', "application/javascript")
        if self.path.startswith("/local-receipts/"):
            p = self._receipt_path()
            if not os.path.exists(p):
                return self._send(404, b"not found", "text/plain")
            ext = p.rsplit(".", 1)[-1]
            ctype = {"png": "image/png", "jpg": "image/jpeg", "webp": "image/webp", "pdf": "application/pdf"}.get(ext)
            with open(p, "rb") as f:
                return self._send(200, f.read(), ctype or "application/octet-stream")
        return super().do_GET()

    def do_POST(self):
        self._lambda("POST")

    def do_DELETE(self):
        self._lambda("DELETE")

    def do_PUT(self):
        if self.path.startswith("/local-receipts/"):
            os.makedirs(RECEIPTS, exist_ok=True)
            length = int(self.headers.get("Content-Length") or 0)
            with open(self._receipt_path(), "wb") as f:
                f.write(self.rfile.read(length))
            return self._send(200, b"", "text/plain")
        self._lambda("PUT")


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8000"))
    print(f"CloudLedger (local mode) -> http://localhost:{port}   data: {DATA_DIR}")
    ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()
