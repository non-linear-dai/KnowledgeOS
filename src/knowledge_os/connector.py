"""NDJSON enterprise connector with durable records and replay."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path

from .compiler import Compiler
from .core import ConflictError, ValidationError, digest, json_text, load_yaml, utcnow
from .values import validate_value


def field(record, dotted):
    value = record
    for key in dotted.split("."):
        if not isinstance(value, dict) or key not in value:
            raise ValidationError(f"connector field not found: {dotted}")
        value = value[key]
    return value


class Connector:
    def __init__(self, service):
        self.service = service
        self.db = service.db
        self.registry = service.registry
        self.audit = service.audit

    def ingest_ndjson(self, input_path, mapping_path, isolate_errors=False):
        mapping = load_yaml(Path(mapping_path))
        records = [json.loads(line) for line in Path(input_path).read_text().splitlines() if line.strip()]
        counters = {"records": len(records), "ingested": 0, "skipped": 0, "rejected": []}
        for record in records:
            record_id = None
            try:
                record_id = str(field(record, mapping["record_id_field"]))
                version = str(field(record, mapping["version_field"])) if mapping.get("version_field") else None
                source_hash = digest(record)
                source_ref_id = ":".join((mapping["source_system"], mapping["source_object"], record_id))
                existing = self.db.first("SELECT source_hash,source_version,source_timestamp FROM source_ref WHERE id=?", (source_ref_id,))
                if existing and existing["source_hash"] == source_hash and (existing["source_version"] or "") == (version or ""):
                    counters["skipped"] += 1
                    continue
                if existing and mapping.get("timestamp_field"):
                    incoming = datetime.fromisoformat(str(field(record, mapping["timestamp_field"])).replace("Z", "+00:00"))
                    previous = datetime.fromisoformat(existing["source_timestamp"].replace("Z", "+00:00"))
                    if incoming < previous:
                        counters["skipped"] += 1
                        continue
                    if incoming == previous and source_hash != existing["source_hash"]:
                        if not (version and version.isdigit() and str(existing["source_version"]).isdigit() and int(version) > int(existing["source_version"])):
                            raise ConflictError("conflicting connector version at same timestamp")
                self.ingest_record(mapping, record, record_id, version, source_hash, source_ref_id)
                counters["ingested"] += 1
            except (ValidationError, ConflictError) as exc:
                if not isolate_errors:
                    raise
                counters["rejected"].append({"record_id": record_id, "error": str(exc)})
        return counters

    def replay(self):
        rows = self.db.execute("SELECT * FROM connector_record ORDER BY observed_at,source_ref_id,CAST(version AS INTEGER)")
        for row in rows:
            mapping, record = json.loads(row["mapping_json"]), json.loads(row["record_json"])
            self.ingest_record(mapping, record, str(field(record, mapping["record_id_field"])), row["version"],
                               row["source_hash"], row["source_ref_id"], replay=True)

    def ingest_record(self, mapping, record, record_id, version, source_hash, source_ref_id, replay=False):
        now = utcnow()
        timestamp = str(field(record, mapping["timestamp_field"])) if mapping.get("timestamp_field") else now
        authority = mapping.get("authority_class", mapping["source_system"])
        node_map = mapping["node"]
        node_id = node_map["id_prefix"] + ":" + str(field(record, node_map["id_field"]))
        with self.db.transaction():
            if not replay:
                self.db.write("INSERT OR IGNORE INTO connector_record(source_ref_id,source_hash,version,observed_at,record_json,mapping_json) VALUES(?,?,?,?,?,?)",
                              (source_ref_id, source_hash, version, timestamp, json_text(record), json_text(mapping)))
            self.db.write("""INSERT INTO source_ref(id,source_system,source_object,source_record_id,source_timestamp,source_version,source_hash,
              authority_class,locator,captured_at,metadata_json) VALUES(?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET
              source_timestamp=excluded.source_timestamp,source_version=excluded.source_version,source_hash=excluded.source_hash,
              authority_class=excluded.authority_class,locator=excluded.locator,captured_at=excluded.captured_at""",
              (source_ref_id, mapping["source_system"], mapping["source_object"], record_id, timestamp, version, source_hash,
               authority, str(mapping.get("locator_prefix") or "") + record_id, now, "{}"))
            if mapping.get("deleted_field") and record.get(mapping["deleted_field"]) is True:
                self.db.write("DELETE FROM attribute_candidate WHERE source_ref_id=?", (source_ref_id,))
                self.db.write("UPDATE assertion SET status='superseded',temperature='warm',valid_to=? WHERE source_path=? AND (valid_to IS NULL OR valid_to='')",
                              (timestamp, "connector:" + source_ref_id))
                if self.db.first("SELECT id FROM node WHERE id=?", (node_id,)):
                    for predicate_id in (mapping.get("attrs") or {}):
                        self._effective_attr(node_id, predicate_id)
                self.db.write("UPDATE node SET lifecycle='retired' WHERE id=? AND source_path=?", (node_id, "connector:" + source_ref_id))
                event_type = "connector_delete"
            else:
                label = str(field(record, node_map["label_field"]))
                natural_key = str(field(record, node_map["key_field"]))
                existing = self.db.first("SELECT id,type,source_class FROM node WHERE id=?", (node_id,))
                if existing and existing["type"] != node_map["type"]:
                    raise ValidationError(f"connector identity type conflict for {node_id}")
                if not existing:
                    if node_map["type"] not in {item["id"] for item in self.registry.ontology.get("concept_types", [])}:
                        raise ValidationError(f"unknown connector node type {node_map['type']}")
                    self.db.write("""INSERT INTO node(id,kind,type,natural_key,label,key_namespace,aliases_json,tags_json,attrs_json,
                      lifecycle,revision,source_class,source_path,source_hash,narrative,compiled_at) VALUES(?,?,?,?,?,?,'[]','[]','{}','active',1,?,?,?,'',?)""",
                      (node_id, node_map.get("kind", "entity"), node_map["type"], natural_key, label,
                       node_map.get("key_namespace", mapping["source_system"]), authority, "connector:" + source_ref_id, source_hash, now))
                elif existing["source_class"] != "git_authored":
                    self.db.write("UPDATE node SET label=?,source_hash=?,compiled_at=?,lifecycle='active' WHERE id=?", (label, source_hash, now, node_id))
                concept = next(item for item in self.registry.ontology.get("concept_types", []) if item["id"] == node_map["type"])
                bound = {item["predicate"] for item in concept.get("properties", [])}
                business_facts = {}
                for predicate_id, dotted in (mapping.get("attrs") or {}).items():
                    predicate = self.registry.validate_predicate(predicate_id)
                    if bound and predicate_id not in bound:
                        raise ValidationError(f"{predicate_id} is not bound to {node_map['type']}")
                    if predicate.get("storage", {}).get("mode") != "attr":
                        raise ValidationError(f"{predicate_id} is not an attr")
                    value = validate_value(predicate, field(record, dotted), self.registry.units, self.registry.currencies)
                    business_facts[predicate_id] = value
                    self.db.write("""INSERT INTO attribute_candidate(node_id,predicate,source_ref_id,source_class,value_json,observed_at,source_hash)
                      VALUES(?,?,?,?,?,?,?) ON CONFLICT(node_id,predicate,source_ref_id) DO UPDATE SET value_json=excluded.value_json,
                      observed_at=excluded.observed_at,source_hash=excluded.source_hash,source_class=excluded.source_class""",
                      (node_id, predicate_id, source_ref_id, authority, json_text(value), timestamp, source_hash))
                    self._effective_attr(node_id, predicate_id)
                for predicate_id, definition in (mapping.get("assertions") or {}).items():
                    predicate = self.registry.validate_predicate(predicate_id)
                    if bound and predicate_id not in bound:
                        raise ValidationError(f"{predicate_id} is not bound to {node_map['type']}")
                    raw = field(record, definition["field"])
                    value = raw if isinstance(raw, dict) else {"type": predicate["value"]["type"], "literal": raw}
                    if definition.get("unit") or definition.get("unit_field"):
                        value["unit"] = definition.get("unit") or field(record, definition["unit_field"])
                    value = validate_value(predicate, value, self.registry.units, self.registry.currencies)
                    business_facts[predicate_id] = value
                    qualifiers = {key: field(record, spec["field"]) if isinstance(spec, dict) and "field" in spec else spec
                                  for key, spec in (definition.get("qualifiers") or {}).items()}
                    if predicate_id == "exchange_rate":
                        from .fx import validate_exchange_rate
                        validate_exchange_rate({"value": value, "qualifiers": qualifiers}, self.registry.currencies)
                    assertion_id = hashlib.sha256(f"{source_ref_id}:{predicate_id}:{source_hash}".encode()).hexdigest()
                    prior = self.db.first("SELECT id FROM assertion WHERE node_id=? AND predicate=? AND source_path=? AND status='confirmed' ORDER BY observed_at DESC LIMIT 1",
                                          (node_id, predicate_id, "connector:" + source_ref_id))
                    auto = predicate.get("policy", {}).get("write") == "auto"
                    if prior and prior["id"] != assertion_id and auto:
                        self.db.write("UPDATE assertion SET status='superseded',temperature='warm',valid_to=? WHERE id=?", (timestamp, prior["id"]))
                    status = "confirmed" if auto else "proposed"
                    temperature = self.registry.temperature({"predicate": predicate_id, "status": status, "observed_at": timestamp})
                    self.db.write("""INSERT OR IGNORE INTO assertion(id,node_id,predicate,value_json,qualifiers_json,observed_at,valid_from,valid_to,
                      assertion_kind,status,confidence,evidence_refs_json,source_refs_json,supersedes,provenance_tier,temperature,source_path,source_hash)
                      VALUES(?,?,?,?,?,?,?,NULL,?,?,1.0,?,?,?,?,?,?,?)""",
                      (assertion_id, node_id, predicate_id, json_text(value), json_text(qualifiers), timestamp, timestamp,
                       definition.get("kind", "measurement"), status, json_text(["snapshot:" + source_hash]), json_text([source_ref_id]),
                       prior["id"] if prior else None, predicate.get("policy", {}).get("provenance_tier", "B"), temperature,
                       "connector:" + source_ref_id, source_hash))
                from .business_logic import evaluate_business
                for constraint in self.registry.constraints.values():
                    if constraint.get("format") != "knowledgeos.business-constraint.v1" or constraint.get("status", {}).get("lifecycle") != "active" or constraint.get("scope", {}).get("subject_concept") != node_map["type"]:
                        continue
                    if not all(item["predicate"] in business_facts for item in constraint["inputs"]):
                        continue
                    verdict = evaluate_business(constraint, "business_constraint", {"subject": business_facts},
                                                self.registry.predicates, self.registry.ontology, self.registry.units)["results"][0]["verdict"]
                    if verdict != "valid":
                        raise ValidationError(f"connector record {record_id}: business constraint {constraint['id']} is {verdict}")
                event_type = "connector_ingest"
            if not replay:
                self.audit.stage(event_type=event_type, actor="connector", target_id=node_id, source_ref=source_ref_id,
                                 after_hash=source_hash, reason="enterprise source delta", payload={"version": version, "record_id": record_id},
                                 event_id=digest(f"connector:{source_ref_id}:{version}:{source_hash}"))
        if not replay:
            compiler = Compiler(self.service.config, self.registry, self.db, self.service.ledger)
            with self.db.transaction():
                compiler._refresh_cards({node_id})
            self.audit.flush()

    def _effective_attr(self, node_id, predicate_id):
        row = self.db.first("SELECT attrs_json FROM node WHERE id=?", (node_id,))
        attrs = json.loads(row["attrs_json"])
        candidates = self.db.execute("SELECT * FROM attribute_candidate WHERE node_id=? AND predicate=?", (node_id, predicate_id))
        policy_id = self.registry.predicates[predicate_id].get("policy", {}).get("authority")
        policies = self.registry.policies.get("authority", {}).get("authority_policies", [])
        policy = next((p for p in policies if p["id"] == policy_id), {})
        def rank(item):
            for index, group in enumerate(("primary", "secondary", "supporting")):
                if item["source_class"] in policy.get(group, []):
                    return index * 100 + policy[group].index(item["source_class"])
            return 1000
        if candidates:
            chosen = min(candidates, key=lambda item: (rank(item), item["source_ref_id"]))
            attrs[predicate_id] = json.loads(chosen["value_json"])
        else:
            attrs.pop(predicate_id, None)
        self.db.write("UPDATE node SET attrs_json=? WHERE id=?", (json_text(attrs), node_id))
