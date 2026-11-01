"""Assistant gateway: orchestrate context → model → validation → KnowledgeOS dry run.

    POST /v1/assistant/suggest   {task, input}   -> suggestion (never written anywhere)
    GET  /v1/assistant/status                    -> provider configuration (no secrets)
    GET  /health

The caller's ``Authorization: Bearer`` token is forwarded to KnowledgeOS for
every call, so the kernel authorizes context reads and dry runs as that user.
The gateway keeps no state besides a per-token rate limiter and logs only task,
duration and token counts — never prompts, drafts or tokens.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import threading
import time
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import jsonschema

from . import __version__
from .config import ConfigurationError, GatewayConfig
from .providers import ProviderError, provider_for

TASKS = ("logic.draft", "ontology.model", "extraction.card", "template.interview")


class GatewayError(Exception):
    def __init__(self, message, status=422, code=None, detail=None):
        super().__init__(message)
        self.status, self.code, self.detail = status, code, detail


def digest(value):
    """Identical to KnowledgeOS core.digest: sha256 of canonical compact JSON."""
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode()).hexdigest()


class KernelClient:
    def __init__(self, base_url, token, timeout=60):
        self.base_url, self.token, self.timeout = base_url.rstrip("/"), token, timeout

    def call(self, method, path, body=None):
        data = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
        request = urllib.request.Request(self.base_url + path, data=data, method=method,
                                         headers={"Authorization": self.token, "Accept": "application/json",
                                                  **({"Content-Type": "application/json"} if data else {})})
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                return json.loads(response.read().decode("utf-8"))["data"]
        except urllib.error.HTTPError as exc:
            try:
                message = json.loads(exc.read().decode("utf-8")).get("error") or f"HTTP {exc.code}"
            except ValueError:
                message = f"HTTP {exc.code}"
            raise GatewayError(f"KnowledgeOS：{message}", exc.code if exc.code in (401, 403, 404, 409, 422) else 502, "KERNEL") from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise GatewayError("无法连接 KnowledgeOS 后端", 502, "KERNEL_UNREACHABLE") from exc


def extract_json(text):
    """The first top-level JSON object in a model reply (tolerates code fences and prose)."""
    cleaned = re.sub(r"^\s*```(?:json)?\s*|\s*```\s*$", "", text.strip())
    try:
        value = json.loads(cleaned)
        if isinstance(value, dict):
            return value
    except ValueError:
        pass
    start = cleaned.find("{")
    while start != -1:
        depth, in_string, escape = 0, False, False
        for index in range(start, len(cleaned)):
            char = cleaned[index]
            if in_string:
                if escape:
                    escape = False
                elif char == "\\":
                    escape = True
                elif char == '"':
                    in_string = False
                continue
            if char == '"':
                in_string = True
            elif char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    try:
                        value = json.loads(cleaned[start:index + 1])
                        if isinstance(value, dict):
                            return value
                    except ValueError:
                        break
                    break
        start = cleaned.find("{", start + 1)
    raise ValueError("模型没有返回 JSON 对象")


def check_payload(task, packet, output):
    """Map a model output to the draft KnowledgeOS dry-runs."""
    if task == "logic.draft":
        return {key: output.get(key) for key in ("kind", "definition", "test_cases", "evidence") if key in output}, {"description": packet["input"]["description"]}
    if task == "ontology.model":
        return {"ontology_request": packet["ontology_request"], "model_output": output.get("model_output")}, None
    if task == "extraction.card":
        return {"card": output.get("card")}, {"concept": packet["input"]["concept"]}
    return {"template": output.get("template")}, None


def item_count(task, output):
    if task == "logic.draft":
        definition = output.get("definition") or {}
        return len(definition.get("rules") or definition.get("checks") or []) + len(output.get("test_cases") or [])
    if task == "ontology.model":
        return len((output.get("model_output") or {}).get("operations") or [])
    return len(output.get("changes") or [])


def user_message(task, packet):
    lines = ["请根据系统说明完成以下编写任务。"]
    if task == "ontology.model":
        lines.append(f"request_id：{packet['ontology_request']['request_id']}")
    lines += ["任务输入：", "```json", json.dumps(packet["input"], ensure_ascii=False, indent=1), "```", "只输出一个符合输出格式的 JSON 对象。"]
    return "\n".join(lines)


def repair_message(issues):
    listed = "\n".join(f"- [{item.get('code', 'ERROR')}] {item.get('path') or ''} {item['message']}" for item in issues[:12])
    return ("上一份 JSON 未通过校验，请逐条修正后重新输出完整的 JSON 对象（不要解释）：\n" + listed)


class Assistant:
    def __init__(self, config, provider=None, kernel_factory=None):
        self.config = config
        self.provider = provider or provider_for(config)
        self.kernel_factory = kernel_factory or (lambda token: KernelClient(config.kernel_url, token))
        self._calls, self._lock = {}, threading.Lock()

    def status(self):
        return {"version": __version__, "tasks": list(TASKS), **self.config.describe()}

    def _limit(self, token):
        key = hashlib.sha256(token.encode()).hexdigest()
        now = time.monotonic()
        with self._lock:
            window = [stamp for stamp in self._calls.get(key, []) if now - stamp < 60]
            if len(window) >= self.config.requests_per_minute:
                raise GatewayError("请求过于频繁，请稍后再试", 429, "RATE_LIMITED")
            window.append(now)
            self._calls[key] = window

    def suggest(self, token, task, task_input):
        if not self.provider:
            raise GatewayError("尚未配置模型服务：请设置 KNOWLEDGEOS_ASSISTANT_PROVIDER、MODEL 与 API_KEY", 503, "ASSISTANT_NOT_CONFIGURED")
        if task not in TASKS:
            raise GatewayError(f"不支持的任务：{task}", 422, "UNKNOWN_TASK")
        if not isinstance(task_input, dict):
            raise GatewayError("input 必须是对象", 422)
        self._limit(token)
        started = time.monotonic()
        kernel = self.kernel_factory(token)
        if task == "extraction.card" and not self.config.send_document_samples:
            task_input = {**task_input, "include_samples": False}
        packet = kernel.call("POST", "/v1/authoring/context", {"task": task, "input": task_input, "include": ["instructions"]})
        system = packet["instructions"]["markdown"]
        messages = [{"role": "user", "content": user_message(task, packet)}]
        usage, rounds, output, check, attempts = {"input_tokens": 0, "output_tokens": 0}, 0, None, None, []
        for attempt in range(self.config.max_repairs + 1):
            try:
                text, used = self.provider.complete(system, messages, packet)
            except ProviderError as exc:
                raise GatewayError(str(exc), exc.status, "PROVIDER") from exc
            for key in usage:
                usage[key] += used.get(key) or 0
            try:
                output = extract_json(text)
                issues = [{"code": "OUTPUT_SCHEMA", "path": "/".join(str(part) for part in error.absolute_path), "message": error.message}
                          for error in sorted(jsonschema.Draft202012Validator(packet["output_schema"]).iter_errors(output), key=lambda error: list(error.absolute_path))][:12]
            except ValueError as exc:
                output, issues = None, [{"code": "NOT_JSON", "message": str(exc)}]
            if not issues:
                draft, check_input = check_payload(task, packet, output)
                check = kernel.call("POST", "/v1/authoring/check", {"task": task, "draft": draft, **({"input": check_input} if check_input else {})})
                issues = [item for item in check["issues"] if item["severity"] == "error"]
            attempts.append({"attempt": attempt + 1, "errors": len(issues)})
            if not issues:
                break
            if attempt == self.config.max_repairs:
                break
            rounds += 1
            messages += [{"role": "assistant", "content": text}, {"role": "user", "content": repair_message(issues)}]
        if output is None or check is None:
            raise GatewayError("模型多次返回无法解析或不符合格式的结果，请调整描述后重试", 502, "INVALID_MODEL_OUTPUT",
                               detail={"attempts": attempts})
        suggestion_id = str(uuid.uuid4())
        created = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
        output_hash = check["draft_hash"] or digest(check_payload(task, packet, output)[0])
        assistance = {"suggestion_id": suggestion_id, "task": task, "provider": self.provider.name, "model": self.config.model or self.provider.name,
                      "renderer_version": packet["instructions"]["renderer_version"], "instructions_hash": packet["instructions"]["content_hash"],
                      "context_hash": packet["context_hash"], "output_hash": output_hash, "created_at": created,
                      "items_total": item_count(task, output), "repair_rounds": rounds}
        print(json.dumps({"event": "suggestion", "task": task, "ms": int((time.monotonic() - started) * 1000), "rounds": rounds,
                          "valid": check["valid"], **usage}), file=sys.stderr, flush=True)
        result = {"suggestion_id": suggestion_id, "task": task, "created_at": created, "demo": self.provider.name == "demo",
                  "output": output, "check": check, "assistance": assistance, "usage": usage, "attempts": attempts,
                  "context": {"request_id": packet["request_id"], "registry_fingerprint": packet["registry_fingerprint"],
                              "instructions_hash": packet["instructions"]["content_hash"]},
                  "write_performed": False}
        if task == "ontology.model":
            result["ontology_request"] = packet["ontology_request"]
        return result


class AssistantServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address, assistant):
        self.assistant = assistant
        super().__init__(address, AssistantHandler)


class AssistantHandler(BaseHTTPRequestHandler):
    server: AssistantServer

    def _send(self, status, body):
        data = json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path == "/health":
            self._send(200, {"status": "ok", "version": __version__})
        elif path == "/v1/assistant/status":
            self._send(200, {"data": self.server.assistant.status()})
        else:
            self._send(404, {"error": "route not found"})

    def do_POST(self):
        path = self.path.split("?", 1)[0]
        if path != "/v1/assistant/suggest":
            self._send(404, {"error": "route not found"})
            return
        token = self.headers.get("Authorization") or ""
        if not re.fullmatch(r"Bearer\s+\S+", token, re.IGNORECASE):
            self._send(401, {"error": "请使用个人访问凭证登录", "code": "AUTHENTICATION_REQUIRED"})
            return
        try:
            length = int(self.headers.get("Content-Length") or 0)
            if not 0 < length <= 1_048_576:
                raise GatewayError("请求体须为 1 MiB 以内的 JSON", 413 if length else 422)
            body = json.loads(self.rfile.read(length))
            if not isinstance(body, dict):
                raise GatewayError("请求体必须是 JSON 对象")
            result = self.server.assistant.suggest(token, body.get("task"), body.get("input") or {})
            self._send(200, {"data": result})
        except GatewayError as exc:
            self._send(exc.status, {"error": str(exc), **({"code": exc.code} if exc.code else {}), **({"detail": exc.detail} if exc.detail else {})})
        except ValueError:
            self._send(422, {"error": "无效的 JSON 请求"})
        except Exception as exc:  # noqa: BLE001 - never leak internals
            print(f"assistant error: {type(exc).__name__}", file=sys.stderr, flush=True)
            self._send(500, {"error": "助手网关内部错误"})

    def log_message(self, format, *args):
        return


def main(argv=None):
    parser = argparse.ArgumentParser(prog="knowledgeos-assistant", description="Optional KnowledgeOS authoring assistant gateway")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8790)
    args = parser.parse_args(argv)
    try:
        config = GatewayConfig.from_env()
    except ConfigurationError as exc:
        print(f"configuration error: {exc}", file=sys.stderr)
        return 2
    server = AssistantServer((args.host, args.port), Assistant(config))
    state = config.describe()
    print(f"KnowledgeOS assistant gateway {__version__} on http://{args.host}:{server.server_address[1]} · "
          f"provider={state['provider'] or 'not configured'} model={state['model'] or '-'} · kernel={config.kernel_url}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
