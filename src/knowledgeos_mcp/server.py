"""Transports for the KnowledgeOS MCP adapter: loopback Streamable HTTP and stdio.

    knowledgeos-mcp --port 8791          # HTTP at http://127.0.0.1:8791/mcp, Bearer per agent
    KNOWLEDGEOS_TOKEN=kos_… knowledgeos-mcp --stdio

The first version runs on this computer only: the HTTP transport binds to a
loopback address and rejects browser origins other than localhost.
"""
from __future__ import annotations

import argparse
import hashlib
import ipaddress
import json
import os
import shutil
import sys
import threading
import time
import uuid
from collections import OrderedDict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

from . import __version__
from .client import KnowledgeOSError
from .protocol import INVALID_REQUEST, PARSE_ERROR, McpServer

DEFAULT_PORT = 8791
MAX_BODY = 8 * 1_048_576
LOCAL_ORIGINS = {"localhost", "127.0.0.1", "::1", "[::1]"}


def stdio_command():
    """How a host should launch this adapter over stdio on this computer."""
    script = os.path.abspath(sys.argv[0]) if sys.argv and sys.argv[0] else ""
    if os.path.basename(script) == "knowledgeos-mcp" and os.path.exists(script):
        return {"command": script, "args": ["--stdio"]}
    found = shutil.which("knowledgeos-mcp")
    if found:
        return {"command": os.path.abspath(found), "args": ["--stdio"]}
    return {"command": sys.executable, "args": ["-m", "knowledgeos_mcp", "--stdio"]}


class Sessions:
    """Optional bookkeeping for hosts that negotiate sessions: remembers who the client is."""

    def __init__(self, limit=1000):
        self.items, self.limit, self.lock = OrderedDict(), limit, threading.Lock()

    def open(self, client_name):
        identifier = uuid.uuid4().hex
        with self.lock:
            self.items[identifier] = client_name
            while len(self.items) > self.limit:
                self.items.popitem(last=False)
        return identifier

    def client(self, identifier):
        with self.lock:
            return self.items.get(identifier or "")

    def close(self, identifier):
        with self.lock:
            self.items.pop(identifier or "", None)


class TokenCache:
    """Short-lived memory of credentials KnowledgeOS accepted, so each request need not re-check."""

    def __init__(self, ttl=30):
        self.ttl, self.items, self.lock = ttl, {}, threading.Lock()

    def valid(self, mcp, token, client_name):
        key = hashlib.sha256(token.encode()).hexdigest()
        with self.lock:
            if self.items.get(key, 0) > time.monotonic():
                return True
        try:
            mcp.client(token, client_name).get("/v1/session")
        except KnowledgeOSError as exc:
            if exc.status == 401:
                return False
            raise
        with self.lock:
            self.items[key] = time.monotonic() + self.ttl
        return True


def client_label(info):
    if not isinstance(info, dict) or not info.get("name"):
        return None
    return f"{str(info['name'])[:60]}/{str(info.get('version') or '?')[:30]} (mcp)"


class McpHttpServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address, mcp: McpServer):
        self.mcp, self.sessions, self.tokens = mcp, Sessions(), TokenCache()
        super().__init__(address, McpHandler)

    @property
    def url(self):
        host, port = self.server_address[:2]
        return f"http://{'127.0.0.1' if host in ('127.0.0.1', '0.0.0.0') else host}:{port}/mcp"


class McpHandler(BaseHTTPRequestHandler):
    server: McpHttpServer
    protocol_version = "HTTP/1.1"

    def log_message(self, format, *args):
        return

    def _send(self, status, body=None, headers=None):
        payload = b"" if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        if body is not None:
            self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        if payload:
            self.wfile.write(payload)

    def _origin_allowed(self):
        origin = self.headers.get("Origin")
        if not origin:
            return True
        return (urlsplit(origin).hostname or "") in LOCAL_ORIGINS

    def do_GET(self):
        path = urlsplit(self.path).path
        if path in ("/", "/info"):
            self._send(200, {"name": "knowledgeos-mcp", "version": __version__, "http_url": self.server.url,
                             "stdio": stdio_command(), "api_base_url": self.server.mcp.api_base_url,
                             "transports": ["streamable-http", "stdio"], "auth": "Bearer（Studio「Agent 管理」签发的凭证）"})
        elif path == "/health":
            self._send(200, {"status": "ok"})
        elif path == "/mcp":
            # No server-initiated stream: every response comes back on its POST.
            self._send(405, {"error": "use POST"}, {"Allow": "POST, DELETE"})
        else:
            self._send(404, {"error": "not found"})

    def do_DELETE(self):
        if urlsplit(self.path).path != "/mcp":
            self._send(404, {"error": "not found"})
            return
        self.server.sessions.close(self.headers.get("Mcp-Session-Id"))
        self._send(204)

    def do_POST(self):
        if urlsplit(self.path).path != "/mcp":
            self._send(404, {"error": "not found"})
            return
        if not self._origin_allowed():
            self._send(403, {"error": "origin not allowed"})
            return
        parts = (self.headers.get("Authorization") or "").split(None, 1)
        if len(parts) != 2 or parts[0].lower() != "bearer" or not parts[1].strip():
            self._send(401, {"error": "Bearer token required：在 Studio「Agent 管理」中为该 Agent 签发凭证"},
                       {"WWW-Authenticate": 'Bearer realm="KnowledgeOS"'})
            return
        token = parts[1].strip()
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = -1
        if length <= 0 or length > MAX_BODY:
            self._send(413 if length > MAX_BODY else 400, {"error": "JSON body required (≤ 8 MiB)"})
            return
        try:
            payload = json.loads(self.rfile.read(length))
        except ValueError:
            self._send(400, {"jsonrpc": "2.0", "id": None, "error": {"code": PARSE_ERROR, "message": "parse error"}})
            return
        messages = payload if isinstance(payload, list) else [payload]
        if not messages:
            self._send(400, {"jsonrpc": "2.0", "id": None, "error": {"code": INVALID_REQUEST, "message": "empty batch"}})
            return
        session = self.headers.get("Mcp-Session-Id")
        initialize = next((item for item in messages if isinstance(item, dict) and item.get("method") == "initialize"), None)
        client_name = client_label((initialize or {}).get("params", {}).get("clientInfo")) if initialize else self.server.sessions.client(session)
        try:
            if not self.server.tokens.valid(self.server.mcp, token, client_name):
                self._send(401, {"error": "凭证无效、已过期或已吊销"}, {"WWW-Authenticate": 'Bearer realm="KnowledgeOS", error="invalid_token"'})
                return
        except KnowledgeOSError as exc:
            self._send(502, {"error": str(exc)})
            return
        headers = {}
        if initialize:
            headers["Mcp-Session-Id"] = self.server.sessions.open(client_name)
        responses = [response for response in (self.server.mcp.handle(item, token=token, client_name=client_name) for item in messages) if response]
        if not responses:
            self._send(202, None, headers)
        else:
            self._send(200, responses if isinstance(payload, list) else responses[0], headers)


def serve_stdio(mcp: McpServer, token, stdin=None, stdout=None):
    stdin, stdout = stdin or sys.stdin, stdout or sys.stdout
    client_name = None
    for line in stdin:
        line = line.strip()
        if not line:
            continue
        try:
            payload = json.loads(line)
        except ValueError:
            stdout.write(json.dumps({"jsonrpc": "2.0", "id": None, "error": {"code": PARSE_ERROR, "message": "parse error"}}) + "\n")
            stdout.flush()
            continue
        messages = payload if isinstance(payload, list) else [payload]
        for item in messages:
            if isinstance(item, dict) and item.get("method") == "initialize":
                client_name = client_label((item.get("params") or {}).get("clientInfo")) or client_name
        responses = [response for response in (mcp.handle(item, token=token, client_name=client_name) for item in messages) if response]
        if responses:
            stdout.write(json.dumps(responses if isinstance(payload, list) else responses[0], ensure_ascii=False) + "\n")
            stdout.flush()


def is_loopback(host):
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def main(argv=None):
    parser = argparse.ArgumentParser(prog="knowledgeos-mcp", description="KnowledgeOS MCP adapter")
    parser.add_argument("--stdio", action="store_true", help="serve one host over stdin/stdout (token from KNOWLEDGEOS_TOKEN)")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=int(os.environ.get("KNOWLEDGEOS_MCP_PORT", DEFAULT_PORT)))
    parser.add_argument("--api", default=os.environ.get("KNOWLEDGEOS_API_BASE_URL", "http://127.0.0.1:8787"))
    args = parser.parse_args(argv)
    mcp = McpServer(args.api)
    if args.stdio:
        token = os.environ.get("KNOWLEDGEOS_TOKEN", "").strip()
        if not token:
            print("KNOWLEDGEOS_TOKEN is required for --stdio (issue one in Studio「Agent 管理」)", file=sys.stderr)
            return 2
        serve_stdio(mcp, token)
        return 0
    if not is_loopback(args.host):
        print("This version serves MCP on this computer only; use a loopback address such as 127.0.0.1.", file=sys.stderr)
        return 2
    server = McpHttpServer((args.host, args.port), mcp)
    print(f"KnowledgeOS MCP: {server.url}  (stdio: {' '.join([stdio_command()['command'], *stdio_command()['args']])})", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
