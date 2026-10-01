"""SQLite projections, durable state, audit outbox, and hash chained ledger."""
from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
import uuid
import fcntl
from contextlib import contextmanager
from pathlib import Path

from .core import Config, ConflictError, IntegrityError, json_text, utcnow


class RuntimeLease:
    def __init__(self, config: Config, exclusive=False):
        config.ensure_runtime()
        self.file = (config.runtime / ".state.lock").open("a+")
        try:
            fcntl.flock(self.file.fileno(), (fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH) | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            self.file.close()
            raise ConflictError("runtime is in use; stop services before backup or restore") from exc

    def close(self):
        fcntl.flock(self.file.fileno(), fcntl.LOCK_UN)
        self.file.close()


PROJECTION_DDL = """
CREATE TABLE IF NOT EXISTS metadata(key TEXT PRIMARY KEY,value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS node(id TEXT PRIMARY KEY,kind TEXT NOT NULL,type TEXT NOT NULL,natural_key TEXT,label TEXT NOT NULL,
 key_namespace TEXT NOT NULL DEFAULT '',aliases_json TEXT NOT NULL DEFAULT '[]',tags_json TEXT NOT NULL DEFAULT '[]',
 attrs_json TEXT NOT NULL DEFAULT '{}',lifecycle TEXT NOT NULL,revision INTEGER NOT NULL DEFAULT 1,
 source_class TEXT NOT NULL,source_path TEXT,source_hash TEXT NOT NULL,narrative TEXT NOT NULL DEFAULT '',compiled_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS assertion(id TEXT PRIMARY KEY,node_id TEXT NOT NULL,predicate TEXT NOT NULL,value_json TEXT NOT NULL,
 qualifiers_json TEXT NOT NULL DEFAULT '{}',observed_at TEXT,valid_from TEXT,valid_to TEXT,assertion_kind TEXT NOT NULL,
 status TEXT NOT NULL,confidence REAL,evidence_refs_json TEXT NOT NULL DEFAULT '[]',source_refs_json TEXT NOT NULL DEFAULT '[]',
 supersedes TEXT,provenance_tier TEXT NOT NULL,temperature TEXT NOT NULL,source_path TEXT,source_hash TEXT NOT NULL,
 FOREIGN KEY(node_id) REFERENCES node(id) ON DELETE CASCADE);
CREATE INDEX IF NOT EXISTS idx_assertion_node_predicate ON assertion(node_id,predicate);
CREATE TABLE IF NOT EXISTS edge(src TEXT NOT NULL,predicate TEXT NOT NULL,dst TEXT NOT NULL,rel_id TEXT NOT NULL DEFAULT '',
 valid_from TEXT NOT NULL DEFAULT '',valid_to TEXT NOT NULL DEFAULT '',weight REAL,source_path TEXT,
 PRIMARY KEY(src,predicate,dst,rel_id,valid_from));
CREATE INDEX IF NOT EXISTS idx_edge_dst ON edge(dst,predicate);
CREATE TABLE IF NOT EXISTS source_ref(id TEXT PRIMARY KEY,source_system TEXT NOT NULL,source_object TEXT,source_record_id TEXT,
 source_timestamp TEXT,source_version TEXT,source_hash TEXT,authority_class TEXT,locator TEXT,captured_at TEXT NOT NULL,
 metadata_json TEXT NOT NULL DEFAULT '{}');
CREATE TABLE IF NOT EXISTS attribute_candidate(node_id TEXT NOT NULL,predicate TEXT NOT NULL,source_ref_id TEXT NOT NULL,
 source_class TEXT NOT NULL,value_json TEXT NOT NULL,observed_at TEXT,source_hash TEXT NOT NULL,
 PRIMARY KEY(node_id,predicate,source_ref_id),FOREIGN KEY(node_id) REFERENCES node(id) ON DELETE CASCADE);
CREATE TABLE IF NOT EXISTS audit_event_ref(event_id TEXT PRIMARY KEY,event_type TEXT NOT NULL,target_id TEXT,
 ledger_sequence INTEGER NOT NULL,timestamp TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS node_embedding(node_id TEXT PRIMARY KEY,model_id TEXT NOT NULL,dimensions INTEGER NOT NULL,
 vector_json TEXT NOT NULL,source_hash TEXT NOT NULL,FOREIGN KEY(node_id) REFERENCES node(id) ON DELETE CASCADE);
CREATE TABLE IF NOT EXISTS entity_card(node_id TEXT PRIMARY KEY,card_json TEXT NOT NULL,updated_at TEXT NOT NULL,
 FOREIGN KEY(node_id) REFERENCES node(id) ON DELETE CASCADE);
"""

DURABLE_DDL = """
CREATE TABLE IF NOT EXISTS durable.derived_result(id TEXT PRIMARY KEY,model_id TEXT NOT NULL,model_version TEXT NOT NULL,
 input_hash TEXT NOT NULL,scenario TEXT NOT NULL DEFAULT 'default',output_json TEXT NOT NULL,trace_json TEXT NOT NULL,
 run_id TEXT NOT NULL,calculated_at TEXT NOT NULL,status TEXT NOT NULL DEFAULT 'current',
 input_refs_json TEXT NOT NULL DEFAULT '{}',model_snapshot_json TEXT,
 UNIQUE(model_id,model_version,input_hash,scenario));
CREATE TABLE IF NOT EXISTS durable.model_revision(id TEXT PRIMARY KEY,model_id TEXT NOT NULL,version TEXT NOT NULL,
 definition_json TEXT NOT NULL,recorded_at TEXT NOT NULL,source_path TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS durable.idx_model_revision_model ON model_revision(model_id,recorded_at);
CREATE TABLE IF NOT EXISTS durable.review_item(id TEXT PRIMARY KEY,kind TEXT NOT NULL,target_id TEXT,severity INTEGER NOT NULL,
 priority TEXT NOT NULL,reason TEXT NOT NULL,details_json TEXT NOT NULL DEFAULT '{}',status TEXT NOT NULL DEFAULT 'open',
 created_at TEXT NOT NULL,updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS durable.changeset(id TEXT PRIMARY KEY,actor TEXT NOT NULL,title TEXT,target_source TEXT NOT NULL,
 risk TEXT NOT NULL,status TEXT NOT NULL,patch_json TEXT NOT NULL,operations_json TEXT NOT NULL DEFAULT '[]',
 reason TEXT NOT NULL,created_at TEXT NOT NULL,reviewed_by TEXT,reviewed_at TEXT,review_note TEXT,published_by TEXT,
 published_at TEXT,source_revision TEXT,base_revision TEXT,publication_json TEXT,expected_json TEXT,
 lock_version INTEGER NOT NULL DEFAULT 0,request_key TEXT);
CREATE TABLE IF NOT EXISTS durable.audit_outbox(event_id TEXT PRIMARY KEY,event_json TEXT NOT NULL,status TEXT NOT NULL DEFAULT 'pending',
 created_at TEXT NOT NULL,delivered_at TEXT,last_error TEXT);
CREATE INDEX IF NOT EXISTS durable.idx_outbox_pending ON audit_outbox(status,created_at);
CREATE TABLE IF NOT EXISTS durable.connector_record(source_ref_id TEXT NOT NULL,source_hash TEXT NOT NULL,version TEXT,
 observed_at TEXT NOT NULL,record_json TEXT NOT NULL,mapping_json TEXT NOT NULL,PRIMARY KEY(source_ref_id,source_hash));
CREATE TABLE IF NOT EXISTS durable.entity_snapshot(node_id TEXT NOT NULL,recorded_at TEXT NOT NULL,content_hash TEXT NOT NULL,
 snapshot_json TEXT NOT NULL,PRIMARY KEY(node_id,recorded_at));
CREATE TABLE IF NOT EXISTS durable.schema_migration(version INTEGER PRIMARY KEY,applied_at TEXT NOT NULL);
INSERT OR IGNORE INTO durable.schema_migration VALUES(1,strftime('%Y-%m-%dT%H:%M:%fZ','now'));
"""

LEDGER_DDL = """
PRAGMA journal_mode=WAL;
CREATE TABLE IF NOT EXISTS event(sequence INTEGER PRIMARY KEY AUTOINCREMENT,event_id TEXT NOT NULL UNIQUE,
 event_type TEXT NOT NULL,actor TEXT NOT NULL,target_id TEXT,source_ref TEXT,before_hash TEXT,after_hash TEXT,
 reason TEXT,payload_json TEXT NOT NULL DEFAULT '{}',timestamp TEXT NOT NULL,prev_hash TEXT NOT NULL,event_hash TEXT NOT NULL UNIQUE);
CREATE INDEX IF NOT EXISTS idx_event_target ON event(target_id,sequence DESC);
CREATE INDEX IF NOT EXISTS idx_event_type ON event(event_type,sequence DESC);
CREATE TRIGGER IF NOT EXISTS event_no_update BEFORE UPDATE ON event BEGIN SELECT RAISE(ABORT,'ledger is append-only'); END;
CREATE TRIGGER IF NOT EXISTS event_no_delete BEFORE DELETE ON event BEGIN SELECT RAISE(ABORT,'ledger is append-only'); END;
"""


class Database:
    def __init__(self, config: Config):
        config.ensure_runtime()
        self.config = config
        self.lease = RuntimeLease(config)
        self.lock = threading.RLock()
        self.depth = 0
        self.connection = sqlite3.connect(str(config.runtime / "knowledge.index.db"), timeout=5, check_same_thread=False, isolation_level=None)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA foreign_keys=ON")
        self.connection.execute("PRAGMA journal_mode=DELETE")
        self.connection.execute("PRAGMA synchronous=FULL")
        self.connection.execute("ATTACH DATABASE ? AS durable", (str(config.runtime / "knowledge.state.db"),))
        self.connection.execute("PRAGMA durable.journal_mode=DELETE")
        self.connection.execute("PRAGMA durable.synchronous=FULL")
        self.connection.executescript(PROJECTION_DDL)
        node_columns = {row["name"] for row in self.execute("PRAGMA main.table_info(node)")}
        if "key_namespace" not in node_columns:
            self.connection.execute("ALTER TABLE node ADD COLUMN key_namespace TEXT NOT NULL DEFAULT ''")
        self.connection.execute("DROP INDEX IF EXISTS idx_node_key")
        self.connection.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_node_scoped_key ON node(key_namespace,type,natural_key) WHERE natural_key IS NOT NULL")
        self.connection.executescript(DURABLE_DDL)
        derived_columns = {row["name"] for row in self.execute("PRAGMA durable.table_info(derived_result)")}
        for name, definition in {"input_refs_json": "TEXT NOT NULL DEFAULT '{}'", "model_snapshot_json": "TEXT"}.items():
            if name not in derived_columns:
                self.connection.execute(f"ALTER TABLE durable.derived_result ADD COLUMN {name} {definition}")
        columns = {row["name"] for row in self.execute("PRAGMA durable.table_info(changeset)")}
        for name, definition in {"title": "TEXT", "operations_json": "TEXT NOT NULL DEFAULT '[]'", "review_note": "TEXT",
                                 "published_by": "TEXT", "published_at": "TEXT", "source_revision": "TEXT", "base_revision": "TEXT",
                                 "publication_json": "TEXT", "expected_json": "TEXT", "lock_version": "INTEGER NOT NULL DEFAULT 0",
                                 "request_key": "TEXT"}.items():
            if name not in columns:
                self.connection.execute(f"ALTER TABLE durable.changeset ADD COLUMN {name} {definition}")
        self.connection.execute("CREATE UNIQUE INDEX IF NOT EXISTS durable.idx_changeset_request ON changeset(actor,request_key) WHERE request_key IS NOT NULL")
        for table in ("changeset", "audit_outbox", "derived_result", "review_item"):
            if self.first("SELECT name FROM main.sqlite_master WHERE type='table' AND name=?", (table,)):
                columns = [r["name"] for r in self.execute(f"PRAGMA main.table_info({table})")]
                durable_columns = {r["name"] for r in self.execute(f"PRAGMA durable.table_info({table})")}
                common = [name for name in columns if name in durable_columns]
                names = ",".join(common)
                self.connection.execute(f"INSERT OR IGNORE INTO durable.{table}({names}) SELECT {names} FROM main.{table}")
                self.connection.execute(f"DROP TABLE main.{table}")
        try:
            self.connection.execute("CREATE VIRTUAL TABLE IF NOT EXISTS node_fts USING fts5(node_id UNINDEXED,label,body,tokenize='unicode61')")
            self.fts_enabled = True
        except sqlite3.OperationalError:
            self.fts_enabled = False

    def execute(self, sql: str, binds=()) -> list[dict]:
        with self.lock:
            return [dict(row) for row in self.connection.execute(sql, binds).fetchall()]

    def first(self, sql: str, binds=()) -> dict | None:
        with self.lock:
            row = self.connection.execute(sql, binds).fetchone()
            return dict(row) if row else None

    def write(self, sql: str, binds=()) -> int:
        with self.lock:
            return self.connection.execute(sql, binds).rowcount

    @contextmanager
    def transaction(self):
        with self.lock:
            name = f"nested_{self.depth}"
            self.connection.execute(f"SAVEPOINT {name}" if self.depth else "BEGIN IMMEDIATE")
            self.depth += 1
            try:
                yield
                self.depth -= 1
                self.connection.execute(f"RELEASE SAVEPOINT {name}" if self.depth else "COMMIT")
            except BaseException:
                self.depth -= 1
                if self.depth:
                    self.connection.execute(f"ROLLBACK TO SAVEPOINT {name}")
                    self.connection.execute(f"RELEASE SAVEPOINT {name}")
                else:
                    self.connection.execute("ROLLBACK")
                raise

    def reset_projection(self) -> None:
        with self.transaction():
            for table in ("assertion", "edge", "source_ref", "audit_event_ref", "entity_card", "attribute_candidate", "node_embedding", "node"):
                self.write(f"DELETE FROM {table}")
            if self.fts_enabled:
                self.write("DELETE FROM node_fts")
            self.write("DELETE FROM metadata WHERE key LIKE 'source:%'")

    def close(self):
        self.connection.close()
        self.lease.close()


class Ledger:
    genesis = "0" * 64

    def __init__(self, config: Config):
        config.ensure_runtime()
        self.lease = RuntimeLease(config)
        self.connection = sqlite3.connect(str(config.runtime / "knowledge.ledger.db"), timeout=5, check_same_thread=False, isolation_level=None)
        self.connection.row_factory = sqlite3.Row
        self.lock = threading.RLock()
        self.connection.executescript(LEDGER_DDL)

    @staticmethod
    def _clean(row):
        if row is None:
            return None
        result = dict(row)
        result["payload_json"] = json.loads(result["payload_json"])
        return result

    def append(self, *, event_type: str, actor: str, target_id=None, source_ref=None, before_hash=None,
               after_hash=None, reason=None, payload=None, event_id=None, timestamp=None):
        event_id, timestamp = event_id or str(uuid.uuid4()), timestamp or utcnow()
        with self.lock:
            existing = self.connection.execute("SELECT * FROM event WHERE event_id=?", (event_id,)).fetchone()
            if existing:
                return self._clean(existing)
            self.connection.execute("BEGIN IMMEDIATE")
            try:
                previous = self.connection.execute("SELECT event_hash FROM event ORDER BY sequence DESC LIMIT 1").fetchone()
                prev_hash = previous[0] if previous else self.genesis
                event = {"event_id": event_id, "event_type": event_type, "actor": actor, "target_id": target_id,
                         "source_ref": source_ref, "before_hash": before_hash, "after_hash": after_hash,
                         "reason": reason, "payload": payload or {}, "timestamp": timestamp}
                event_hash = hashlib.sha256((prev_hash + json_text(event, sorted_keys=True)).encode()).hexdigest()
                self.connection.execute("INSERT INTO event(event_id,event_type,actor,target_id,source_ref,before_hash,after_hash,reason,payload_json,timestamp,prev_hash,event_hash) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                                        (event_id, event_type, actor, target_id, source_ref, before_hash, after_hash, reason,
                                         json_text(payload or {}, sorted_keys=True), timestamp, prev_hash, event_hash))
                self.connection.execute("COMMIT")
            except BaseException:
                self.connection.execute("ROLLBACK")
                raise
            return self._clean(self.connection.execute("SELECT * FROM event WHERE event_id=?", (event_id,)).fetchone())

    def verify(self):
        previous = self.genesis
        rows = self.connection.execute("SELECT * FROM event ORDER BY sequence").fetchall()
        for row in rows:
            data = dict(row)
            event = {key: data[key] for key in ("event_id", "event_type", "actor", "target_id", "source_ref", "before_hash", "after_hash", "reason", "timestamp")}
            event["payload"] = json.loads(data["payload_json"])
            expected = hashlib.sha256((previous + json_text(event, sorted_keys=True)).encode()).hexdigest()
            if data["prev_hash"] != previous or data["event_hash"] != expected:
                raise IntegrityError(f"ledger hash mismatch at sequence {data['sequence']}")
            previous = data["event_hash"]
        return {"valid": True, "events": len(rows), "head_hash": previous}

    def events(self, *, target_id=None, event_type=None, limit=100):
        conditions, binds = [], []
        if target_id:
            conditions.append("target_id=?")
            binds.append(target_id)
        if event_type:
            conditions.append("event_type=?")
            binds.append(event_type)
        sql = "SELECT * FROM event" + (" WHERE " + " AND ".join(conditions) if conditions else "") + " ORDER BY sequence DESC LIMIT ?"
        return [self._clean(row) for row in self.connection.execute(sql, (*binds, int(limit))).fetchall()]

    def close(self):
        self.connection.close()
        self.lease.close()


class Audit:
    def __init__(self, database: Database, ledger: Ledger):
        self.db, self.ledger = database, ledger

    def stage(self, **event):
        event["event_id"] = event.get("event_id") or str(uuid.uuid4())
        event["timestamp"] = event.get("timestamp") or utcnow()
        self.db.write("INSERT OR IGNORE INTO audit_outbox(event_id,event_json,status,created_at) VALUES(?,?,?,?)",
                      (event["event_id"], json_text(event), "pending", event["timestamp"]))
        return event["event_id"]

    def flush(self):
        rows = self.db.execute("SELECT event_id,event_json FROM audit_outbox WHERE status='pending' ORDER BY created_at,event_id")
        for row in rows:
            event = self.ledger.append(**json.loads(row["event_json"]))
            with self.db.transaction():
                self.db.write("UPDATE audit_outbox SET status='delivered',delivered_at=? WHERE event_id=?", (utcnow(), row["event_id"]))
                self.db.write("INSERT OR IGNORE INTO audit_event_ref(event_id,event_type,target_id,ledger_sequence,timestamp) VALUES(?,?,?,?,?)",
                              (event["event_id"], event["event_type"], event["target_id"], event["sequence"], event["timestamp"]))
