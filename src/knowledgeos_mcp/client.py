"""Minimal client for the public KnowledgeOS HTTP API."""
from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request


class KnowledgeOSError(Exception):
    def __init__(self, message, status=502):
        super().__init__(message)
        self.status = status


class KnowledgeOSClient:
    def __init__(self, base_url, token, client_name=None, timeout=60):
        self.base_url, self.token, self.client_name, self.timeout = base_url.rstrip("/"), token, client_name, timeout

    def get(self, path, **params):
        query = urllib.parse.urlencode({key: value for key, value in params.items() if value is not None and value != ""})
        return self._call("GET", path + (f"?{query}" if query else ""))

    def post(self, path, body):
        return self._call("POST", path, body)

    def _call(self, method, path, body=None):
        data = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
        headers = {"Authorization": f"Bearer {self.token}", "Accept": "application/json"}
        if data is not None:
            headers["Content-Type"] = "application/json"
        if self.client_name:
            headers["X-KnowledgeOS-Client"] = self.client_name[:120]
        request = urllib.request.Request(self.base_url + path, data=data, method=method, headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            try:
                message = json.loads(exc.read().decode("utf-8")).get("error") or f"HTTP {exc.code}"
            except ValueError:
                message = f"HTTP {exc.code}"
            raise KnowledgeOSError(message, exc.code) from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise KnowledgeOSError(f"无法连接 KnowledgeOS 后端（{self.base_url}）", 502) from exc
