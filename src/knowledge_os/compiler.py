"""Validate Git-authored knowledge and build disposable SQLite projections."""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

import jsonschema

from .core import Config, Registry, ValidationError, digest, frontmatter, json_text, utcnow
from .storage import Audit, Database, Ledger
from .semantic import SemanticIndex
from .values import validate_value


def decode_assertion(row: dict) -> dict:
    return {"id": row["id"], "node_id": row["node_id"], "predicate": row["predicate"],
            "value": json.loads(row["value_json"]), "qualifiers": json.loads(row["qualifiers_json"]),
            "temporal": {key: row[key] for key in ("observed_at", "valid_from", "valid_to")},
            "epistemic": {"assertion_kind": row["assertion_kind"], "status": row["status"], "confidence": row["confidence"]},
            "provenance": {"tier": row["provenance_tier"], "evidence_refs": json.loads(row["evidence_refs_json"]), "source_refs": json.loads(row["source_refs_json"])},
            "supersedes": row["supersedes"], "temperature": row["temperature"]}


def card_assertion(row: dict) -> dict:
    return {"id": row["id"], "predicate": row["predicate"], "value": json.loads(row["value_json"]),
            "observed_at": row["observed_at"], "valid_from": row["valid_from"], "valid_to": row["valid_to"],
            "assertion_kind": row["assertion_kind"], "status": row["status"], "confidence": row["confidence"],
            "provenance_tier": row["provenance_tier"], "temperature": row["temperature"]}


class Compiler:
    def __init__(self, config: Config, registry: Registry | None = None, database: Database | None = None,
                 ledger: Ledger | None = None):
        self.config = config
        self.registry = registry or Registry(config)
        self.db = database or Database(config)
        self.ledger = ledger or Ledger(config)
        self.own_db, self.own_ledger = database is None, ledger is None
        self.audit = Audit(self.db, self.ledger)

    def close(self):
        if self.own_db:
            self.db.close()
        if self.own_ledger:
            self.ledger.close()

    def _assertions(self, path: Path, data: dict) -> list:
        assertions = list(data.get("knowledge", {}).get("assertions") or [])
        ref = data.get("external", {}).get("assertions_ref")
        if ref:
            target = (path.parent / ref).resolve()
            if not target.is_relative_to(self.config.knowledge.resolve()) or not target.is_file():
                raise ValidationError(f"{path}: assertions_ref escapes knowledge root or is missing")
            try:
                assertions.extend(json.loads(line) for line in target.read_text().splitlines() if line.strip())
            except json.JSONDecodeError as exc:
                raise ValidationError(f"{target}: invalid NDJSON: {exc}") from exc
        return assertions

    def validate(self, path: Path, data: dict, assertions: list):
        schema = self.registry.schemas.get("canonical-node")
        if schema:
            try:
                jsonschema.validate({**data, "knowledge": {**data.get("knowledge", {}), "assertions": assertions}}, schema,
                                    format_checker=jsonschema.FormatChecker())
            except jsonschema.ValidationError as exc:
                raise ValidationError(f"{path}: {exc.message}") from exc
        base, knowledge = data.get("base", {}), data.get("knowledge", {})
        node = base.get("node", {})
        if not node.get("id") or not node.get("type") or not node.get("label"):
            raise ValidationError(f"{path}: node id, type and label are required")
        concepts = {item["id"] for item in self.registry.ontology.get("concept_types", [])}
        if node["type"] not in concepts:
            raise ValidationError(f"{path}: unknown ontology type {node['type']}")
        if base.get("lifecycle", {}).get("state") not in ("draft", "active", "deprecated", "merged", "retired"):
            raise ValidationError(f"{path}: invalid lifecycle")
        attrs = knowledge.get("attrs") or {}
        concept = next(item for item in self.registry.ontology.get("concept_types", []) if item["id"] == node["type"])
        bindings = {item["predicate"]: item for item in concept.get("properties", [])}
        for identifier in attrs:
            predicate = self.registry.validate_predicate(identifier)
            if predicate.get("storage", {}).get("mode") != "attr":
                raise ValidationError(f"{path}: {identifier} is not an attribute")
            if bindings and identifier not in bindings:
                raise ValidationError(f"{path}: predicate {identifier} is not declared for concept {node['type']}")
            validate_value(predicate, attrs[identifier], self.registry.units, self.registry.currencies)
        for item in assertions:
            if not isinstance(item, dict) or not item.get("id") or not item.get("predicate"):
                raise ValidationError(f"{path}: assertion requires id and predicate")
            predicate = self.registry.validate_predicate(item["predicate"])
            if predicate.get("storage", {}).get("mode") not in ("assertion", "external"):
                raise ValidationError(f"{path}: {item['predicate']} is not an assertion")
            if bindings and item["predicate"] not in bindings:
                raise ValidationError(f"{path}: predicate {item['predicate']} is not declared for concept {node['type']}")
            validate_value(predicate, item.get("value"), self.registry.units, self.registry.currencies)
            if item["predicate"] == "exchange_rate":
                from .fx import validate_exchange_rate
                validate_exchange_rate(item, self.registry.currencies)
            if item.get("epistemic", {}).get("status") == "confirmed":
                tier = predicate.get("policy", {}).get("provenance_tier", "C")
                policies = self.registry.policies.get("provenance", {}).get("provenance_policies", [])
                policy = next((p for p in policies if p["id"] == tier), {})
                provenance = item.get("provenance") or {}
                if policy.get("source_ref_required") and not provenance.get("source_refs"):
                    raise ValidationError(f"{path}: assertion {item['id']} requires source_refs")
                if policy.get("evidence_or_snapshot_required") and not provenance.get("evidence_refs"):
                    raise ValidationError(f"{path}: assertion {item['id']} requires evidence_refs")
                if policy.get("locator_required") and not any(re.match(r"^[a-z][a-z0-9+.-]*:", str(ref), re.I) for ref in provenance.get("source_refs") or []):
                    raise ValidationError(f"{path}: assertion {item['id']} requires a source locator")
            temporal = item.get("temporal") or {}
            if predicate.get("policy", {}).get("temporal") == "required" and not any(temporal.get(key) for key in ("observed_at", "valid_from", "valid_to")):
                raise ValidationError(f"{path}: {item['predicate']} requires temporal metadata")
            for key, value in temporal.items():
                if value:
                    try:
                        from datetime import datetime
                        datetime.fromisoformat(str(value).replace("Z", "+00:00"))
                    except ValueError as exc:
                        raise ValidationError(f"{path}: invalid {key} timestamp {value}") from exc
        used = set(attrs) | {item["predicate"] for item in assertions}
        for identifier, binding in bindings.items():
            if binding.get("required") and identifier not in used:
                raise ValidationError(f"{path}: concept {node['type']} requires predicate {identifier}")
            cardinality = binding.get("cardinality", "inherit")
            if cardinality == "inherit":
                cardinality = self.registry.predicates[identifier].get("value", {}).get("cardinality")
            if cardinality == "one":
                count = (len(attrs[identifier]) if isinstance(attrs.get(identifier), list) else 1 if identifier in attrs else 0)
                count += sum(item["predicate"] == identifier for item in assertions)
                if count > 1:
                    raise ValidationError(f"{path}: {identifier} allows at most one value")
        relation_definitions = {item["id"]: item for item in self.registry.ontology.get("relation_types", [])}
        for relation in knowledge.get("relations") or []:
            if relation.get("predicate") not in relation_definitions:
                raise ValidationError(f"{path}: unknown relation {relation.get('predicate')}")
            definition = relation_definitions[relation["predicate"]]
            if definition.get("mode", "simple") == "reified" and not relation.get("id"):
                raise ValidationError(f"{path}: reified relation {relation['predicate']} requires id")
            if definition.get("mode", "simple") == "simple" and relation.get("id"):
                raise ValidationError(f"{path}: simple relation {relation['predicate']} cannot have id")
            if definition.get("connections") and node["type"] not in {edge["source_type"] for edge in definition["connections"]}:
                raise ValidationError(f"{path}: relation {relation['predicate']} does not allow source type {node['type']}")
        for definition in self.registry.constraints.values():
            if definition.get("format") == "knowledgeos.business-constraint.v1" and definition.get("status", {}).get("lifecycle") == "active" and definition.get("scope", {}).get("subject_concept") == node["type"]:
                from .business_logic import evaluate_business
                facts = dict(attrs)
                from datetime import datetime, timezone
                instant = datetime.now(timezone.utc)
                floor = datetime.min.replace(tzinfo=timezone.utc)
                for binding in definition["inputs"]:
                    predicate = binding["predicate"]
                    candidates = []
                    for assertion in assertions:
                        if assertion.get("predicate") != predicate or assertion.get("epistemic", {}).get("status") != "confirmed":
                            continue
                        temporal = assertion.get("temporal") or {}
                        def parse(value):
                            if not value:
                                return floor
                            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
                            if parsed.tzinfo is None:
                                raise ValidationError(f"{path}: business constraint facts require timezone-aware timestamps")
                            return parsed
                        if temporal.get("observed_at") and parse(temporal["observed_at"]) > instant:
                            continue
                        if temporal.get("valid_from") and parse(temporal["valid_from"]) > instant:
                            continue
                        if temporal.get("valid_to") and parse(temporal["valid_to"]) <= instant:
                            continue
                        candidates.append((parse(temporal.get("valid_from")), parse(temporal.get("observed_at")), assertion))
                    candidates.sort(key=lambda item: (item[0], item[1]), reverse=True)
                    if len(candidates) > 1 and candidates[0][:2] == candidates[1][:2]:
                        raise ValidationError(f"{path}: ambiguous confirmed {predicate} facts for business constraint")
                    if candidates:
                        facts[predicate] = candidates[0][2].get("value")
                outcome = evaluate_business(definition, "business_constraint", {"subject": facts},
                                            self.registry.predicates, self.registry.ontology, self.registry.units)
                verdict = outcome["results"][0]["verdict"]
                if verdict != "valid":
                    raise ValidationError(f"{path}: business constraint {definition['id']} is {verdict}")
            for rule in definition.get("document", {}).get("unique", []):
                values = data
                for key in rule["path"].split("."):
                    values = values.get(key, []) if isinstance(values, dict) else []
                seen = set()
                for item in values or []:
                    value = item.get(rule["key"]) if isinstance(item, dict) else None
                    if rule.get("ignore_blank") and not value:
                        continue
                    if value in seen:
                        raise ValidationError(f"{path}: duplicate {rule['path']}.{rule['key']} {value}")
                    seen.add(value)
        assertion_ids = [item["id"] for item in assertions]
        if len(assertion_ids) != len(set(assertion_ids)):
            raise ValidationError(f"{path}: duplicate assertion id")
        for model in knowledge.get("logic_refs") or []:
            if model not in self.registry.models:
                raise ValidationError(f"{path}: unknown logic model {model}")

    def compile(self, rebuild=False) -> dict:
        self.registry.reload()
        paths = sorted(self.config.knowledge.rglob("*.md"))
        previous = self.db.first("SELECT value FROM metadata WHERE key='control_fingerprint'")
        control_changed = not previous or previous["value"] != self.registry.fingerprint
        changed, skipped = [], []
        present = {str(path.relative_to(self.config.root)) for path in paths}
        impacted = set()
        with self.db.transaction():
            if rebuild:
                self.db.reset_projection()
            else:
                for row in self.db.execute("SELECT id,source_path FROM node WHERE source_class='git_authored'"):
                    if row["source_path"] not in present:
                        impacted.add(row["id"])
                        self.db.write("DELETE FROM node WHERE id=?", (row["id"],))
                        self.db.write("DELETE FROM metadata WHERE key=?", ("source:" + row["source_path"],))
            for path in paths:
                data, narrative = frontmatter(path)
                assertions = self._assertions(path, data)
                source_hash = hashlib.sha256((json_text(data, sorted_keys=True) + narrative + json_text(assertions, sorted_keys=True)).encode()).hexdigest()
                rel = str(path.relative_to(self.config.root))
                prior = self.db.first("SELECT value FROM metadata WHERE key=?", ("source:" + rel,))
                if not rebuild and not control_changed and prior and prior["value"] == source_hash:
                    skipped.append(rel)
                    continue
                self.validate(path, data, assertions)
                self._project(rel, data, narrative, assertions, source_hash)
                impacted.add(data["base"]["node"]["id"])
                self.db.write("INSERT INTO metadata(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", ("source:" + rel, source_hash))
                changed.append(rel)
                self.audit.stage(event_type="git_publication", actor="compiler", target_id=rel, source_ref=rel,
                                 after_hash=source_hash, reason="index rebuild" if rebuild else "incremental compile",
                                 payload={"source_path": rel}, event_id=digest(f"git_publication:{rel}:{source_hash}"))
            if rebuild:
                from .connector import Connector
                class _Service:
                    pass
                shell = _Service()
                shell.config, shell.registry, shell.db, shell.ledger, shell.audit = self.config, self.registry, self.db, self.ledger, self.audit
                Connector(shell).replay()
            self._validate_edges()
            if rebuild or control_changed:
                self._validate_connector_business_constraints()
            impacted.update(self._reclassify())
            if rebuild or control_changed:
                self._refresh_cards()
            elif impacted:
                self._refresh_cards(impacted)
            self._scan_maintenance()
            generation = utcnow()
            self.db.write("INSERT INTO metadata(key,value) VALUES('index_generation',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (generation,))
            self.db.write("INSERT INTO metadata(key,value) VALUES('control_fingerprint',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (self.registry.fingerprint,))
            self.audit.stage(event_type="index_rebuild" if rebuild else "index_compile", actor="compiler", target_id="knowledge.index.db",
                             reason="rebuild requested" if rebuild else "incremental compile requested",
                             payload={"changed": changed, "skipped": skipped, "files": len(paths)})
        self.audit.flush()
        return {"files": len(paths), "changed": changed, "skipped": skipped, "rebuild": bool(rebuild)}

    def _validate_connector_business_constraints(self):
        from .business_logic import evaluate_business
        from datetime import datetime, timezone
        instant = datetime.now(timezone.utc)
        for constraint in self.registry.constraints.values():
            if constraint.get("format") != "knowledgeos.business-constraint.v1" or constraint.get("status", {}).get("lifecycle") != "active":
                continue
            for node in self.db.execute("SELECT id,attrs_json FROM node WHERE type=? AND lifecycle='active' AND source_class!='git_authored' ORDER BY id",
                                        (constraint["scope"]["subject_concept"],)):
                facts = json.loads(node["attrs_json"])
                for binding in constraint["inputs"]:
                    predicate = binding["predicate"]
                    if predicate in facts:
                        continue
                    candidates = []
                    for assertion in self.db.execute("SELECT value_json,observed_at,valid_from,valid_to FROM assertion WHERE node_id=? AND predicate=? AND status='confirmed'",
                                                     (node["id"], predicate)):
                        def parse(value):
                            if not value:
                                return datetime.min.replace(tzinfo=timezone.utc)
                            result = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
                            if result.tzinfo is None:
                                raise ValidationError("business constraint facts require timezone-aware timestamps")
                            return result
                        if assertion["observed_at"] and parse(assertion["observed_at"]) > instant:
                            continue
                        if assertion["valid_from"] and parse(assertion["valid_from"]) > instant:
                            continue
                        if assertion["valid_to"] and parse(assertion["valid_to"]) <= instant:
                            continue
                        candidates.append((parse(assertion["valid_from"]), parse(assertion["observed_at"]), assertion))
                    candidates.sort(key=lambda item: (item[0], item[1]), reverse=True)
                    if len(candidates) > 1 and candidates[0][:2] == candidates[1][:2]:
                        raise ValidationError(f"ambiguous confirmed {predicate} facts for business constraint on {node['id']}")
                    if candidates:
                        facts[predicate] = json.loads(candidates[0][2]["value_json"])
                verdict = evaluate_business(constraint, "business_constraint", {"subject": facts},
                                            self.registry.predicates, self.registry.ontology, self.registry.units)["results"][0]["verdict"]
                if verdict == "invalid":
                    raise ValidationError(f"connector node {node['id']}: business constraint {constraint['id']} is invalid")

    def _project(self, rel, data, narrative, assertions, source_hash):
        base, knowledge = data["base"], data["knowledge"]
        node = base["node"]
        prior = self.db.first("SELECT id FROM node WHERE source_path=?", (rel,))
        if prior and prior["id"] != node["id"]:
            self.db.write("DELETE FROM node WHERE id=?", (prior["id"],))
        other = self.db.first("SELECT source_path FROM node WHERE id=?", (node["id"],))
        if other and other["source_path"] != rel:
            raise ValidationError(f"duplicate authored node id {node['id']}")
        self.db.write("""INSERT INTO node(id,kind,type,natural_key,label,key_namespace,aliases_json,tags_json,attrs_json,lifecycle,revision,source_class,source_path,source_hash,narrative,compiled_at)
          VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET kind=excluded.kind,type=excluded.type,
          natural_key=excluded.natural_key,label=excluded.label,key_namespace=excluded.key_namespace,aliases_json=excluded.aliases_json,
          tags_json=excluded.tags_json,attrs_json=excluded.attrs_json,lifecycle=excluded.lifecycle,revision=excluded.revision,
          source_class=excluded.source_class,source_path=excluded.source_path,source_hash=excluded.source_hash,
          narrative=excluded.narrative,compiled_at=excluded.compiled_at""",
          (node["id"], node["kind"], node["type"], node.get("key"), node["label"], node.get("key_namespace", ""),
           json_text(node.get("aliases") or []), json_text(base.get("classification", {}).get("tags") or []),
           json_text({key: validate_value(self.registry.predicates[key], value, self.registry.units, self.registry.currencies) for key, value in (knowledge.get("attrs") or {}).items()}), base.get("lifecycle", {}).get("state", "active"),
           base.get("version", {}).get("entity_revision", 1), "git_authored", rel, source_hash, narrative, utcnow()))
        self._sync_git_attrs(node["id"], knowledge.get("attrs") or {}, rel, source_hash)
        self.db.write("DELETE FROM assertion WHERE source_path=?", (rel,))
        self.db.write("DELETE FROM edge WHERE source_path=?", (rel,))
        for item in assertions:
            temporal, epistemic, provenance = (item.get(key) or {} for key in ("temporal", "epistemic", "provenance"))
            status = epistemic.get("status", "proposed")
            tier = self.registry.predicates[item["predicate"]].get("policy", {}).get("provenance_tier", "C")
            temperature = self.registry.temperature({"predicate": item["predicate"], "status": status, **temporal})
            self.db.write("""INSERT INTO assertion(id,node_id,predicate,value_json,qualifiers_json,observed_at,valid_from,valid_to,
              assertion_kind,status,confidence,evidence_refs_json,source_refs_json,supersedes,provenance_tier,temperature,source_path,source_hash)
              VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
              (item["id"], node["id"], item["predicate"], json_text(validate_value(self.registry.predicates[item["predicate"]], item["value"], self.registry.units, self.registry.currencies)), json_text(item.get("qualifiers") or {}),
               temporal.get("observed_at"), temporal.get("valid_from"), temporal.get("valid_to"),
               epistemic.get("assertion_kind", "claim"), status, epistemic.get("confidence"),
               json_text(provenance.get("evidence_refs") or []), json_text(provenance.get("source_refs") or []),
               (item.get("version") or {}).get("supersedes"), tier, temperature, rel, source_hash))
        for item in knowledge.get("relations") or []:
            temporal = item.get("temporal") or {}
            self.db.write("INSERT OR REPLACE INTO edge(src,predicate,dst,rel_id,valid_from,valid_to,weight,source_path) VALUES(?,?,?,?,?,?,?,?)",
                          (node["id"], item["predicate"], item["target"], item.get("id") or "", temporal.get("valid_from") or "", temporal.get("valid_to") or "", item.get("weight"), rel))

    def _validate_edges(self):
        nodes = {row["id"]: row["type"] for row in self.db.execute("SELECT id,type FROM node")}
        definitions = {item["id"]: item for item in self.registry.ontology.get("relation_types", [])}
        for row in self.db.execute("SELECT src,dst,predicate FROM edge"):
            if row["dst"] not in nodes:
                raise ValidationError(f"relation {row['predicate']} targets missing node {row['dst']}")
            definition = definitions.get(row["predicate"])
            if definition and definition.get("connections") and not any(edge["source_type"] == nodes[row["src"]] and edge["target_type"] == nodes[row["dst"]]
                                                                    for edge in definition["connections"]):
                raise ValidationError(f"relation {row['predicate']} does not allow {nodes[row['src']]} -> {nodes[row['dst']]}")

    def _reclassify(self):
        changed = set()
        for row in self.db.execute("SELECT id,node_id,predicate,status,observed_at,valid_from,valid_to,temperature FROM assertion"):
            value = self.registry.temperature(row)
            if value != row["temperature"]:
                self.db.write("UPDATE assertion SET temperature=? WHERE id=?", (value, row["id"]))
                changed.add(row["node_id"])
        return changed

    def _refresh_cards(self, identifiers=None):
        nodes = self.db.execute("SELECT * FROM node ORDER BY id") if identifiers is None else [row for identifier in identifiers
            if (row := self.db.first("SELECT * FROM node WHERE id=?", (identifier,)))]
        for node in nodes:
            rows = self.db.execute("SELECT * FROM assertion WHERE node_id=? AND status='confirmed' AND temperature='hot' ORDER BY predicate,id", (node["id"],))
            assertions = [card_assertion(row) for row in rows]
            edges = self.db.execute("SELECT src,predicate,dst,rel_id,valid_from,valid_to,weight FROM edge WHERE src=? ORDER BY predicate,dst", (node["id"],))
            gaps = (["no_current_assertions"] if not assertions else []) + (["draft_node"] if node["lifecycle"] == "draft" else [])
            card = {"id": node["id"], "type": node["type"], "label": node["label"], "attrs": json.loads(node["attrs_json"]),
                    "current_assertions": assertions, "key_relations": edges, "source_class": node["source_class"], "knowledge_gaps": gaps}
            now = utcnow()
            self.db.write("INSERT INTO entity_card(node_id,card_json,updated_at) VALUES(?,?,?) ON CONFLICT(node_id) DO UPDATE SET card_json=excluded.card_json,updated_at=excluded.updated_at",
                          (node["id"], json_text(card), now))
            searchable = "\n".join(str(item["value"].get("literal", "")) for item in assertions
                                   if self.registry.predicates[item["predicate"]].get("policy", {}).get("embedding") is True)
            if self.db.fts_enabled:
                self.db.write("DELETE FROM node_fts WHERE node_id=?", (node["id"],))
                self.db.write("INSERT INTO node_fts(node_id,label,body) VALUES(?,?,?)", (node["id"], node["label"], node["narrative"] + "\n" + searchable))
            SemanticIndex(self.db).index(node["id"], node["label"] + "\n" + node["narrative"] + "\n" + searchable, node["source_hash"])
            snapshot = {**card, "assertion_history": self.db.execute("SELECT * FROM assertion WHERE node_id=?", (node["id"],)),
                        "edge_history": self.db.execute("SELECT * FROM edge WHERE src=?", (node["id"],))}
            content_hash = digest(snapshot)
            previous = self.db.first("SELECT content_hash FROM entity_snapshot WHERE node_id=? ORDER BY recorded_at DESC LIMIT 1", (node["id"],))
            if not previous or previous["content_hash"] != content_hash:
                self.db.write("INSERT INTO entity_snapshot(node_id,recorded_at,content_hash,snapshot_json) VALUES(?,?,?,?)",
                              (node["id"], now, content_hash, json_text(snapshot)))

    def _sync_git_attrs(self, node_id, attrs, rel, source_hash):
        source_ref = "git:" + rel
        self.db.write("DELETE FROM attribute_candidate WHERE node_id=? AND source_ref_id=?", (node_id, source_ref))
        for identifier, value in attrs.items():
            normalized = validate_value(self.registry.predicates[identifier], value, self.registry.units, self.registry.currencies)
            self.db.write("INSERT INTO attribute_candidate(node_id,predicate,source_ref_id,source_class,value_json,observed_at,source_hash) VALUES(?,?,?,'git_authored',?,?,?)",
                          (node_id, identifier, source_ref, json_text(normalized), utcnow(), source_hash))
        grouped = {}
        for item in self.db.execute("SELECT * FROM attribute_candidate WHERE node_id=?", (node_id,)):
            grouped.setdefault(item["predicate"], []).append(item)
        effective = {}
        policies = self.registry.policies.get("authority", {}).get("authority_policies", [])
        for identifier, candidates in grouped.items():
            policy_id = self.registry.predicates[identifier].get("policy", {}).get("authority")
            policy = next((p for p in policies if p["id"] == policy_id), {})
            def rank(item):
                for index, group in enumerate(("primary", "secondary", "supporting")):
                    if item["source_class"] in policy.get(group, []):
                        return index * 100 + policy[group].index(item["source_class"])
                return 1000
            chosen = min(candidates, key=lambda item: (rank(item), item["source_ref_id"]))
            effective[identifier] = json.loads(chosen["value_json"])
        self.db.write("UPDATE node SET attrs_json=? WHERE id=?", (json_text(effective), node_id))

    def _scan_maintenance(self):
        managed = ("broken_relation", "provenance", "stale_assertion", "deprecated_predicate", "authority_conflict", "assertion_externalization")
        self.db.write("DELETE FROM review_item WHERE status='open' AND kind IN (?,?,?,?,?,?)", managed)
        now = utcnow()
        scores = {}
        for definition in self.registry.rules.values():
            scores.update(definition.get("review_scores", {}))
        thresholds = self.registry.policies.get("maintenance", {}).get("priority", {})
        def review(kind, target, default, reason, details):
            severity = int(scores.get(kind, default))
            priority = next((key for key, item in sorted(thresholds.items(), key=lambda kv: -int(kv[1].get("minimum_score", 0)))
                             if severity >= int(item.get("minimum_score", 0))), "P3")
            identifier = hashlib.sha256(f"{kind}:{target}:{reason}".encode()).hexdigest()
            self.db.write("""INSERT INTO review_item(id,kind,target_id,severity,priority,reason,details_json,status,created_at,updated_at)
              VALUES(?,?,?,?,?,?,?,'open',?,?) ON CONFLICT(id) DO UPDATE SET severity=excluded.severity,
              priority=excluded.priority,reason=excluded.reason,details_json=excluded.details_json,updated_at=excluded.updated_at""",
              (identifier, kind, target, severity, priority, reason, json_text(details), now, now))
        from datetime import datetime, timezone
        for row in self.db.execute("SELECT * FROM assertion WHERE status='confirmed'"):
            predicate = self.registry.predicates.get(row["predicate"], {})
            observed = row["observed_at"]
            stale_days = self.registry.freshness_days(row["predicate"])
            if stale_days is not None and observed:
                age = (datetime.now(timezone.utc) - datetime.fromisoformat(observed.replace("Z", "+00:00"))).days
                if age > stale_days:
                    review("stale_assertion", row["id"], 60, "assertion exceeds its freshness policy", row)
            if predicate.get("status", {}).get("lifecycle") == "deprecated":
                review("deprecated_predicate", row["id"], 70, "assertion uses a deprecated predicate", row)
            tier = predicate.get("policy", {}).get("provenance_tier", "C")
            provenance = next((p for p in self.registry.policies.get("provenance", {}).get("provenance_policies", []) if p["id"] == tier), {})
            source_refs, evidence_refs = json.loads(row["source_refs_json"]), json.loads(row["evidence_refs_json"])
            if provenance.get("source_ref_required") and not source_refs or provenance.get("evidence_or_snapshot_required") and not evidence_refs:
                review("provenance", row["id"], 95, "assertion lacks required provenance", row)
        threshold = self.registry.policies.get("maintenance", {}).get("assertion_externalization", {})
        for row in self.db.execute("SELECT source_path,count(*) AS count FROM assertion WHERE source_path LIKE 'knowledge/%' GROUP BY source_path"):
            limit = int(threshold.get("inline_count", 0))
            if limit and row["count"] > limit:
                review("assertion_externalization", row["source_path"], 45,
                       "authored assertions exceed externalization threshold", row)
