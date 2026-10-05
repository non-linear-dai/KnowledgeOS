"""Authenticated HTTP JSON API for the Python application service."""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import sqlite3
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

from .core import AuthenticationError, AuthorizationError, ConflictError, NotFoundError, ValidationError
from .service import Service


ROLE_PERMISSIONS = {"reader": ["read"], "agent": ["read", "agent", "extract", "propose"],
                    "reviewer": ["read", "review"], "publisher": ["read", "publish"],
                    "template_author": ["read", "template_edit"],
                    "template_reviewer": ["read", "template_review"],
                    "template_publisher": ["read", "template_publish"],
                    "admin": ["read", "agent", "extract", "propose", "review", "publish",
                              "template_edit", "template_review", "template_publish"]}


class AccessControl:
    def __init__(self, entries=None, enabled=True):
        self.entries, self.enabled = entries or {}, enabled

    @classmethod
    def from_env(cls, environ=None):
        environ = environ or os.environ
        mode = environ.get("KNOWLEDGEOS_AUTH_MODE", "required")
        if mode == "disabled":
            return cls(enabled=False)
        if mode != "required":
            raise ValidationError("KNOWLEDGEOS_AUTH_MODE must be required or disabled")
        raw = environ.get("KNOWLEDGEOS_AUTH_TOKENS")
        if not raw:
            raise ValidationError("KNOWLEDGEOS_AUTH_TOKENS is required")
        try:
            definitions = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValidationError(f"invalid KNOWLEDGEOS_AUTH_TOKENS JSON: {exc}") from exc
        if not isinstance(definitions, dict) or not definitions:
            raise ValidationError("KNOWLEDGEOS_AUTH_TOKENS must be a nonempty JSON object")
        entries = {}
        for token, definition in definitions.items():
            if isinstance(definition, str):
                definition = {"principal": definition, "roles": ["reader"]}
            if not isinstance(definition, dict) or not str(definition.get("principal") or "").strip():
                raise ValidationError("authentication principal is required")
            roles = definition.get("roles") or []
            if not isinstance(roles, list) or any(role not in ROLE_PERMISSIONS for role in roles):
                raise ValidationError("unknown authentication roles")
            entries[hashlib.sha256(token.encode()).hexdigest()] = {"id": definition["principal"], "roles": roles}
        return cls(entries)

    def authenticate(self, header):
        if not self.enabled:
            return {"id": "local:anonymous", "roles": ["admin"]}
        parts = (header or "").split(None, 1)
        if len(parts) != 2 or parts[0].lower() != "bearer" or not parts[1]:
            raise AuthenticationError("Bearer token required")
        hashed = hashlib.sha256(parts[1].encode()).hexdigest()
        for key, principal in self.entries.items():
            if hmac.compare_digest(key, hashed):
                return principal
        raise AuthenticationError("invalid Bearer token")

    @staticmethod
    def authorize(principal, permission):
        if permission not in {p for role in principal["roles"] for p in ROLE_PERMISSIONS[role]}:
            raise AuthorizationError(f"principal {principal['id']} is not allowed to {permission}")


PERMISSIONS = {
    "/v1/agent/request": "agent", "/v1/agent/respond": "agent", "/v1/agent/invoke": "agent",
    "/v1/extraction/request": "extract", "/v1/extraction/candidates": "extract",
    "/v1/calculate": "agent", "/v1/calculate/entity": "agent", "/v1/fx/convert": "agent",
    "/v1/models/preview": "propose", "/v1/business/preview": "propose", "/v1/business/impact": "propose",
    "/v1/templates/propose": "template_edit", "/v1/review": "review",
}
POST_ONLY = {"/v1/agent/request", "/v1/agent/respond", "/v1/agent/invoke", "/v1/extraction/request",
             "/v1/extraction/candidates", "/v1/changesets/review", "/v1/changesets/apply", "/v1/changesets/publish", "/v1/query",
             "/v1/calculate", "/v1/calculate/entity", "/v1/fx/convert", "/v1/models/preview", "/v1/business/preview", "/v1/business/impact", "/v1/business/evaluate", "/v1/propose",
             "/v1/templates/preview", "/v1/templates/calculate", "/v1/templates/propose"}


class KnowledgeServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address, service: Service, auth: AccessControl):
        self.service, self.auth = service, auth
        super().__init__(address, KnowledgeHandler)


class KnowledgeHandler(BaseHTTPRequestHandler):
    server: KnowledgeServer

    def do_GET(self):
        self._handle()

    def do_POST(self):
        self._handle()

    def _send(self, status, data, principal=None, extra=None):
        encoded = json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        if principal:
            self.send_header("X-KnowledgeOS-Principal", principal["id"])
        for key, value in (extra or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(encoded)

    def _handle(self):
        path = urlsplit(self.path).path
        params = {key: value[-1] for key, value in parse_qs(urlsplit(self.path).query).items()}
        service = self.server.service
        principal = None
        try:
            if path == "/health":
                self._send(200, {"status": "ok", "nodes": service.db.first("SELECT count(*) AS count FROM node")["count"]})
                return
            principal = self.server.auth.authenticate(self.headers.get("Authorization"))
            self.server.auth.authorize(principal, PERMISSIONS.get(path, "read"))
            if path in ("/v1/templates/preview", "/v1/templates/calculate"):
                permissions = {permission for role in principal["roles"] for permission in ROLE_PERMISSIONS[role]}
                if not permissions.intersection({"template_edit", "template_review", "template_publish"}):
                    raise AuthorizationError("template preview requires expert template permission")
            if path in POST_ONLY and self.command != "POST":
                self._send(405, {"error": "POST required"}, principal)
                return
            body = self._body() if self.command == "POST" else {}
            with service.db.lock:
                service.refresh_registry()
                self._authorize_governed_mutation(path, body, principal)
                result = self._dispatch(path, params, body, principal)
            status = 201 if path in ("/v1/propose", "/v1/templates/propose") else 200
            self._send(status, result, principal)
        except AuthenticationError as exc:
            self._send(401, {"error": str(exc)}, extra={"WWW-Authenticate": 'Bearer realm="KnowledgeOS"'})
        except AuthorizationError as exc:
            self._send(403, {"error": str(exc)}, principal)
        except NotFoundError as exc:
            self._send(404, {"error": str(exc)}, principal)
        except ConflictError as exc:
            self._send(409, {"error": str(exc), "code": "CONFLICT"}, principal)
        except (ValidationError, KeyError, ValueError, TypeError) as exc:
            self._send(422, {"error": str(exc)}, principal)
        except sqlite3.OperationalError as exc:
            if "locked" in str(exc).lower():
                self._send(503, {"error": "database busy; retry the same idempotent request", "code": "BUSY"}, principal, {"Retry-After": "1"})
            else:
                print(f"KnowledgeOS API error: {exc}", file=sys.stderr)
                self._send(500, {"error": "internal server error"}, principal)
        except Exception as exc:
            print(f"KnowledgeOS API error: {type(exc).__name__}: {exc}", file=sys.stderr)
            self._send(500, {"error": "internal server error"}, principal)

    def _body(self):
        raw_length = self.headers.get("Content-Length")
        if raw_length is None:
            raise ValidationError("JSON body required")
        length = int(raw_length)
        if length > 1_048_576:
            raise ValidationError("JSON body exceeds 1 MiB")
        if length == 0:
            raise ValidationError("JSON body required")
        try:
            body = json.loads(self.rfile.read(length))
        except json.JSONDecodeError as exc:
            raise ValidationError(f"invalid JSON body: {exc}") from exc
        if not isinstance(body, dict):
            raise ValidationError("JSON body must be an object")
        return body

    @staticmethod
    def _required(params, name):
        value = str(params.get(name) or "").strip()
        if not value:
            raise ValidationError(f"missing parameter: {name}")
        return value

    def _authorize_governed_mutation(self, path, body, principal):
        if path == "/v1/agent/invoke" and body.get("operation") == "propose":
            arguments = body.get("arguments") or {}
            source = str(arguments.get("target_source") or "")
            if any(part.strip().startswith(("knowledge/templates/", "knowledge/operations/", "knowledge/decisions/")) for part in source.split(",")):
                self.server.auth.authorize(principal, "template_edit")
            return
        if path not in ("/v1/propose", "/v1/changesets/review", "/v1/changesets/apply", "/v1/changesets/publish"):
            return
        if path == "/v1/propose":
            source = str(body.get("target_source") or "")
            is_template = any(part.strip().startswith(("knowledge/templates/", "knowledge/operations/", "knowledge/decisions/")) for part in source.split(","))
            if is_template and (not isinstance(body.get("patch"), dict) or body["patch"].get("op") != "template_package"):
                raise ValidationError("expert templates require the governed template package workflow")
        else:
            row = self.server.service.db.first("SELECT target_source FROM changeset WHERE id=?", (body.get("id"),))
            if not row:
                fallback = {"/v1/changesets/review": "review", "/v1/changesets/apply": "publish", "/v1/changesets/publish": "publish"}
                self.server.auth.authorize(principal, fallback[path])
                raise NotFoundError("changeset not found")
            is_template = any(part.strip().startswith(("knowledge/templates/", "knowledge/operations/", "knowledge/decisions/")) for part in row["target_source"].split(","))
        ordinary = {"/v1/propose": "propose", "/v1/changesets/review": "review",
                    "/v1/changesets/apply": "publish", "/v1/changesets/publish": "publish"}
        specialized = {"/v1/propose": "template_edit", "/v1/changesets/review": "template_review",
                       "/v1/changesets/apply": "template_publish", "/v1/changesets/publish": "template_publish"}
        self.server.auth.authorize(principal, specialized[path] if is_template else ordinary[path])

    def _dispatch(self, path, p, body, principal):
        s = self.server.service
        if path == "/v1/session":
            return {"principal": principal["id"], "roles": principal["roles"],
                    "permissions": list(dict.fromkeys(permission for role in principal["roles"] for permission in ROLE_PERMISSIONS[role]))}
        if path == "/v1/resolve":
            return s.resolve(self._required(p, "query"), p.get("scope"))
        if path == "/v1/control":
            return s.control_plane()
        if path == "/v1/studio":
            return s.studio()
        if path == "/v1/templates":
            return s.template_catalog()
        if path == "/v1/templates/get":
            return s.template_get(self._required(p, "id"))
        if path == "/v1/models/history":
            return s.model_history(self._required(p, "id"))
        if path == "/v1/changesets":
            return s.changesets(p.get("status"), p.get("limit", 100))
        if path == "/v1/get":
            return s.get(self._required(p, "id"), p.get("history") == "true")
        if path == "/v1/search":
            return s.search(self._required(p, "query"), {"type": p["type"]} if p.get("type") else {}, p.get("mode", "hybrid"))
        if path == "/v1/neighbors":
            return s.neighbors(self._required(p, "id"), p.get("relation_types"), p.get("depth", 1), p.get("as_of"), p.get("recorded_as_of"))
        if path == "/v1/history":
            return s.history(self._required(p, "id"), p.get("predicate"))
        if path == "/v1/explain":
            return s.explain(self._required(p, "target"), p.get("item"))
        if path == "/v1/context":
            return s.context(self._required(p, "id"), domain=self._required(p, "domain"), as_of=p.get("as_of"), recorded_as_of=p.get("recorded_as_of"))
        if path == "/v1/query":
            return s.query(body["template"], body.get("params", {}))
        if path == "/v1/calculate":
            return s.calculate(body["model_id"], body["inputs"], body.get("scenario", "default"))
        if path == "/v1/calculate/entity":
            return s.calculate_for_entity(body["model_id"], body["node_id"], as_of=body.get("as_of"),
                                          scenario=body.get("scenario", "default"))
        if path == "/v1/fx/convert":
            return s.convert_currency(body["amount"], body["quote_node_id"], as_of=body["as_of"],
                                      rate_type=body.get("rate_type", "mid"))
        if path == "/v1/models/preview":
            return s.preview_model(body["model"], body["inputs"])
        if path == "/v1/templates/preview":
            return s.template_preview(body["template"], body["case"])
        if path == "/v1/templates/calculate":
            return s.template_calculate(body["template"], body["case"], body["scenario"])
        if path == "/v1/templates/propose":
            return s.template_propose(actor=principal["id"], template=body["template"],
                                      reason=body["reason"], idempotency_key=body.get("idempotency_key"))
        if path == "/v1/business/preview":
            return s.preview_business(body["definition"], body["kind"], body["facts"])
        if path == "/v1/business/impact":
            return s.impact_business(body["definition"], body["kind"], body.get("subject_id"),
                                     body.get("candidate_ids"), body.get("as_of"))
        if path == "/v1/business/evaluate":
            return s.evaluate_business_definition(body["kind"], body["id"], body["subject_id"],
                                                  body.get("candidate_ids"), body.get("as_of"))
        if path == "/v1/propose":
            return s.propose(actor=principal["id"], target_source=body["target_source"], patch=body["patch"],
                             reason=body["reason"], risk=body.get("risk", "normal"), title=body.get("title"),
                             operations=body.get("operations", []), base_revision=body.get("base_revision"),
                             idempotency_key=body.get("idempotency_key"))
        if path == "/v1/review":
            return s.review(p.get("priority"), p.get("limit", 100))
        if path == "/v1/changesets/review":
            return s.review_changeset(id=body["id"], reviewer=principal["id"], decision=body["decision"], note=body.get("note"))
        if path == "/v1/changesets/apply":
            return s.apply_changeset(id=body["id"], publisher=principal["id"])
        if path == "/v1/changesets/publish":
            return s.publish_changeset(id=body["id"], publisher=principal["id"], source_revision=body["source_revision"])
        if path == "/v1/agent/capabilities":
            return s.agent_capabilities(p.get("domain"))
        if path == "/v1/agent/skill":
            return s.agent_skill(self._required(p, "id"))
        if path == "/v1/agent/request":
            return s.agent_request(question=body["question"], domain=body["domain"], target=body.get("target"),
                                   query=body.get("query"), skill=body.get("skill"), as_of=body.get("as_of"),
                                   max_items=body.get("max_items", 25))
        if path == "/v1/agent/respond":
            return s.agent_respond(request=body["request"], model_output=body["model_output"], tool_results=body.get("tool_results", []))
        if path == "/v1/agent/invoke":
            arguments = body.get("arguments", {})
            if body["operation"] == "propose":
                arguments = {**arguments, "actor": principal["id"]}
            return s.agent_invoke(domain=body["domain"], operation=body["operation"], arguments=arguments)
        if path == "/v1/extraction/request":
            source = body["source"]
            if not source.get("content") and not source.get("segments"):
                raise ValidationError("API extraction sources must include materialized content or a source envelope")
            return s.extraction_request(source=source, profile=body.get("profile", "default"))
        if path == "/v1/extraction/candidates":
            return s.extraction_candidates(request=body["request"], model_output=body["model_output"], profile=body.get("profile", "default"))
        raise NotFoundError(f"route not found: {path}")

    def log_message(self, format, *args):
        return
