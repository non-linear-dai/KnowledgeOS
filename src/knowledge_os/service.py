"""Domain independent KnowledgeOS application service."""
from __future__ import annotations

import hashlib
import json
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path

from . import __contract_version__
from .compiler import Compiler, card_assertion, decode_assertion
from .core import Config, ConflictError, NotFoundError, Registry, ValidationError, digest, json_text, utcnow
from .models import ModelEngine
from .storage import Audit, Database, Ledger
from .governance import ChangePlan
from .semantic import SemanticIndex, MODEL_ID


def envelope(data, *, source=None, temporal=None, quality=None, conflicts=None, gaps=None, traces=None):
    return {"data": data, "source_status": source or {}, "temporal_status": temporal or {},
            "quality_status": quality or {}, "conflicts": conflicts or [], "knowledge_gaps": gaps or [], "trace_refs": traces or []}


class Service:
    def __init__(self, config: Config | None = None):
        self.config = config or Config()
        self.registry = Registry(self.config)
        self.db = Database(self.config)
        self.ledger = Ledger(self.config)
        self.audit = Audit(self.db, self.ledger)
        self.audit.flush()
        self.models = ModelEngine(self.registry, self.db, self.audit)
        from .agent import AgentService
        self.agent = AgentService(self)

    def close(self):
        self.db.close()
        self.ledger.close()

    def refresh_registry(self):
        if self.registry.source_fingerprint() != self.registry.fingerprint:
            self.registry.reload()

    def compile(self, rebuild=False):
        result = Compiler(self.config, self.registry, self.db, self.ledger).compile(rebuild=rebuild)
        self._record_model_revisions()
        return result

    def _record_model_revisions(self):
        with self.db.transaction():
            for model in self.registry.models.values():
                definition = {key: value for key, value in model.items() if not key.startswith("_")}
                identifier = digest(definition)
                path = str(Path(model["_path"]).relative_to(self.config.root))
                inserted = self.db.write("INSERT OR IGNORE INTO durable.model_revision(id,model_id,version,definition_json,recorded_at,source_path) VALUES(?,?,?,?,?,?)",
                                         (identifier, model["id"], model["version"], json_text(definition), utcnow(), path))
                if inserted:
                    self.audit.stage(event_type="model_revision_recorded", actor="compiler", target_id=model["id"],
                                     source_ref=path, after_hash=identifier, reason="compiled control definition snapshot",
                                     payload={"version": model["version"], "definition_hash": identifier})
        self.audit.flush()

    def model_history(self, model_id):
        if model_id not in self.registry.models and not self.db.first("SELECT 1 FROM durable.model_revision WHERE model_id=?", (model_id,)):
            raise NotFoundError(f"model not found: {model_id}")
        rows = self.db.execute("SELECT * FROM durable.model_revision WHERE model_id=? ORDER BY recorded_at DESC,id DESC", (model_id,))
        return envelope([{"id": row["id"], "model_id": row["model_id"], "version": row["version"],
                          "definition": json.loads(row["definition_json"]), "recorded_at": row["recorded_at"],
                          "source_path": row["source_path"]} for row in rows])

    def resolve(self, query, scope=None):
        needle = str(query).strip()
        rows = self.db.execute("""SELECT id,type,natural_key,label,aliases_json,lifecycle,source_class FROM node
          WHERE id=? OR natural_key=? OR lower(label)=lower(?) OR lower(label) LIKE lower(?)
          ORDER BY CASE WHEN id=? THEN 0 WHEN natural_key=? THEN 1 WHEN lower(label)=lower(?) THEN 2 ELSE 3 END,label LIMIT 20""",
          (needle, needle, needle, "%" + needle.replace("%", "\\%").replace("_", "\\_") + "%", needle, needle, needle))
        return envelope([{**{k: v for k, v in row.items() if k != "aliases_json"}, "aliases": json.loads(row["aliases_json"])}
                         for row in rows if not scope or row["type"] == scope])

    def get(self, identifier, include_history=False):
        self._refresh_temperatures(identifier)
        card = self.db.first("SELECT card_json,updated_at FROM entity_card WHERE node_id=?", (identifier,))
        if not card:
            raise NotFoundError(f"node not found: {identifier}")
        data = json.loads(card["card_json"])
        data["current_assertions"] = [card_assertion(row) for row in self.db.execute(
            "SELECT * FROM assertion WHERE node_id=? AND temperature='hot' AND status='confirmed' ORDER BY predicate,id", (identifier,))]
        data["card_updated_at"] = card["updated_at"]
        if include_history:
            data["history"] = self.history(identifier)["data"]
        return envelope(data, gaps=data.get("knowledge_gaps", []))

    def query(self, template, params=None):
        params = params or {}
        if template == "nodes_by_type":
            data = self.db.execute("SELECT id,type,label,lifecycle,source_class FROM node WHERE type=? ORDER BY label", (params["type"],))
        elif template == "current_assertions":
            identifier = params["node_id"]
            self._refresh_temperatures(identifier)
            data = [decode_assertion(row) for row in self.db.execute("SELECT * FROM assertion WHERE node_id=? AND temperature='hot' AND status='confirmed' ORDER BY COALESCE(valid_from,observed_at,'') DESC,id", (identifier,))]
        elif template == "review_queue":
            data = self.review(params.get("priority"), params.get("limit", 100))["data"]
        else:
            raise ValidationError(f"unknown query template: {template}")
        return envelope(data)

    @staticmethod
    def _instant(value):
        try:
            return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValidationError(f"invalid timestamp: {value}") from exc

    def neighbors(self, identifier, relation_types=None, depth=1, as_of=None, recorded_as_of=None):
        as_of = as_of or recorded_as_of or utcnow()
        instant = self._instant(as_of)
        types = relation_types or []
        if isinstance(types, str):
            types = types.split(",")
        if recorded_as_of:
            snapshots = self._snapshots_at(recorded_as_of)
            historical_edges = {}
            for snapshot in snapshots.values():
                for row in snapshot.get("edge_history", []):
                    if types and row["predicate"] not in types:
                        continue
                    if row.get("valid_from") and self._instant(row["valid_from"]) > instant:
                        continue
                    if row.get("valid_to") and self._instant(row["valid_to"]) <= instant:
                        continue
                    key = (row["src"], row["predicate"], row["dst"], row.get("rel_id", ""), row.get("valid_from", ""))
                    historical_edges[key] = row
            visited, frontier, found = {identifier: 0}, [identifier], {}
            for level in range(min(max(int(depth), 1), 5)):
                next_frontier = []
                for row in historical_edges.values():
                    if row["src"] not in frontier and row["dst"] not in frontier:
                        continue
                    key = (row["src"], row["predicate"], row["dst"], row.get("rel_id", ""), row.get("valid_from", ""))
                    found[key] = row
                    for other in (row["src"], row["dst"]):
                        if other not in visited:
                            visited[other] = level + 1
                            next_frontier.append(other)
                frontier = next_frontier
            nodes = [{"id": node_id, "label": snapshots.get(node_id, {}).get("label"),
                      "type": snapshots.get(node_id, {}).get("type"), "distance": distance,
                      "missing": node_id not in snapshots} for node_id, distance in visited.items()]
            return envelope({"nodes": nodes, "edges": list(found.values()), "as_of": as_of, "recorded_as_of": recorded_as_of})
        visited, frontier, edges = {identifier: 0}, [identifier], {}
        for level in range(min(max(int(depth), 1), 5)):
            next_frontier = []
            for node_id in frontier:
                for row in self.db.execute("SELECT * FROM edge WHERE src=? OR dst=?", (node_id, node_id)):
                    if types and row["predicate"] not in types:
                        continue
                    if row["valid_from"] and self._instant(row["valid_from"]) > instant:
                        continue
                    if row["valid_to"] and self._instant(row["valid_to"]) <= instant:
                        continue
                    key = (row["src"], row["predicate"], row["dst"], row["rel_id"], row["valid_from"])
                    edges[key] = row
                    other = row["dst"] if row["src"] == node_id else row["src"]
                    if other not in visited:
                        visited[other] = level + 1
                        next_frontier.append(other)
            frontier = next_frontier
            if not frontier:
                break
        nodes = []
        for node_id, distance in visited.items():
            row = self.db.first("SELECT id,type,label,lifecycle FROM node WHERE id=?", (node_id,))
            nodes.append({**row, "distance": distance} if row else {"id": node_id, "distance": distance, "missing": True})
        return envelope({"nodes": nodes, "edges": list(edges.values()), "as_of": as_of})

    def history(self, identifier, predicate=None, range=None):
        sql, binds = "SELECT * FROM assertion WHERE node_id=?", [identifier]
        if predicate:
            sql += " AND predicate=?"
            binds.append(predicate)
        if range and range[0]:
            sql += " AND COALESCE(observed_at,valid_from,'')>=?"
            binds.append(range[0])
        if range and len(range) > 1 and range[1]:
            sql += " AND COALESCE(observed_at,valid_from,'')<=?"
            binds.append(range[1])
        sql += " ORDER BY COALESCE(valid_from,observed_at,'') DESC,id"
        events = self.ledger.events(target_id=identifier)
        node = self.db.first("SELECT source_path FROM node WHERE id=?", (identifier,))
        if node and node["source_path"]:
            events.extend(self.ledger.events(target_id=node["source_path"]))
            events = list({item["event_id"]: item for item in events}.values())
            events.sort(key=lambda item: -item["sequence"])
        return envelope({"assertions": [decode_assertion(row) for row in self.db.execute(sql, binds)], "events": events[:100]})

    def search(self, query, filters=None, mode="hybrid"):
        if mode not in ("keyword", "vector", "hybrid", "hybrid_research", "structured_first", "temporal_graph_first"):
            raise ValidationError(f"unsupported search mode: {mode}")
        self._refresh_temperatures()
        filters = filters or {}
        typ = filters.get("type")
        term = str(query).strip()
        rows = []
        if self.db.fts_enabled and term:
            tokens = re.findall(r"[\w:-]+", term, re.UNICODE)
            if tokens:
                match = " OR ".join('"' + token.replace('"', '""') + '"' for token in tokens)
                try:
                    sql = "SELECT n.id,n.type,n.label,n.lifecycle,n.source_class,bm25(node_fts) AS keyword_score FROM node_fts JOIN node n ON n.id=node_fts.node_id WHERE node_fts MATCH ?"
                    binds = [match]
                    if typ:
                        sql += " AND n.type=?"
                        binds.append(typ)
                    rows = self.db.execute(sql + " ORDER BY keyword_score LIMIT 50", binds)
                except Exception:
                    rows = []
        if not rows:
            sql = "SELECT id,type,label,lifecycle,source_class FROM node WHERE (lower(label) LIKE lower(?) OR lower(narrative) LIKE lower(?))"
            binds = ["%" + term.lower() + "%"] * 2
            if typ:
                sql += " AND type=?"
                binds.append(typ)
            rows = self.db.execute(sql + " ORDER BY label LIMIT 50", binds)
        semantic = [] if mode == "keyword" or not term else SemanticIndex(self.db).search(term, typ)
        if mode == "vector":
            result = semantic
        elif mode == "keyword":
            result = rows
        else:
            combined = {row["id"]: {**row, "score": 0.55 / (index + 1)} for index, row in enumerate(rows)}
            for index, row in enumerate(semantic):
                current = combined.get(row["id"], {})
                combined[row["id"]] = {**current, **row, "score": current.get("score", 0.0) + 0.45 / (index + 1)}
            result = sorted(combined.values(), key=lambda row: -row["score"])[:50]
        plans = {"keyword": ["fts_or_label"], "vector": ["deterministic_embedding"],
                 "structured_first": ["structured_identity", "fts", "deterministic_embedding"],
                 "temporal_graph_first": ["temporal_filter", "graph_context", "fts", "deterministic_embedding"]}
        return envelope(result, source={"mode": mode, "fts": self.db.fts_enabled, "vector": bool(semantic),
                                        "vector_model": MODEL_ID, "plan": plans.get(mode, ["fts", "deterministic_embedding", "rank_fusion"])})

    def explain(self, target, field_or_assertion=None):
        row = self.db.first("SELECT * FROM assertion WHERE id=?", (field_or_assertion or target,))
        if row:
            data = decode_assertion(row)
            data["ledger_events"] = self.ledger.events(target_id=row["id"], limit=50)
            return envelope(data, traces=data["provenance"]["source_refs"])
        node = self.db.first("SELECT * FROM node WHERE id=?", (target,))
        if not node:
            raise NotFoundError(f"target not found: {target}")
        node["attrs"] = json.loads(node["attrs_json"])
        node["ledger_events"] = self.ledger.events(target_id=target, limit=50)
        return envelope(node, source={key: node[key] for key in ("source_class", "source_path", "source_hash")})

    def calculate(self, model_id, inputs, scenario="default"):
        result = self.models.calculate(model_id, inputs, scenario)
        return envelope(result, traces=[result["run_id"]])

    def preview_model(self, model, inputs):
        from .model_contract import validate_model
        if not isinstance(model, dict):
            raise ValidationError("model definition must be an object")
        validate_model(model, self.registry.predicates, self.registry.ontology, self.registry.units)
        return envelope(self.models.preview(model, inputs), quality={"preview_only": True})

    def _effective_assertion(self, node_id, predicate, as_of, *, rate_type=None):
        instant = self._instant(as_of)
        if instant.tzinfo is None:
            raise ValidationError("as_of requires a timezone offset")
        candidates = []
        for row in self.db.execute("SELECT * FROM assertion WHERE node_id=? AND predicate=? AND status='confirmed'",
                                   (node_id, predicate)):
            if row["observed_at"] and self._instant(row["observed_at"]) > instant:
                continue
            if row["valid_from"] and self._instant(row["valid_from"]) > instant:
                continue
            if row["valid_to"] and self._instant(row["valid_to"]) <= instant:
                continue
            if rate_type and json.loads(row["qualifiers_json"]).get("rate_type") != rate_type:
                continue
            candidates.append(row)
        if not candidates:
            raise ValidationError(f"no confirmed {predicate} assertion for {node_id} at {as_of}")
        floor = datetime.min.replace(tzinfo=timezone.utc)
        ranking = lambda row: (self._instant(row["valid_from"]) if row["valid_from"] else floor,
                               self._instant(row["observed_at"]) if row["observed_at"] else floor)
        candidates.sort(key=ranking, reverse=True)
        if len(candidates) > 1 and ranking(candidates[0]) == ranking(candidates[1]):
            raise ConflictError(f"ambiguous {predicate} assertions for {node_id} at {as_of}")
        return candidates[0]

    @staticmethod
    def _assertion_ref(row):
        return {"assertion_id": row["id"], "source_refs": json.loads(row["source_refs_json"]),
                "evidence_refs": json.loads(row["evidence_refs_json"]), "source_hash": row["source_hash"],
                "observed_at": row["observed_at"], "valid_from": row["valid_from"], "valid_to": row["valid_to"]}

    def calculate_for_entity(self, model_id, node_id, *, as_of=None, scenario="default"):
        model = self.registry.models.get(model_id)
        if not model or not model.get("applies_to"):
            raise ValidationError(f"model {model_id} has no entity input bindings")
        node = self.db.first("SELECT id,type FROM node WHERE id=?", (node_id,))
        if not node or node["type"] != model["applies_to"]:
            raise ValidationError(f"model {model_id} requires a {model['applies_to']} entity")
        as_of = as_of or utcnow()
        inputs, refs = {}, {}
        for definition in model["inputs"]:
            row = self._effective_assertion(node_id, definition["predicate"], as_of)
            inputs[definition["id"]] = json.loads(row["value_json"])
            refs[definition["id"]] = self._assertion_ref(row)
        result = self.models.calculate(model_id, inputs, scenario, input_refs=refs)
        return envelope(result, temporal={"as_of": as_of}, traces=[result["run_id"]])

    def convert_currency(self, amount, quote_node_id, *, as_of, rate_type="mid"):
        from .fx import validate_exchange_rate
        from .models import decimal
        if not isinstance(amount, dict) or not isinstance(amount.get("unit"), str) or not re.fullmatch(r"[A-Z]{3}", amount["unit"]):
            raise ValidationError("amount requires a three-letter currency unit")
        decimal(amount.get("literal"))
        node = self.db.first("SELECT id,type FROM node WHERE id=?", (quote_node_id,))
        if not node or node["type"] != "exchange_quote":
            raise ValidationError("quote_node_id must identify an exchange_quote")
        row = self._effective_assertion(quote_node_id, "exchange_rate", as_of, rate_type=rate_type)
        if not str(row["source_path"]).startswith("connector:"):
            raise ValidationError("exchange quote must originate from a connector")
        value, qualifiers = json.loads(row["value_json"]), json.loads(row["qualifiers_json"])
        validate_exchange_rate({"value": value, "qualifiers": qualifiers}, self.registry.currencies)
        freshness_days = self.registry.freshness_days("exchange_rate")
        if freshness_days is not None and row["observed_at"] and (self._instant(as_of) - self._instant(row["observed_at"])).total_seconds() > freshness_days * 86400:
            raise ValidationError("exchange quote is stale at the requested time")
        if amount["unit"] != qualifiers["base_currency"]:
            raise ValidationError("amount currency does not match quote base_currency")
        result = self.models.calculate("fx_conversion", {"amount": amount, "rate": value["literal"]},
                                       scenario=f"{quote_node_id}:{rate_type}:{as_of}",
                                       input_refs={"rate": self._assertion_ref(row)},
                                       output_currency=qualifiers["quote_currency"], governed=True)
        return envelope(result, temporal={"as_of": as_of}, traces=[result["run_id"]])

    def context(self, identifier, *, domain, max_items=25, as_of=None, recorded_as_of=None):
        pack = self.registry.domain(domain)
        snapshot = self._snapshots_at(recorded_as_of).get(identifier) if recorded_as_of else None
        card = (snapshot or {"id": identifier, "knowledge_gaps": ["no_recorded_snapshot"]}) if recorded_as_of else self.get(identifier)["data"]
        if as_of and not recorded_as_of:
            historical = self._snapshots_at(as_of).get(identifier)
            card = {**card, "attrs": historical.get("attrs", {}) if historical else {}}
            if not historical:
                card["knowledge_gaps"] = list(dict.fromkeys(card.get("knowledge_gaps", []) + ["historical_attributes_unavailable"]))
        retrieval = pack.get("retrieval", {})
        relations = self.neighbors(identifier, retrieval.get("relation_types"), retrieval.get("depth", 1), as_of, recorded_as_of)["data"]
        if recorded_as_of:
            instant = self._instant(as_of or recorded_as_of)
            rows = [row for row in (snapshot or {}).get("assertion_history", [])
                    if row["status"] in ("confirmed", "superseded", "stale") and all(
                        not row.get(key) or (self._instant(row[key]) <= instant if key != "valid_to" else self._instant(row[key]) > instant)
                        for key in ("observed_at", "valid_from", "valid_to"))]
        elif as_of:
            instant = self._instant(as_of)
            rows = [row for row in self.db.execute("SELECT * FROM assertion WHERE node_id=? AND status IN ('confirmed','superseded','stale')", (identifier,))
                    if all(not row[key] or (self._instant(row[key]) <= instant if key != "valid_to" else self._instant(row[key]) > instant)
                           for key in ("observed_at", "valid_from", "valid_to"))]
        else:
            rows = self.db.execute("SELECT * FROM assertion WHERE node_id=? AND status='confirmed' AND temperature='hot'", (identifier,))
        assertions = [decode_assertion(row) for row in rows]
        superseded = {item["supersedes"] for item in assertions if item.get("supersedes")}
        assertions = [item for item in assertions if item["id"] not in superseded]
        priorities = retrieval.get("predicate_priority", [])
        assertions.sort(key=lambda item: priorities.index(item["predicate"]) if item["predicate"] in priorities else len(priorities))
        assertions = assertions[:min(max(int(max_items), 1), 100)]
        tiers = {}
        for item in assertions:
            tier = item["provenance"]["tier"]
            tiers[tier] = tiers.get(tier, 0) + 1
        steps = [{"dimension": "C", "action": "resolve", "result": identifier},
                 {"dimension": "R", "action": "traverse", "result": len(relations["edges"])},
                 {"dimension": "L", "action": "select_logic", "result": pack.get("required_models", [])},
                 {"dimension": "T", "action": "filter", "result": as_of or "current"},
                 {"dimension": "P", "action": "verify", "result": {"tiers": tiers, "checked": len(assertions)}}]
        gaps = card.get("knowledge_gaps", [])
        return envelope({"domain": domain, "workflow": pack.get("workflow", []), "steps": steps,
                         "retrieval_profile": self.registry.retrieval_profiles.get(domain, {}), "entity_card": card,
                         "assertions": assertions, "relations": relations},
                        temporal={"as_of": as_of or "current", "recorded_as_of": recorded_as_of,
                                  "assertion_projection": "historical" if as_of or recorded_as_of else "current"}, gaps=gaps)

    def _snapshots_at(self, recorded_as_of):
        if not recorded_as_of:
            return {}
        timestamp = self._instant(recorded_as_of).astimezone(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")
        rows = self.db.execute("SELECT node_id,recorded_at,snapshot_json FROM entity_snapshot WHERE recorded_at<=? ORDER BY node_id,recorded_at DESC", (timestamp,))
        snapshots = {}
        for row in rows:
            snapshots.setdefault(row["node_id"], json.loads(row["snapshot_json"]))
        return snapshots

    def review(self, priority=None, limit=100):
        budget = self.registry.policies.get("maintenance", {}).get("budgets", {})
        limit = min(max(int(limit), 1), 1000, int(budget.get("daily_review_items", 1000)), int(budget.get("weekly_review_items", 1000)))
        sql = "SELECT * FROM review_item WHERE status='open'"
        binds = []
        if priority:
            sql += " AND priority=?"
            binds.append(priority)
        sql += " ORDER BY CASE priority WHEN 'P0' THEN 0 WHEN 'P1' THEN 1 WHEN 'P2' THEN 2 ELSE 3 END,severity DESC,created_at LIMIT ?"
        binds.append(limit)
        return envelope([{**{k: v for k, v in row.items() if k != "details_json"}, "details": json.loads(row["details_json"])}
                         for row in self.db.execute(sql, binds)])

    def changesets(self, status=None, limit=100):
        sql, binds = "SELECT * FROM changeset", []
        if status:
            sql += " WHERE status=?"
            binds.append(status)
        sql += " ORDER BY created_at DESC LIMIT ?"
        binds.append(min(max(int(limit), 1), 500))
        data = []
        for row in self.db.execute(sql, binds):
            data.append({**{k: v for k, v in row.items() if k not in ("patch_json", "operations_json", "publication_json")},
                         "patch": json.loads(row["patch_json"]), "operations": json.loads(row["operations_json"] or "[]"),
                         "publication": json.loads(row["publication_json"]) if row["publication_json"] else None})
        return envelope(data)

    def propose(self, *, actor, target_source, patch, reason, risk="normal", title=None, operations=None,
                base_revision=None, idempotency_key=None):
        if risk not in ("low", "normal", "high"):
            raise ValidationError("risk must be low, normal, or high")
        if not str(actor).strip() or not str(reason).strip():
            raise ValidationError("actor and reason are required")
        operations = operations or []
        if any((operation.get("targetKind") or operation.get("target_kind")) in ("schema", "model", "unit", "currency", "business_constraint", "business_rule") for operation in operations) or any(
            path.strip().startswith(("control/schemas/", "control/models/", "control/units/", "control/currencies/", "control/constraints/business/", "control/rules/business/")) for path in target_source.split(",")
        ):
            risk = "high"
        plan = ChangePlan(self)
        identifier, now = str(uuid.uuid4()), utcnow()
        status = "proposed" if risk == "low" else "review_required"
        with self.db.transaction():
            existing = self.db.first("SELECT * FROM changeset WHERE actor=? AND request_key=?", (actor, idempotency_key)) if idempotency_key else None
            if existing:
                if any((existing["patch_json"] != json_text(patch), existing["operations_json"] != json_text(operations),
                        existing["target_source"] != target_source, existing["risk"] != risk, existing["reason"] != reason)):
                    raise ConflictError("idempotency key was already used with different content")
                return envelope({"id": existing["id"], "status": existing["status"], "target_source": target_source,
                                 "base_revision": existing["base_revision"]})
            revision = plan.revision(target_source)
            if base_revision and base_revision not in (revision, self.registry.fingerprint):
                raise ConflictError("proposal baseline has changed; refresh and merge your draft")
            expected = plan.expectation(target_source, patch, operations)
            if plan.revision(target_source) != revision:
                raise ConflictError("source changed while preparing proposal")
            self.db.write("""INSERT INTO changeset(id,actor,title,target_source,risk,status,patch_json,operations_json,reason,created_at,base_revision,expected_json,request_key)
              VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
              (identifier, actor, title, target_source, risk, status, json_text(patch), json_text(operations), reason, now,
               revision, json_text(expected), idempotency_key))
            self.audit.stage(event_type="agent_proposal", actor=actor, target_id=identifier, source_ref=target_source,
                             before_hash=revision, reason=reason,
                             payload={"actor": actor, "risk": risk, "patch": patch, "operations": operations,
                                      "expected": expected, "title": title, "base_revision": revision})
        self.audit.flush()
        return envelope({"id": identifier, "status": status, "target_source": target_source, "base_revision": revision})

    def review_changeset(self, *, id, reviewer, decision, note=None):
        if decision not in ("approved", "rejected", "changes_requested"):
            raise ValidationError("decision must be approved, rejected, or changes_requested")
        with self.db.transaction():
            row = self.db.first("SELECT * FROM changeset WHERE id=?", (id,))
            if not row:
                raise NotFoundError(f"changeset not found: {id}")
            if row["status"] not in ("proposed", "review_required"):
                raise ValidationError(f"changeset in {row['status']} cannot be reviewed")
            if decision == "approved" and not row["expected_json"]:
                raise ValidationError("approval requires a verifiable patch")
            if decision == "approved" and row["risk"] == "high" and reviewer == row["actor"]:
                raise ValidationError("high-risk control changes require an independent reviewer")
            review_note = str(note or "").strip() or f"governance decision: {decision}"
            updated = self.db.write("UPDATE changeset SET status=?,reviewed_by=?,reviewed_at=?,review_note=?,lock_version=lock_version+1 WHERE id=? AND lock_version=?",
                                    (decision, reviewer, utcnow(), review_note, id, row["lock_version"]))
            if updated != 1:
                raise ConflictError("ChangeSet changed concurrently")
            self.audit.stage(event_type="changeset_" + decision, actor=reviewer, target_id=id,
                             source_ref=row["target_source"], reason="review decision", payload={"reviewer": reviewer, "note": review_note})
        self.audit.flush()
        return envelope({"id": id, "status": decision,
                         "note": "Approval records governance only; apply the patch to the real source of truth, then compile."})

    def publish_changeset(self, *, id, publisher, source_revision):
        plan = ChangePlan(self)
        row = self.db.first("SELECT * FROM changeset WHERE id=?", (id,))
        if not row:
            raise NotFoundError(f"changeset not found: {id}")
        if row["status"] == "published" and row["source_revision"] == source_revision:
            verification = json.loads(row["publication_json"])
            return envelope({"id": id, "status": "published", "source_revision": source_revision, "verification": verification})
        if row["status"] != "approved":
            raise ValidationError("changeset must be approved before publication")
        if not source_revision:
            raise ValidationError("source_revision is required")
        actual = plan.revision(row["target_source"])
        accepted = {actual}
        if not plan.is_git_source(row["target_source"]):
            source = self.db.first("SELECT source_hash FROM source_ref WHERE id=? OR locator=?", (row["target_source"], row["target_source"]))
            accepted.add(source["source_hash"])
        if source_revision not in accepted:
            raise ValidationError("source_revision does not match current authoritative source")
        if row["base_revision"] == actual:
            raise ValidationError("target source has not changed since ChangeSet was proposed")
        expected = json.loads(row["expected_json"])
        plan.verify(row["target_source"], expected)
        if plan.is_git_source(row["target_source"]):
            self.compile(rebuild=False)
            plan.verify(row["target_source"], expected)
        if plan.revision(row["target_source"]) != actual:
            raise ConflictError("source changed during compilation")
        verification = ({"kind": "git_authored", "verified_revision": source_revision,
                         "paths": list(plan.paths(row["target_source"]))} if plan.is_git_source(row["target_source"])
                        else {"kind": "upstream_source_system", "verified_revision": source_revision,
                              "source_ref": row["target_source"]})
        with self.db.transaction():
            current = self.db.first("SELECT status,lock_version FROM changeset WHERE id=?", (id,))
            if not current or current["status"] != "approved" or current["lock_version"] != row["lock_version"]:
                raise ConflictError("ChangeSet changed concurrently")
            if plan.revision(row["target_source"]) != actual:
                raise ConflictError("source changed during publication")
            updated = self.db.write("""UPDATE changeset SET status='published',published_by=?,published_at=?,source_revision=?,
              publication_json=?,lock_version=lock_version+1 WHERE id=? AND status='approved' AND lock_version=?""",
              (publisher, utcnow(), source_revision, json_text(verification), id, row["lock_version"]))
            if updated != 1:
                raise ConflictError("ChangeSet changed concurrently")
            self.audit.stage(event_type="changeset_published", actor=publisher, target_id=id,
                             source_ref=row["target_source"], before_hash=row["base_revision"], after_hash=source_revision,
                             reason="approved source result verified and compiled",
                             payload={"publisher": publisher, "source_revision": source_revision, "verification": verification})
        self.audit.flush()
        return envelope({"id": id, "status": "published", "source_revision": source_revision, "verification": verification})

    def apply_changeset(self, *, id, publisher):
        """Write an approved standalone control definition to the Git-authored source tree."""
        row = self.db.first("SELECT * FROM changeset WHERE id=?", (id,))
        if not row:
            raise NotFoundError(f"changeset not found: {id}")
        if row["status"] != "approved":
            raise ValidationError("changeset must be approved before source application")
        plan = ChangePlan(self)
        current_revision = plan.revision(row["target_source"])
        if row["source_revision"] and current_revision == row["source_revision"]:
            plan.verify(row["target_source"], json.loads(row["expected_json"]))
            return envelope({"id": id, "status": "approved", "source_revision": current_revision,
                             "next_action": "publish_changeset"})
        if current_revision != row["base_revision"]:
            raise ConflictError("source changed since ChangeSet approval; refresh and propose again")
        revision = plan.apply_approved_control(row["target_source"], json.loads(row["patch_json"]),
                                               json.loads(row["operations_json"]), json.loads(row["expected_json"]))
        with self.db.transaction():
            updated = self.db.write("UPDATE changeset SET source_revision=?,lock_version=lock_version+1 WHERE id=? AND status='approved' AND lock_version=?",
                                    (revision, id, row["lock_version"]))
            if updated != 1:
                raise ConflictError("ChangeSet changed concurrently during source application")
            self.audit.stage(event_type="changeset_source_applied", actor=publisher, target_id=id,
                             source_ref=row["target_source"], before_hash=row["base_revision"], after_hash=revision,
                             reason="approved control definition written to Git-authored source",
                             payload={"publisher": publisher, "source_revision": revision})
        self.audit.flush()
        return envelope({"id": id, "status": "approved", "source_revision": revision,
                         "next_action": "publish_changeset"})

    def control_plane(self):
        self.refresh_registry()
        policy_sets = {}
        for name in ("authority", "freshness", "provenance"):
            groups = [value for value in self.registry.policies.get(name, {}).values() if isinstance(value, list)]
            policy_sets[name] = {item["id"]: item for group in groups for item in group}
        policy_sets["maintenance"] = self.registry.policies.get("maintenance", {})
        return envelope({"contract_version": __contract_version__, "ontology": self.registry.ontology,
                         "predicates": self.registry._public(self.registry.predicates), "policies": policy_sets,
                         "models": self.registry._public(self.registry.models), "units": self.registry._public(self.registry.units),
                         "currencies": self.registry._public(self.registry.currencies),
                         "domains": self.registry._public(self.registry.domains),
                         "retrieval_profiles": self.registry.retrieval_profiles, "schemas": self.registry.schemas,
                         "connectors": self.registry._public(self.registry.connectors),
                         "extraction_profiles": self.registry._public(self.registry.extraction_profiles),
                         "extensions": {"constraints": list(self.registry._public(self.registry.constraints).values()),
                                        "rules": list(self.registry._public(self.registry.rules).values()),
                                        "skills": list(self.registry.skills.values())},
                         "capabilities": {"durable_writes": "changeset_only"}},
                        source={"class": "git_authored", "roots": ["control", "connectors"]})

    def agent_capabilities(self, domain=None):
        self.refresh_registry()
        data = self.agent.capabilities(domain)
        return envelope(data, source={"class": "git_authored", "roots": ["control/domains", "control/skills"]},
                        quality={"registry_fingerprint": data["registry_fingerprint"], "write_performed": False})

    def agent_skill(self, identifier):
        self.refresh_registry()
        data = self.registry.skill_bundle(identifier)
        return envelope(data, source={"class": "git_authored", "path": data["package"]["path"]},
                        quality={"portable": True, "required_files": list(data["files"]), "write_performed": False})

    def agent_request(self, **arguments):
        self.refresh_registry()
        data = self.agent.prepare(**arguments)
        return envelope(data, source={"class": "compiled_projection", "domain": data["domain"]},
                        temporal={"as_of": data["as_of"] or "current"},
                        quality={"grounding_evidence": len(data["evidence"]), "write_performed": False},
                        gaps=data.get("context", {}).get("crltp", {}).get("entity_card", {}).get("knowledge_gaps", []),
                        traces=[item["ref"] for item in data["evidence"]])

    def agent_respond(self, *, request, model_output, tool_results=None):
        self.refresh_registry()
        data = self.agent.finalize(request, model_output, tool_results)
        return envelope(data, source={"class": "model_output", "request_id": data["request_id"]},
                        quality={**data["grounding"], "write_performed": False}, gaps=data["knowledge_gaps"],
                        traces=data["grounding"]["cited_evidence_refs"])

    def agent_invoke(self, *, domain, operation, arguments=None):
        self.refresh_registry()
        return self.agent.invoke(domain=domain, operation=operation, arguments=arguments)

    def extraction_request(self, *, source, profile="default"):
        self.refresh_registry()
        from .extraction import Extraction
        data = Extraction(self, profile).prepare(source)
        return envelope(data, source={"class": data["source"]["kind"], "source_id": data["source"]["id"],
                                      "content_hash": data["source"]["content_hash"]},
                        quality={"registry_fingerprint": data["registry_fingerprint"], "write_performed": False})

    def extraction_candidates(self, *, request, model_output, profile="default"):
        self.refresh_registry()
        from .extraction import Extraction
        data = Extraction(self, profile).finalize(request, model_output)
        return envelope(data, source=data["source"], quality={"valid_candidates": len(data["candidates"]),
                                                                  "rejected_candidates": len(data["rejected"]),
                                                                  "registry_fingerprint": data["registry_fingerprint"],
                                                                  "write_performed": False},
                        gaps=data["unmapped_facts"], traces=[data["source"]["id"]])

    def studio(self):
        self.refresh_registry()
        definitions = []
        for kind, collection in (("concept", "concept_types"), ("relation", "relation_types")):
            for item in self.registry.ontology.get(collection, []):
                ontology_path = item.get("_source_path", "control/ontology/core.yaml")
                definition = {"id": item["id"], "kind": kind, "label": item.get("label", item["id"]),
                              "description": item.get("description", ""), "lifecycle": item.get("status", {}).get("lifecycle", "active"),
                              "source_path": ontology_path, "refs": 1, "files": [ontology_path], "config": {}, "read_only": False}
                if kind == "concept":
                    definition["bindings"] = [{"predicate_id": binding["predicate"], "required": binding.get("required", False),
                                               "cardinality": binding.get("cardinality", "inherit"), "group": binding.get("group", "other")}
                                              for binding in item.get("properties", [])]
                else:
                    definition["relation_mode"] = item.get("mode", "simple")
                    definition["endpoints"] = [{"source_concept_id": endpoint["source_type"], "target_concept_id": endpoint["target_type"],
                                                "source_cardinality": endpoint.get("source_cardinality", "many"), "target_cardinality": endpoint.get("target_cardinality", "many")}
                                               for endpoint in item.get("connections", [])]
                    if item.get("reification"):
                        definition["reification"] = item["reification"]
                definitions.append(definition)
        for item in self.registry.predicates.values():
            path = str(Path(item["_path"]).relative_to(self.config.root))
            config = {"value_type": item.get("value", {}).get("type"), "cardinality": item.get("value", {}).get("cardinality"),
                      "dimension": item.get("value", {}).get("dimension"), "units": item.get("value", {}).get("units"),
                      "default_unit": item.get("value", {}).get("default_unit"),
                      "minimum": item.get("value", {}).get("minimum"), "maximum": item.get("value", {}).get("maximum"),
                      "storage_mode": item.get("storage", {}).get("mode"), **item.get("policy", {}),
                      "equivalent_to": item.get("status", {}).get("equivalent_to")}
            definitions.append({"id": item["id"], "kind": "predicate", "label": item.get("label", item["id"]),
                                "description": item.get("semantics", {}).get("description") or "",
                                "lifecycle": item.get("status", {}).get("lifecycle") or "active", "source_path": path,
                                "refs": 1, "files": [path], "config": config, "read_only": False})
        policy_labels = {"authority": ("Authority policies", "Source priority and conflict handling."),
                         "freshness": ("Freshness policies", "Freshness windows for stable and operational knowledge."),
                         "provenance": ("Provenance policies", "Evidence and source requirements for provenance tiers."),
                         "maintenance": ("Maintenance policy", "Review budgets, priority thresholds, storage tiers, and externalization limits.")}
        for name, item in self.registry.policies.items():
            path = f"control/policies/{name}.yaml"
            label, description = policy_labels.get(name, (name, ""))
            definitions.append({"id": "policy:" + name, "kind": "policy", "label": label, "description": description,
                                "lifecycle": "active", "source_path": path, "refs": 1, "files": [path],
                                "config": item, "read_only": True})
        for kind, values in (("model", self.registry.models), ("domain", self.registry.domains)):
            for item in values.values():
                path = str(Path(item["_path"]).relative_to(self.config.root))
                config = {k: v for k, v in item.items() if k != "_path"}
                if kind == "model":
                    identifier, description = "model:" + item["id"], item.get("description") or ""
                    config["model_id"] = item["id"]
                else:
                    identifier, description = "domain:" + item["id"], "Domain workflow, retrieval, model, and tool policy."
                    config["retrieval_profile"] = self.registry.retrieval_profiles.get(item["id"])
                definition = {"id": identifier, "kind": kind, "label": item.get("label", item["id"]),
                              "description": description, "lifecycle": item.get("status", {}).get("lifecycle", "active"), "source_path": path, "refs": 1,
                              "files": [path], "config": config, "read_only": kind != "model"}
                if kind == "domain":
                    definition["concept_scopes"] = item.get("concept_scopes", [])
                definitions.append(definition)
        for item in self.registry.units.values():
            path = str(Path(item["_path"]).relative_to(self.config.root))
            definitions.append({"id": "unit:" + item["id"], "kind": "unit", "label": item.get("label", item["id"]),
                                "description": f"{item['dimension']} physical unit", "lifecycle": item.get("status", {}).get("lifecycle", "active"),
                                "source_path": path, "refs": 1, "files": [path],
                                "config": {k: v for k, v in item.items() if not k.startswith("_")}, "read_only": False})
        for item in self.registry.currencies.values():
            path = str(Path(item["_path"]).relative_to(self.config.root))
            definitions.append({"id": "currency:" + item["id"], "kind": "currency", "label": item.get("label", item["id"]),
                                "description": "Registered monetary currency and minor-unit precision.",
                                "lifecycle": item.get("status", {}).get("lifecycle", "active"),
                                "source_path": path, "refs": 1, "files": [path],
                                "config": {k: v for k, v in item.items() if not k.startswith("_")}, "read_only": False})
        from .business_logic import KINDS, validate_business
        for kind, items in (("business_constraint", self.registry.constraints), ("business_rule", self.registry.rules)):
            for item in items.values():
                if item.get("format") != KINDS[kind]:
                    continue
                validate_business(item, kind, self.registry.predicates, self.registry.ontology, self.registry.units)
                path = str(Path(item["_path"]).relative_to(self.config.root))
                directory = "constraints" if kind == "business_constraint" else "rules"
                if path != f"control/{directory}/business/{item['id']}.yaml":
                    raise ValidationError("business definition must be stored in its dedicated control directory")
                config = {key: value for key, value in item.items() if key not in ("_path", "label", "description", "status")}
                definitions.append({"id": kind + ":" + item["id"], "kind": kind,
                                    "label": item["label"], "description": item["description"],
                                    "lifecycle": item["status"]["lifecycle"], "source_path": path,
                                    "refs": 0, "files": [path], "config": config, "read_only": False})
        for name, item in self.registry.schemas.items():
            path = f"control/schemas/{name}.schema.json"
            base = item.get("properties", {}).get("base", {})
            node = base.get("properties", {}).get("node", {})
            knowledge = item.get("properties", {}).get("knowledge", {})
            config = {"version": base.get("properties", {}).get("schema", {}).get("properties", {}).get("ckm", {}).get("const"),
                      "required_root": item.get("required", []), "required_base": base.get("required", []),
                      "required_node": node.get("required", []), "required_knowledge": knowledge.get("required", []),
                      "external_assertions": "assertions_ref" in item.get("properties", {}).get("external", {}).get("properties", {})}
            definitions.append({"id": "schema:" + name.replace("-", "_"), "kind": "schema", "label": item.get("title", name),
                                "description": item.get("description") or "", "lifecycle": "active", "source_path": path,
                                "refs": 1, "files": [path], "config": config, "read_only": False})
        for path in sorted((self.config.root / "connectors").glob("**/*.mapping.yaml")):
            from .core import load_yaml
            mapping = load_yaml(path)
            name = path.name.removesuffix(".mapping.yaml")
            definitions.append({"id": "connector:" + name.replace("-", "_"), "kind": "connector", "label": name,
                                "description": f"Deterministic mapping from {mapping.get('source_system')} {mapping.get('source_object')} into the canonical contract.",
                                "lifecycle": "active", "source_path": str(path.relative_to(self.config.root)), "refs": 1,
                                "files": [str(path.relative_to(self.config.root))], "config": mapping, "read_only": True})
        coverage = {}
        for item in definitions:
            coverage[item["kind"]] = coverage.get(item["kind"], 0) + 1
        data = {"contract_version": __contract_version__, "registry_fingerprint": self.registry.fingerprint,
                "definitions": definitions, "coverage": coverage, "extensions": {"constraints": len(self.registry.constraints),
                                                                                      "rules": len(self.registry.rules), "skills": len(self.registry.skills)},
                "capabilities": {"durable_writes": "changeset_only", "review_decisions": ["approved", "rejected", "changes_requested"]},
                "changesets": self.changesets()["data"]}
        result = envelope(data, source={"class": "git_authored", "roots": ["control", "connectors"]}, quality={"registry_valid": True})
        import jsonschema
        jsonschema.validate(result, self.registry.schemas["studio-snapshot"])
        return result

    def preview_business(self, definition, kind, facts):
        from .business_logic import evaluate_business
        result = evaluate_business(definition, kind, facts, self.registry.predicates, self.registry.ontology, self.registry.units)
        return envelope(result, quality={"preview_only": True, "durable_write": False})

    def _business_facts_for(self, definition, node_id, role, as_of):
        node = self.db.first("SELECT id,type,attrs_json,source_path,source_hash FROM node WHERE id=?", (node_id,))
        if not node:
            raise NotFoundError(f"node not found: {node_id}")
        if node["type"] != definition["scope"][f"{role}_concept"]:
            raise ValidationError(f"{node_id} is not in the {role} concept scope")
        attrs = json.loads(node["attrs_json"])
        facts, refs = {"id": node_id}, {}
        for item in definition["inputs"]:
            if item["role"] != role:
                continue
            predicate = item["predicate"]
            if predicate in attrs:
                facts[predicate] = attrs[predicate]
                refs[predicate] = {"source_path": node["source_path"], "source_hash": node["source_hash"]}
            else:
                try:
                    row = self._effective_assertion(node_id, predicate, as_of)
                    facts[predicate] = json.loads(row["value_json"])
                    refs[predicate] = self._assertion_ref(row)
                except ValidationError as exc:
                    if not str(exc).startswith("no confirmed "):
                        raise
        return facts, refs

    def _business_candidate_ids(self, definition, candidate_ids):
        if candidate_ids is not None and (not isinstance(candidate_ids, list) or len(candidate_ids) > 500 or any(not isinstance(item, str) for item in candidate_ids) or len(set(candidate_ids)) != len(candidate_ids)):
            raise ValidationError("business rule accepts up to 500 unique candidate ids")
        if candidate_ids is None:
            rows = self.db.execute("SELECT id FROM node WHERE type=? AND lifecycle='active' ORDER BY id LIMIT 501",
                                   (definition["scope"]["candidate_concept"],))
            if len(rows) > 500:
                raise ValidationError("candidate scope exceeds 500 entities; provide an explicit candidate list")
            candidate_ids = [row["id"] for row in rows]
        return candidate_ids

    def impact_business(self, definition, kind, subject_id=None, candidate_ids=None, as_of=None):
        from .business_logic import evaluate_business, validate_business
        validate_business(definition, kind, self.registry.predicates, self.registry.ontology, self.registry.units)
        as_of = as_of or utcnow()
        if kind == "business_constraint":
            rows = self.db.execute("SELECT id FROM node WHERE type=? AND lifecycle='active' ORDER BY id LIMIT 501",
                                   (definition["scope"]["subject_concept"],))
            if len(rows) > 500:
                raise ValidationError("constraint impact scope exceeds 500 entities")
            results, refs = [], {}
            for row in rows:
                facts, source_refs = self._business_facts_for(definition, row["id"], "subject", as_of)
                outcome = evaluate_business(definition, kind, {"subject": facts}, self.registry.predicates,
                                            self.registry.ontology, self.registry.units)
                results.append({"entity_id": row["id"], **outcome["results"][0]})
                refs[row["id"]] = source_refs
            return envelope({"definition_id": definition["id"], "version": definition["version"], "kind": kind,
                             "as_of": as_of, "results": results, "source_refs": refs,
                             "verdict_counts": {verdict: sum(item["verdict"] == verdict for item in results)
                                                for verdict in ("valid", "invalid", "unknown")}},
                            quality={"impact_only": True, "durable_write": False})
        if not str(subject_id or "").strip():
            raise ValidationError("business rule impact requires a subject entity id")
        subject, subject_refs = self._business_facts_for(definition, subject_id, "subject", as_of)
        candidate_ids = self._business_candidate_ids(definition, candidate_ids)
        candidates, candidate_refs = [], {}
        for node_id in candidate_ids:
            facts, refs = self._business_facts_for(definition, node_id, "candidate", as_of)
            candidates.append(facts)
            candidate_refs[node_id] = refs
        result = evaluate_business(definition, kind, {"subject": subject, "candidates": candidates},
                                   self.registry.predicates, self.registry.ontology, self.registry.units)
        result["as_of"] = as_of
        result["source_refs"] = {"subject": subject_refs, "candidates": candidate_refs}
        result["verdict_counts"] = {verdict: sum(item["verdict"] == verdict for item in result["results"])
                                    for verdict in ("eligible", "not_matched", "condition_failed", "unknown")}
        return envelope(result, quality={"impact_only": True, "durable_write": False})

    def evaluate_business_definition(self, kind, identifier, subject_id, candidate_ids=None, as_of=None):
        from .business_logic import evaluate_business, validate_business
        if kind not in ("business_constraint", "business_rule"):
            raise ValidationError("unknown business definition kind")
        collection = self.registry.constraints if kind == "business_constraint" else self.registry.rules
        definition = collection.get(identifier)
        if not definition:
            raise NotFoundError(f"business definition not found: {identifier}")
        validate_business(definition, kind, self.registry.predicates, self.registry.ontology, self.registry.units)
        if definition["status"]["lifecycle"] != "active":
            raise ValidationError("only active business definitions may evaluate entity data")
        if kind == "business_rule":
            candidate_ids = self._business_candidate_ids(definition, candidate_ids)
        as_of = as_of or utcnow()
        subject, subject_refs = self._business_facts_for(definition, subject_id, "subject", as_of)
        candidates, candidate_refs = [], {}
        for node_id in candidate_ids or []:
            facts, refs = self._business_facts_for(definition, node_id, "candidate", as_of)
            candidates.append(facts)
            candidate_refs[node_id] = refs
        result = evaluate_business(definition, kind, {"subject": subject, "candidates": candidates},
                                   self.registry.predicates, self.registry.ontology, self.registry.units)
        result["as_of"] = as_of
        result["source_refs"] = {"subject": subject_refs, "candidates": candidate_refs}
        if result["relation_type"]:
            result["derived_edges"] = [{"source": subject_id, "relation": result["relation_type"], "target": node_id}
                                       for node_id in result["eligible_candidate_ids"]]
        return envelope(result, quality={"deterministic": True, "definition_version": definition["version"]})

    def _refresh_temperatures(self, identifier=None):
        rows = self.db.execute("SELECT id,node_id,predicate,status,observed_at,valid_from,valid_to,temperature FROM assertion" + (" WHERE node_id=?" if identifier else ""),
                               (identifier,) if identifier else ())
        changed = set()
        for row in rows:
            temperature = self.registry.temperature(row)
            if temperature != row["temperature"]:
                self.db.write("UPDATE assertion SET temperature=? WHERE id=?", (temperature, row["id"]))
                changed.add(row["node_id"])
        if changed:
            compiler = Compiler(self.config, self.registry, self.db, self.ledger)
            with self.db.transaction():
                compiler._refresh_cards(changed)
