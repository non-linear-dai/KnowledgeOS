"""Participants: people and agents as equal actors with an accountability chain.

People sign in with a local account; agents are registered by a person (their
owner) and authenticate with scoped credentials issued from Studio. Permissions
come only from roles and credential scopes, never from the participant kind.
The kind matters for two things: final approval policy and review
independence, which is judged on the accountability chain (an agent and its
owner are the same responsible party).
"""
from __future__ import annotations

import hashlib
import hmac
import json
import re
import secrets
import threading
import time
from datetime import datetime, timedelta, timezone

from .core import AuthenticationError, AuthorizationError, ConflictError, NotFoundError, ValidationError, utcnow

ROLE_PERMISSIONS = {"reader": ["read"], "agent": ["read", "agent", "extract", "propose"],
                    "reviewer": ["read", "review"], "publisher": ["read", "publish"],
                    "template_author": ["read", "template_edit"],
                    "template_reviewer": ["read", "template_review"],
                    "template_publisher": ["read", "template_publish"],
                    "admin": ["read", "agent", "extract", "propose", "review", "publish",
                              "template_edit", "template_review", "template_publish", "manage"]}
AGENT_ROLES = ("reader", "agent", "template_author")
APPROVAL_PERMISSIONS = {"review", "publish", "template_review", "template_publish", "manage"}
DEFAULT_POLICY = {"mode": "personal", "self_review_high_risk": False, "human_final_approval": True}
SESSION_HOURS = 12
TOKEN_PREFIX = "kos_"
_SLUG = re.compile(r"^[a-z][a-z0-9_-]{1,39}$")

DDL = """
CREATE TABLE IF NOT EXISTS durable.participant(id TEXT PRIMARY KEY,kind TEXT NOT NULL CHECK(kind IN ('human','agent')),
 label TEXT NOT NULL,owner TEXT,roles_json TEXT NOT NULL,status TEXT NOT NULL DEFAULT 'active',host TEXT,model TEXT,
 description TEXT NOT NULL DEFAULT '',password_hash TEXT,created_by TEXT NOT NULL,created_at TEXT NOT NULL,updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS durable.credential(id TEXT PRIMARY KEY,participant_id TEXT NOT NULL,kind TEXT NOT NULL,
 label TEXT NOT NULL,token_hash TEXT NOT NULL UNIQUE,scopes_json TEXT,created_by TEXT NOT NULL,created_at TEXT NOT NULL,
 expires_at TEXT,last_used_at TEXT,last_client TEXT,revoked_at TEXT);
CREATE INDEX IF NOT EXISTS durable.idx_credential_participant ON credential(participant_id);
CREATE TABLE IF NOT EXISTS durable.participant_setting(key TEXT PRIMARY KEY,value_json TEXT NOT NULL,updated_by TEXT NOT NULL,updated_at TEXT NOT NULL);
INSERT OR IGNORE INTO durable.schema_migration VALUES(7,strftime('%Y-%m-%dT%H:%M:%fZ','now'));
"""


def permissions_for(roles, scopes=None):
    granted = list(dict.fromkeys(permission for role in roles for permission in ROLE_PERMISSIONS[role]))
    return [permission for permission in granted if scopes is None or permission in scopes]


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _hash_password(password: str, salt: bytes | None = None) -> str:
    salt = salt or secrets.token_bytes(16)
    derived = hashlib.scrypt(password.encode(), salt=salt, n=2 ** 14, r=8, p=1, dklen=32)
    return f"scrypt${salt.hex()}${derived.hex()}"


def _check_password(password: str, stored: str | None) -> bool:
    if not stored or not stored.startswith("scrypt$"):
        return False
    _, salt, expected = stored.split("$", 2)
    return hmac.compare_digest(_hash_password(password, bytes.fromhex(salt)), stored) and bool(expected)


def _later(hours: float) -> str:
    return (datetime.now(timezone.utc) + timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


class Participants:
    def __init__(self, db):
        self.db = db
        with db.lock:
            db.connection.executescript(DDL)
        self._failures: dict[str, list[float]] = {}
        self._failure_lock = threading.Lock()

    # ------------------------------------------------------------------ lookup
    def get(self, identifier):
        row = self.db.first("SELECT * FROM participant WHERE id=?", (identifier,))
        return self._public(row) if row else None

    def _public(self, row):
        return {"id": row["id"], "kind": row["kind"], "label": row["label"], "owner": row["owner"],
                "roles": json.loads(row["roles_json"]), "status": row["status"], "host": row["host"], "model": row["model"],
                "description": row["description"], "created_by": row["created_by"], "created_at": row["created_at"],
                "updated_at": row["updated_at"]}

    def kind(self, identifier):
        row = self.db.first("SELECT kind FROM participant WHERE id=?", (identifier,))
        return row["kind"] if row else None

    def accountable(self, identifier):
        """The responsible party: an agent's owner, otherwise the participant itself."""
        row = self.db.first("SELECT kind,owner FROM participant WHERE id=?", (identifier,))
        return row["owner"] if row and row["kind"] == "agent" and row["owner"] else identifier

    def independent(self, reviewer, actor):
        return self.accountable(reviewer) != self.accountable(actor)

    def setup_required(self):
        return not self.db.first("SELECT 1 AS present FROM participant WHERE kind='human' LIMIT 1")

    # ------------------------------------------------------------------ policy
    def policy(self):
        result = dict(DEFAULT_POLICY)
        for row in self.db.execute("SELECT key,value_json FROM participant_setting"):
            if row["key"] in result:
                result[row["key"]] = json.loads(row["value_json"])
        return result

    def set_policy(self, changes, actor):
        if not isinstance(changes, dict) or not changes:
            raise ValidationError("policy changes are required")
        for key, value in changes.items():
            if key not in DEFAULT_POLICY:
                raise ValidationError(f"unknown policy setting: {key}")
            if key == "mode" and value not in ("personal", "organization"):
                raise ValidationError("mode must be personal or organization")
            if key != "mode" and not isinstance(value, bool):
                raise ValidationError(f"{key} must be true or false")
        merged = {**self.policy(), **changes}
        if merged["mode"] == "organization" and merged["self_review_high_risk"]:
            raise ValidationError("self review of high-risk changes is only available in personal mode")
        with self.db.transaction():
            for key, value in changes.items():
                self.db.write("INSERT INTO participant_setting(key,value_json,updated_by,updated_at) VALUES(?,?,?,?) "
                              "ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json,updated_by=excluded.updated_by,updated_at=excluded.updated_at",
                              (key, json.dumps(value), actor, utcnow()))
        return merged

    def review_decision(self, reviewer, actor, risk):
        """How an approval by `reviewer` of a change proposed by `actor` is governed.

        `basis` records why an approval is allowed: an independent responsible party,
        the person reviewing their own agent's work (personal mode), or explicit self review.
        """
        policy = self.policy()
        reviewer_kind = self.kind(reviewer)
        if policy["human_final_approval"] and reviewer_kind == "agent":
            return {"allowed": False, "reason": "最终批准须由人完成（审批策略）", "self_review": False, "basis": None}
        if risk != "high" or self.independent(reviewer, actor):
            return {"allowed": True, "reason": None, "self_review": False, "basis": "independent"}
        personal = policy["mode"] == "personal" and reviewer_kind == "human"
        if personal and self.kind(actor) == "agent" and self.accountable(actor) == reviewer:
            # Personal mode: the owner is the human check on their agents' work.
            return {"allowed": True, "reason": "个人模式：负责人审核本人名下 Agent 的提案", "self_review": False, "basis": "owner_of_agent"}
        if personal and policy["self_review_high_risk"]:
            return {"allowed": True, "reason": "个人模式自审", "self_review": True, "basis": "self_review"}
        return {"allowed": False, "reason": "高风险变更需由另一独立责任方批准（high-risk changes require an independent reviewer）", "self_review": False, "basis": None}

    def assert_may_publish(self, publisher):
        if self.policy()["human_final_approval"] and self.kind(publisher) == "agent":
            raise ValidationError("最终批准须由人完成：Agent 不能应用或发布变更")

    # ------------------------------------------------------------------ people
    def setup(self, *, username, label, password):
        if not self.setup_required():
            raise ConflictError("local owner account already exists")
        return self.create_human(username=username, label=label, password=password, roles=["admin"], actor="setup")

    def create_human(self, *, username, label, password, roles, actor):
        username = str(username or "").strip().lower()
        if not _SLUG.match(username):
            raise ValidationError("用户名需以小写字母开头，2–40 位小写字母、数字、- 或 _")
        self._validate_password(password)
        self._validate_roles(roles, agent=False)
        identifier = f"user:{username}"
        now = utcnow()
        with self.db.transaction():
            if self.db.first("SELECT 1 AS present FROM participant WHERE id=?", (identifier,)):
                raise ConflictError(f"participant already exists: {identifier}")
            self.db.write("INSERT INTO participant(id,kind,label,owner,roles_json,status,password_hash,created_by,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
                          (identifier, "human", str(label or username).strip()[:80], None, json.dumps(roles), "active", _hash_password(password), actor, now, now))
        return self.get(identifier)

    @staticmethod
    def _validate_password(password):
        if not isinstance(password, str) or len(password) < 8 or len(password) > 200:
            raise ValidationError("密码至少 8 位")

    def change_password(self, identifier, current, new):
        row = self.db.first("SELECT password_hash FROM participant WHERE id=? AND kind='human'", (identifier,))
        if not row or not _check_password(str(current or ""), row["password_hash"]):
            raise AuthenticationError("当前密码不正确")
        self._validate_password(new)
        self.db.write("UPDATE participant SET password_hash=?,updated_at=? WHERE id=?", (_hash_password(new), utcnow(), identifier))
        return {"id": identifier, "password_changed": True}

    def login(self, username, password, client=None):
        username = str(username or "").strip().lower()
        with self._failure_lock:
            recent = [moment for moment in self._failures.get(username, []) if moment > time.monotonic() - 60]
            self._failures[username] = recent
            if len(recent) >= 5:
                raise AuthenticationError("登录失败次数过多，请一分钟后再试")
        row = self.db.first("SELECT * FROM participant WHERE id=? AND kind='human'", (f"user:{username}",))
        if not row or row["status"] != "active" or not _check_password(str(password or ""), row["password_hash"]):
            with self._failure_lock:
                self._failures.setdefault(username, []).append(time.monotonic())
            raise AuthenticationError("用户名或密码不正确")
        with self._failure_lock:
            self._failures.pop(username, None)
        issued = self._issue(row["id"], kind="session", label=client or "Studio", scopes=None, actor=row["id"], expires_at=_later(SESSION_HOURS))
        return {**issued, "participant": self._public(row)}

    # ------------------------------------------------------------------ agents
    def create_agent(self, *, slug, label, owner, roles, actor, host=None, model=None, description=""):
        slug = str(slug or "").strip().lower()
        if not _SLUG.match(slug):
            raise ValidationError("Agent 标识需以小写字母开头，2–40 位小写字母、数字、- 或 _")
        owner_row = self.db.first("SELECT kind,status FROM participant WHERE id=?", (owner,))
        if not owner_row or owner_row["kind"] != "human" or owner_row["status"] != "active":
            raise ValidationError("Agent 的负责人必须是有效的人员账号")
        self._validate_roles(roles, agent=True)
        identifier, now = f"agent:{slug}", utcnow()
        with self.db.transaction():
            if self.db.first("SELECT 1 AS present FROM participant WHERE id=?", (identifier,)):
                raise ConflictError(f"participant already exists: {identifier}")
            self.db.write("INSERT INTO participant(id,kind,label,owner,roles_json,status,host,model,description,created_by,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                          (identifier, "agent", str(label or slug).strip()[:80], owner, json.dumps(roles), "active",
                           self._text(host, 60), self._text(model, 80), str(description or "").strip()[:500], actor, now, now))
        return self.get(identifier)

    @staticmethod
    def _text(value, limit):
        value = str(value or "").strip()
        return value[:limit] or None

    def _validate_roles(self, roles, *, agent):
        if not isinstance(roles, list) or not roles or any(role not in ROLE_PERMISSIONS for role in roles):
            raise ValidationError("unknown participant roles")
        if agent and any(role not in AGENT_ROLES for role in roles):
            raise ValidationError(f"Agent 只能使用以下角色：{', '.join(AGENT_ROLES)}（审批与发布由人完成）")

    def update(self, identifier, changes, actor):
        row = self.db.first("SELECT * FROM participant WHERE id=?", (identifier,))
        if not row:
            raise NotFoundError(f"participant not found: {identifier}")
        allowed = {"label", "roles", "status", "host", "model", "description"}
        if not isinstance(changes, dict) or not changes or set(changes) - allowed:
            raise ValidationError(f"updatable fields: {', '.join(sorted(allowed))}")
        values = {}
        if "roles" in changes:
            self._validate_roles(changes["roles"], agent=row["kind"] == "agent")
            values["roles_json"] = json.dumps(changes["roles"])
        if "status" in changes:
            if changes["status"] not in ("active", "disabled"):
                raise ValidationError("status must be active or disabled")
            if identifier == actor and changes["status"] == "disabled":
                raise ValidationError("不能停用自己的账号")
            values["status"] = changes["status"]
        for key, limit in (("label", 80), ("host", 60), ("model", 80)):
            if key in changes:
                values[key] = self._text(changes[key], limit) or (row["label"] if key == "label" else None)
        if "description" in changes:
            values["description"] = str(changes["description"] or "").strip()[:500]
        values["updated_at"] = utcnow()
        assignments = ",".join(f"{key}=?" for key in values)
        self.db.write(f"UPDATE participant SET {assignments} WHERE id=?", (*values.values(), identifier))
        return self.get(identifier)

    # ------------------------------------------------------------------ credentials
    def issue(self, participant_id, *, label, actor, scopes=None, expires_in_days=None):
        row = self.db.first("SELECT * FROM participant WHERE id=?", (participant_id,))
        if not row:
            raise NotFoundError(f"participant not found: {participant_id}")
        if row["status"] != "active":
            raise ValidationError("participant is disabled")
        granted = permissions_for(json.loads(row["roles_json"]))
        if scopes is not None:
            if not isinstance(scopes, list) or not scopes or any(scope not in granted for scope in scopes):
                raise ValidationError("credential scopes must be a nonempty subset of the participant's permissions")
        expires = None
        if expires_in_days is not None:
            if not isinstance(expires_in_days, int) or not 1 <= expires_in_days <= 3650:
                raise ValidationError("expires_in_days must be 1–3650")
            expires = _later(24 * expires_in_days)
        label = str(label or "").strip()[:60] or "凭证"
        return self._issue(participant_id, kind="agent" if row["kind"] == "agent" else "personal", label=label,
                           scopes=scopes, actor=actor, expires_at=expires)

    def _issue(self, participant_id, *, kind, label, scopes, actor, expires_at):
        token = TOKEN_PREFIX + secrets.token_urlsafe(32)
        identifier = "cred_" + secrets.token_hex(8)
        self.db.write("INSERT INTO credential(id,participant_id,kind,label,token_hash,scopes_json,created_by,created_at,expires_at) VALUES(?,?,?,?,?,?,?,?,?)",
                      (identifier, participant_id, kind, label, _hash_token(token), json.dumps(scopes) if scopes is not None else None, actor, utcnow(), expires_at))
        return {"id": identifier, "token": token, "participant_id": participant_id, "label": label, "kind": kind,
                "scopes": scopes, "expires_at": expires_at, "note": "凭证只显示这一次；KnowledgeOS 只保存摘要。"}

    def revoke(self, credential_id, *, participant_id=None):
        row = self.db.first("SELECT * FROM credential WHERE id=?", (credential_id,))
        if not row or (participant_id and row["participant_id"] != participant_id):
            raise NotFoundError(f"credential not found: {credential_id}")
        if not row["revoked_at"]:
            self.db.write("UPDATE credential SET revoked_at=? WHERE id=?", (utcnow(), credential_id))
        return {"id": credential_id, "revoked": True}

    def credentials(self, participant_id, include_sessions=False):
        rows = self.db.execute("SELECT * FROM credential WHERE participant_id=? ORDER BY created_at DESC", (participant_id,))
        keys = ("id", "kind", "label", "created_by", "created_at", "expires_at", "last_used_at", "last_client", "revoked_at")
        return [{**{key: row[key] for key in keys}, "scopes": json.loads(row["scopes_json"]) if row["scopes_json"] else None}
                for row in rows if include_sessions or row["kind"] != "session"]

    def authenticate(self, token, client=None):
        if not token.startswith(TOKEN_PREFIX):
            return None
        row = self.db.first("SELECT c.id AS credential_id,c.kind AS credential_kind,c.scopes_json,c.expires_at,c.revoked_at,c.last_used_at,c.last_client,"
                            "p.* FROM credential c JOIN participant p ON p.id=c.participant_id WHERE c.token_hash=?", (_hash_token(token),))
        if not row:
            return None
        now = utcnow()
        if row["revoked_at"]:
            raise AuthenticationError("凭证已吊销")
        if row["expires_at"] and row["expires_at"] <= now:
            raise AuthenticationError("凭证已过期，请重新登录" if row["credential_kind"] == "session" else "凭证已过期")
        if row["status"] != "active":
            raise AuthenticationError("账号已停用")
        client = self._text(client, 120)
        # Coarse activity tracking: avoid a write on every request.
        if not row["last_used_at"] or row["last_used_at"][:16] != now[:16] or (client and client != row["last_client"]):
            self.db.write("UPDATE credential SET last_used_at=?,last_client=COALESCE(?,last_client) WHERE id=?", (now, client, row["credential_id"]))
        roles = json.loads(row["roles_json"])
        scopes = json.loads(row["scopes_json"]) if row["scopes_json"] else None
        return {"id": row["id"], "roles": roles, "permissions": permissions_for(roles, scopes), "kind": row["kind"],
                "label": row["label"], "owner": row["owner"], "credential_id": row["credential_id"],
                "credential_kind": row["credential_kind"], "host": row["host"], "model": row["model"], "client": client}

    # ------------------------------------------------------------------ overview
    def directory(self):
        participants = [self._public(row) for row in self.db.execute("SELECT * FROM participant ORDER BY kind DESC,created_at")]
        counts = {}
        for row in self.db.execute("SELECT actor,status,count(*) AS count FROM changeset GROUP BY actor,status"):
            counts.setdefault(row["actor"], {})[row["status"]] = row["count"]
        for participant in participants:
            participant["permissions"] = permissions_for(participant["roles"])
            participant["credentials"] = self.credentials(participant["id"])
            participant["changesets"] = counts.get(participant["id"], {})
            active = [item for item in participant["credentials"] if not item["revoked_at"]]
            participant["last_used_at"] = max((item["last_used_at"] for item in active if item["last_used_at"]), default=None)
        return {"participants": participants, "policy": self.policy(), "agent_roles": list(AGENT_ROLES),
                "role_permissions": ROLE_PERMISSIONS}


def require(principal, permission):
    if permission not in principal.get("permissions", permissions_for(principal["roles"])):
        raise AuthorizationError(f"principal {principal['id']} is not allowed to {permission}")
