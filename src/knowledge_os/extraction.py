"""Provider neutral extraction requests and evidence checked candidate proposals."""
from __future__ import annotations

import copy
import hashlib
import json
import re
from datetime import datetime

from .core import ValidationError, digest, frontmatter, utcnow
from .entity_resolution import EntityResolver, norm
from .values import validate_value


ALIASES = {"voice": "audio", "transcript": "audio", "meeting": "meeting_minutes", "minutes": "meeting_minutes", "meeting_notes": "meeting_minutes"}


class Extraction:
    def __init__(self, service, profile_id="default"):
        self.service = service
        self.registry = service.registry
        try:
            self.profile = self.registry.extraction_profiles[profile_id]
        except KeyError as exc:
            raise ValidationError(f"extraction profile not found: {profile_id}") from exc

    def fingerprint(self):
        return digest({"ontology": self.registry.ontology, "predicates": self.registry._public(self.registry.predicates),
                       "schemas": self.registry.schemas, "policies": self.registry.policies,
                       "profile": {k: v for k, v in self.profile.items() if not k.startswith("_")}})

    def _connector_only(self, predicate_id):
        policy = self.registry.predicates[predicate_id].get("policy", {})
        authority = next((item for item in self.registry.policies.get("authority", {}).get("authority_policies", [])
                          if item["id"] == policy.get("authority")), {})
        return "git_authored" not in authority.get("primary", []) and policy.get("history") != "git_only"

    def prepare(self, source):
        if not isinstance(source, dict):
            raise ValidationError("source must be an object")
        if source.get("version") == "1.0" and isinstance(source.get("segments"), list):
            envelope = source
        else:
            kind = ALIASES.get(source.get("kind"), source.get("kind"))
            if kind not in self.profile.get("source_types", []):
                raise ValidationError(f"unsupported extraction source kind: {kind}")
            content = str(source.get("content") or "").replace("\x00", "").strip()
            if not content:
                raise ValidationError("source content is required after materialization")
            if len(content) > int(self.profile.get("max_source_chars", 500000)):
                raise ValidationError("source content exceeds profile maximum")
            content_hash = hashlib.sha256(content.encode()).hexdigest()
            locator = str(source.get("locator") or f"inline:{kind}").strip()
            captured_at = source.get("captured_at") or utcnow()
            try:
                datetime.fromisoformat(captured_at.replace("Z", "+00:00"))
            except ValueError as exc:
                raise ValidationError("invalid source captured_at") from exc
            size = max(int(self.profile.get("segment_chars", 4000)), 1)
            segments = []
            for start in range(0, len(content), size):
                chunk = content[start:start + size]
                text = chunk.strip()
                if text:
                    offset = start + chunk.index(text)
                    segments.append({"id": f"segment:{len(segments)+1:04d}", "text": text, "start": offset, "end": offset + len(text)})
            source_identity = locator if source.get("locator") else locator + ":" + content_hash
            envelope = {"version": "1.0", "id": source.get("id") or f"source:{kind}:{hashlib.sha256(source_identity.encode()).hexdigest()[:24]}",
                        "kind": kind, "locator": locator, "mime_type": source.get("mime_type"), "content_hash": content_hash,
                        "captured_at": captured_at, "metadata": source.get("metadata") if isinstance(source.get("metadata"), dict) else {},
                        "segments": segments}
        self._validate_source(envelope)
        concepts = []
        for concept in self.registry.ontology.get("concept_types", []):
            bindings = []
            for binding in concept.get("properties", []):
                predicate = self.registry.predicates[binding["predicate"]]
                bindings.append({"predicate": binding["predicate"], "required_for_create": binding.get("required", False),
                                 "storage": predicate.get("storage", {}).get("mode"), "value_type": predicate.get("value", {}).get("type"),
                                 "cardinality": predicate.get("value", {}).get("cardinality"),
                                 "description": predicate.get("semantics", {}).get("description")})
            relations = []
            for relation in self.registry.ontology.get("relation_types", []):
                endpoints = [e for e in relation.get("connections", []) if e["source_type"] == concept["id"]]
                if endpoints:
                    relations.append({"id": relation["id"], "mode": relation.get("mode", "simple"),
                                      "target_types": list(dict.fromkeys(e["target_type"] for e in endpoints))})
            concepts.append({"id": concept["id"], "label": concept.get("label"), "description": concept.get("description"),
                             "properties": bindings, "relations": relations})
        existing = EntityResolver(self.service.db).hints(envelope, int(self.profile.get("existing_entity_limit", 500)))
        fingerprint = self.fingerprint()
        return {"protocol_version": self.profile.get("protocol_version", "knowledgeos.extraction.v1"),
                "registry_fingerprint": fingerprint,
                "instructions": ["Extract only facts explicitly supported by source segments.",
                                 "Use only identifiers present in the ontology contract.",
                                 "Cite segment ids and exact evidence quotes for every entity.",
                                 "Do not mark assertions confirmed; candidates are proposals only."],
                "canonical_schema": self.registry.schemas.get("canonical-node"), "ontology": {"concepts": concepts},
                "existing_entities": existing, "source": envelope, "output_schema": self._output_schema(fingerprint)}

    @staticmethod
    def _evidence_schema():
        return {"type": "object", "required": ["segment_id", "quote"],
                "properties": {"segment_id": {"type": "string"}, "quote": {"type": "string", "minLength": 1}},
                "additionalProperties": False}

    @staticmethod
    def _value_schema(predicate):
        definition = predicate.get("value", {})
        typ = definition.get("type")
        if typ in ("quantity", "currency"):
            numeric = {"type": ["number", "string"]} if typ == "currency" else {"type": "number"}
            obj = {"type": "object", "required": ["literal", "unit"],
                   "properties": {"literal": numeric, "unit": {"type": "string", "minLength": 1}}, "additionalProperties": True}
            return {"oneOf": [numeric, obj]} if definition.get("default_unit") else obj
        if typ in ("string", "text", "enum", "node_ref", "date", "datetime"):
            result = {"type": "string"}
        elif typ == "decimal":
            result = {"type": ["number", "string"]}
        elif typ == "number":
            result = {"type": "number"}
        elif typ == "boolean":
            result = {"type": "boolean"}
        else:
            result = {}
        if definition.get("allowed"):
            result["enum"] = definition["allowed"]
        return result

    def _output_schema(self, fingerprint):
        variants = []
        for concept in self.registry.ontology.get("concept_types", []):
            bindings = concept.get("properties", [])
            attrs = {item["predicate"]: self._value_schema(self.registry.predicates[item["predicate"]]) for item in bindings
                     if self.registry.predicates[item["predicate"]].get("storage", {}).get("mode") == "attr"}
            assertions = []
            for item in bindings:
                identifier = item["predicate"]
                predicate = self.registry.predicates[identifier]
                if predicate.get("storage", {}).get("mode") not in ("assertion", "external"):
                    continue
                value_schema = self._value_schema(predicate)
                if predicate.get("value", {}).get("type") not in ("quantity", "currency"):
                    value_schema = {"oneOf": [value_schema, {"type": "object", "required": ["literal"],
                                                               "properties": {"literal": value_schema}, "additionalProperties": True}]}
                assertions.append({"type": "object", "required": ["predicate", "value"],
                                   "properties": {"predicate": {"const": identifier}, "value": value_schema,
                                                  "qualifiers": {"type": "object"}, "temporal": {"type": "object"},
                                                  "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                                                  "supersedes_id": {"type": "string"},
                                                  "evidence": {"type": "array", "items": self._evidence_schema()}},
                                   "additionalProperties": False})
            relation_ids = [relation["id"] for relation in self.registry.ontology.get("relation_types", [])
                            if any(edge["source_type"] == concept["id"] for edge in relation.get("connections", []))]
            relation_schema = {"type": "array", "maxItems": 0} if not relation_ids else {"type": "array", "items": {
                "type": "object", "required": ["predicate", "target"],
                "properties": {"predicate": {"enum": relation_ids}, "target": {"type": "string"},
                               "temporal": {"type": "object"}, "weight": {"type": "number"},
                               "evidence": {"type": "array", "items": self._evidence_schema()}}, "additionalProperties": False}}
            variants.append({"type": "object", "required": ["type", "label", "attrs", "assertions", "relations", "confidence", "evidence"],
                             "properties": {"type": {"const": concept["id"]}, "existing_id": {"type": "string"},
                                            "suggested_id": {"type": "string"}, "key": {"type": "string"},
                                            "key_namespace": {"type": "string", "minLength": 1},
                                            "context_ids": {"type": "array", "items": {"type": "string"}},
                                            "rename": {"type": "boolean"},
                                            "label": {"type": "string", "minLength": 1},
                                            "aliases": {"type": "array", "items": {"type": "string"}},
                                            "attrs": {"type": "object", "properties": attrs, "additionalProperties": False},
                                            "assertions": {"type": "array", "maxItems": 0} if not assertions else {"type": "array", "items": {"oneOf": assertions}},
                                            "relations": relation_schema, "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                                            "evidence": {"type": "array", "items": self._evidence_schema()}},
                             "additionalProperties": False})
        return {"$schema": "https://json-schema.org/draft/2020-12/schema", "type": "object",
                "required": ["protocol_version", "registry_fingerprint", "entities"],
                "properties": {"protocol_version": {"const": self.profile.get("protocol_version", "knowledgeos.extraction.v1")},
                               "registry_fingerprint": {"const": fingerprint},
                               "entities": {"type": "array", "items": {"oneOf": variants}},
                               "unmapped_facts": {"type": "array", "items": {"type": "object", "required": ["text", "reason", "evidence"],
                                                                                 "properties": {"text": {"type": "string"}, "reason": {"type": "string"},
                                                                                                "evidence": {"type": "array", "items": self._evidence_schema()}},
                                                                                 "additionalProperties": False}}}, "additionalProperties": False}

    def _validate_source(self, source):
        if source.get("kind") not in self.profile.get("source_types", []) or not source.get("id") or not source.get("segments"):
            raise ValidationError("invalid extraction source envelope")
        content = "".join(str(segment.get("text") or "") for segment in source["segments"])
        if not content or len(content) > int(self.profile.get("max_source_chars", 500000)):
            raise ValidationError("source content missing or exceeds maximum")

    def _evidence(self, items, source):
        if not isinstance(items, list) or self.profile.get("require_evidence", True) and not items:
            raise ValidationError("evidence is required")
        segments = {segment["id"]: segment["text"] for segment in source["segments"]}
        for item in items:
            if not isinstance(item, dict) or item.get("segment_id") not in segments or not str(item.get("quote") or "").strip():
                raise ValidationError("evidence must cite a segment and exact quote")
            if item["quote"].strip() not in segments[item["segment_id"]]:
                raise ValidationError(f"evidence quote is not present in {item['segment_id']}")

    def finalize(self, request, model_output, resolutions=None):
        if not isinstance(request, dict) or request.get("registry_fingerprint") != self.fingerprint():
            raise ValidationError("extraction request is stale because ontology or profile changed")
        if not isinstance(model_output, dict) or model_output.get("protocol_version") != request.get("protocol_version") or model_output.get("registry_fingerprint") != request.get("registry_fingerprint"):
            raise ValidationError("model output protocol or registry fingerprint mismatch")
        if not isinstance(model_output.get("entities"), list) or not isinstance(model_output.get("unmapped_facts", []), list):
            raise ValidationError("model output entities and unmapped_facts must be arrays")
        source = request["source"]
        self._validate_source(source)
        resolutions = resolutions or {}
        candidates, rejected, unresolved, identities = [], [], [], set()
        unmapped = list(model_output.get("unmapped_facts", []))
        resolver = EntityResolver(self.service.db)
        concepts = {item["id"]: item for item in self.registry.ontology.get("concept_types", [])}
        for index, entity in enumerate(model_output["entities"]):
            try:
                if not isinstance(entity, dict):
                    raise ValidationError("entity must be an object")
                entity = copy.deepcopy(entity)
                for field in ("type", "label", "attrs", "assertions", "relations", "confidence", "evidence"):
                    if field not in entity:
                        raise ValidationError(f"entity is missing {field}")
                typ = entity["type"]
                if typ not in concepts:
                    raise ValidationError(f"unknown concept type {typ}")
                if not str(entity["label"]).strip():
                    raise ValidationError("entity label is required")
                confidence = float(entity["confidence"])
                if not 0 <= confidence <= 1:
                    raise ValidationError("confidence must be between 0 and 1")
                self._evidence(entity["evidence"], source)
                if not isinstance(entity["attrs"], dict) or not isinstance(entity["assertions"], list) or not isinstance(entity["relations"], list):
                    raise ValidationError("attrs, assertions or relations have invalid shape")
                if typ == "decision_table":
                    raise ValidationError("versioned decision tables must be maintained through the expert template workbench")
                resolution = resolver.resolve(entity, source)
                if index in resolutions and resolution["status"] != "needs_resolution":
                    raise ValidationError("manual resolution is only allowed for an unresolved mention")
                if resolution["status"] == "needs_resolution":
                    selected = resolutions.get(index)
                    chosen = next((item for item in resolution["candidates"] if item["id"] == selected), None)
                    if selected and not chosen:
                        raise ValidationError("manual resolution must select a cited server candidate")
                    if chosen:
                        resolution = {"status": "matched", "target": chosen,
                                      "reason": "reviewed_manual_resolution", "candidates": resolution["candidates"]}
                    else:
                        unresolved.append({"index": index, "label": entity["label"],
                                           "evidence": entity["evidence"], **resolution})
                        continue
                existing = self.service.db.first("SELECT * FROM node WHERE id=?", (resolution["target"]["id"],)) if resolution["status"] == "matched" else None
                if existing:
                    identifier = existing["id"]
                else:
                    prefix = self.profile.get("identity_prefixes", {}).get(typ, typ)
                    seed = entity.get("key") or entity["label"]
                    slug = re.sub(r"[^a-z0-9]+", "-", seed.lower()).strip("-") or digest(seed)[:12]
                    identifier = entity.get("suggested_id") or f"{prefix}:{slug}"
                    if not re.fullmatch(r"[a-z][a-z0-9_-]*:[a-z0-9][a-z0-9._-]*", identifier) or not identifier.startswith(prefix + ":"):
                        raise ValidationError("invalid suggested_id")
                    if self.service.db.first("SELECT id FROM node WHERE id=?", (identifier,)):
                        raise ValidationError("generated identity already exists; provide existing_id")
                    if entity.get("key") and self.service.db.first(
                        "SELECT id FROM node WHERE key_namespace=? AND type=? AND natural_key=?",
                        (entity.get("key_namespace", ""), typ, entity["key"])):
                        raise ValidationError("scoped business key already exists")
                if identifier in identities:
                    raise ValidationError("duplicate extracted entity identity")
                identities.add(identifier)
                allowed = {binding["predicate"] for binding in concepts[typ].get("properties", [])}
                for predicate_id in list(entity["attrs"]):
                    if predicate_id not in allowed or self.registry.predicates[predicate_id].get("storage", {}).get("mode") != "attr":
                        raise ValidationError(f"predicate {predicate_id} is not declared as an attribute")
                    if (not existing or existing["source_class"] == "git_authored") and self._connector_only(predicate_id):
                        unmapped.append({"text": predicate_id, "reason": "authoritative connector required for operational fact",
                                         "evidence": entity["evidence"]})
                        del entity["attrs"][predicate_id]
                        continue
                    entity["attrs"][predicate_id] = validate_value(self.registry.predicates[predicate_id], entity["attrs"][predicate_id], self.registry.units, self.registry.currencies)
                assertions = []
                for item in entity["assertions"]:
                    predicate_id = item.get("predicate")
                    if predicate_id not in allowed or self.registry.predicates[predicate_id].get("storage", {}).get("mode") not in ("assertion", "external"):
                        raise ValidationError(f"predicate {predicate_id} is not declared as an assertion")
                    if (not existing or existing["source_class"] == "git_authored") and self._connector_only(predicate_id):
                        unmapped.append({"text": predicate_id, "reason": "authoritative connector required for operational fact",
                                         "evidence": item.get("evidence") or entity["evidence"]})
                        continue
                    evidence = item.get("evidence") or entity["evidence"]
                    self._evidence(evidence, source)
                    if (self.registry.predicates[predicate_id].get("policy", {}).get("provenance_tier") == "A"
                            and source["locator"].startswith("inline:")):
                        raise ValidationError(f"Tier A assertion {predicate_id} requires a stable source locator")
                    value = item["value"]
                    if not isinstance(value, dict):
                        value = {"type": self.registry.predicates[predicate_id]["value"]["type"], "literal": value}
                    value = validate_value(self.registry.predicates[predicate_id], value, self.registry.units, self.registry.currencies)
                    supersedes = item.get("supersedes_id")
                    if supersedes:
                        prior = self.service.db.first("SELECT node_id,predicate,source_path,status FROM assertion WHERE id=?", (supersedes,))
                        if (not prior or prior["node_id"] != identifier or prior["predicate"] != predicate_id
                                or prior["status"] != "confirmed" or not existing or prior["source_path"] != existing["source_path"]):
                            raise ValidationError("supersedes_id must identify a confirmed assertion from the same authored instance and predicate")
                    assertions.append({"id": "assertion:extract:" + digest([identifier, predicate_id, value, item.get("qualifiers") or {}, source["id"], source["content_hash"]])[:32],
                                       "predicate": predicate_id, "value": value, "qualifiers": item.get("qualifiers") or {},
                                       "temporal": {**(item.get("temporal") or {}), "observed_at": (item.get("temporal") or {}).get("observed_at") or source["captured_at"]},
                                       "epistemic": {"assertion_kind": "extracted_claim", "status": "proposed", "confidence": float(item.get("confidence", confidence))},
                                       "provenance": {"source_refs": list(dict.fromkeys((source["id"], source["locator"]))),
                                                      "evidence_refs": list(dict.fromkeys(source["id"] + "#" + e["segment_id"] for e in evidence))},
                                       "version": {"supersedes": supersedes}})
                relations = []
                relation_candidates = []
                definitions = {item["id"]: item for item in self.registry.ontology.get("relation_types", [])}
                for item in entity["relations"]:
                    predicate_id, target = item.get("predicate"), item.get("target")
                    if predicate_id not in definitions or not target:
                        raise ValidationError("unknown relation or missing target")
                    target_node = self.service.db.first("SELECT type FROM node WHERE id=?", (target,))
                    if not target_node:
                        raise ValidationError(f"relation target is not an existing entity: {target}")
                    if not any(edge["source_type"] == typ and edge["target_type"] == target_node["type"] for edge in definitions[predicate_id].get("connections", [])):
                        raise ValidationError(f"relation {predicate_id} does not allow {typ} -> {target_node['type']}")
                    relation_evidence = item.get("evidence") or entity["evidence"]
                    self._evidence(relation_evidence, source)
                    relation = {"predicate": predicate_id, "target": target}
                    if item.get("temporal"):
                        relation["temporal"] = item["temporal"]
                    if "weight" in item:
                        relation["weight"] = item["weight"]
                    relations.append(relation)
                    relation_candidates.append({"relation": relation, "source_ref_id": source["id"],
                                                "source_hash": source["content_hash"],
                                                "evidence_refs": list(dict.fromkeys(source["id"] + "#" + e["segment_id"] for e in relation_evidence)),
                                                "evidence": relation_evidence})
                if existing:
                    if existing["source_class"] == "git_authored":
                        authored, _ = frontmatter(self.service.config.root / existing["source_path"])
                        base = copy.deepcopy(authored["base"])
                        current_attrs = copy.deepcopy(authored["knowledge"]["attrs"])
                        current_assertions = copy.deepcopy(authored["knowledge"]["assertions"])
                        current_relations = copy.deepcopy(authored["knowledge"]["relations"])
                        logic_refs = copy.deepcopy(authored["knowledge"]["logic_refs"])
                        external = copy.deepcopy(authored.get("external", {"assertions_ref": None}))
                    else:
                        base = {"schema": {"ckm": "3.0"}, "node": {"id": identifier, "kind": existing["kind"], "type": typ,
                                "key": existing["natural_key"], "key_namespace": existing["key_namespace"], "label": existing["label"],
                                "aliases": json.loads(existing["aliases_json"])},
                                "classification": {"tags": json.loads(existing["tags_json"])},
                                "lifecycle": {"state": existing["lifecycle"]}, "version": {"entity_revision": existing["revision"]}}
                        current_attrs = json.loads(existing["attrs_json"])
                        current_assertions = []
                        for row in self.service.db.execute("SELECT * FROM assertion WHERE node_id=? ORDER BY id", (identifier,)):
                            current_assertions.append({"id": row["id"], "predicate": row["predicate"], "value": json.loads(row["value_json"]),
                                                       "qualifiers": json.loads(row["qualifiers_json"]),
                                                       "temporal": {key: row[key] for key in ("observed_at", "valid_from", "valid_to")},
                                                       "epistemic": {"assertion_kind": row["assertion_kind"], "status": row["status"], "confidence": row["confidence"]},
                                                       "provenance": {"evidence_refs": json.loads(row["evidence_refs_json"]), "source_refs": json.loads(row["source_refs_json"])},
                                                       "version": {"supersedes": row["supersedes"]}})
                        current_relations = [{"predicate": row["predicate"], "target": row["dst"]} for row in self.service.db.execute(
                            "SELECT * FROM edge WHERE src=?", (identifier,))]
                        logic_refs, external = [], {"assertions_ref": None}
                    if entity.get("rename"):
                        if not any(entity["label"].casefold() in item["quote"].casefold() for item in entity["evidence"]):
                            raise ValidationError("renamed label requires exact cited evidence")
                        base["node"]["label"] = entity["label"]
                    for alias in entity.get("aliases") or []:
                        if not any(alias.casefold() in item["quote"].casefold() for item in entity["evidence"]):
                            raise ValidationError("new aliases require cited evidence")
                        if alias not in base["node"]["aliases"]:
                            base["node"]["aliases"].append(alias)
                else:
                    base = {"schema": {"ckm": "3.0"}, "node": {"id": identifier, "kind": "entity", "type": typ,
                            "key": entity.get("key") or identifier.split(":", 1)[1].upper(), "label": entity["label"], "aliases": entity.get("aliases") or []},
                            "classification": {"tags": []}, "lifecycle": {"state": "draft"}, "version": {"entity_revision": 1}}
                    if entity.get("key_namespace"):
                        base["node"]["key_namespace"] = entity["key_namespace"]
                    current_attrs, current_assertions, current_relations, logic_refs, external = {}, [], [], [], {"assertions_ref": None}
                merged_attrs = {**current_attrs, **entity["attrs"]}
                merged_assertions = current_assertions + [item for item in assertions if item["id"] not in {old["id"] for old in current_assertions}]
                merged_relations = current_relations + [item for item in relations if (item["predicate"], item["target"]) not in {(old["predicate"], old["target"]) for old in current_relations}]
                used = set(merged_attrs) | {item["predicate"] for item in merged_assertions}
                for binding in concepts[typ].get("properties", []):
                    if binding.get("required") and binding["predicate"] not in used:
                        raise ValidationError(f"concept {typ} requires predicate {binding['predicate']}")
                document = {"base": base, "knowledge": {"attrs": merged_attrs, "assertions": merged_assertions,
                                                              "relations": merged_relations, "logic_refs": logic_refs},
                            "external": external}
                target_source = existing["source_path"] if existing else f"knowledge/entities/{identifier.replace(':', '-')}.md"
                if not existing and (self.service.config.root / target_source).exists():
                    raise ValidationError("new identity source path already exists; compile and resolve the authored instance")
                publication_route = "upstream_source_system" if existing and existing["source_class"] != "git_authored" else "governed_changeset"
                if publication_route == "upstream_source_system":
                    target_source = target_source.removeprefix("connector:")
                if existing:
                    operations = []
                    if base["node"]["label"] != existing["label"]:
                        operations.append({"op": "replace", "path": "/base/node/label", "value": entity["label"]})
                    aliases = base["node"]["aliases"]
                    if aliases != json.loads(existing["aliases_json"]):
                        operations.append({"op": "replace", "path": "/base/node/aliases", "value": aliases})
                    for key, value in entity["attrs"].items():
                        if current_attrs.get(key) != value:
                            operations.append({"op": "replace" if key in current_attrs else "add", "path": "/knowledge/attrs/" + key.replace("~", "~0").replace("/", "~1"), "value": value})
                    for item in assertions:
                        if item["id"] not in {old["id"] for old in current_assertions}:
                            operations.append({"op": "add", "path": "/knowledge/assertions/-", "value": item})
                    for item in relations:
                        if (item["predicate"], item["target"]) not in {(old["predicate"], old["target"]) for old in current_relations}:
                            operations.append({"op": "add", "path": "/knowledge/relations/-", "value": item})
                    if operations and publication_route == "governed_changeset":
                        document["base"]["version"]["entity_revision"] = existing["revision"] + 1
                        operations.append({"op": "replace", "path": "/base/version/entity_revision", "value": existing["revision"] + 1})
                    action = "update_instance" if operations else "no_change"
                else:
                    operations = [{"op": "add", "path": "", "value": document}]
                    action = "create_instance"
                if publication_route == "governed_changeset":
                    from .compiler import Compiler
                    Compiler(self.service.config, self.registry, self.service.db, self.service.ledger).validate(
                        self.service.config.root / target_source, document, merged_assertions)
                from .governance import ChangePlan
                base_revision = ChangePlan(self.service).revision(target_source)
                candidates.append({"input_index": index, "action": action, "target_id": identifier,
                                   "target_source": target_source, "publication_route": publication_route,
                                   "base_revision": base_revision, "confidence": confidence, "operations": operations,
                                   "resolution": resolution, "evidence": entity["evidence"],
                                   "source_binding": {"source_id": source["id"], "source_hash": source["content_hash"],
                                                      "locator": source["locator"], "entity_type": typ,
                                                      "mention": norm(entity["label"]),
                                                      "context_ids": sorted(entity.get("context_ids") or []),
                                                      "target_id": identifier, "evidence": entity["evidence"]},
                                   "attribute_candidates": [{"predicate": key, "value": value, "previous_value": current_attrs.get(key),
                                                             "source_ref_id": source["id"], "source_hash": source["content_hash"],
                                                             "evidence_refs": [source["id"] + "#" + e["segment_id"] for e in entity["evidence"]]}
                                                            for key, value in entity["attrs"].items() if current_attrs.get(key) != value],
                                   "relation_candidates": relation_candidates,
                                   "proposed_instance": document,
                                   "validation": {"valid": True, "registry_fingerprint": request["registry_fingerprint"]},
                                   "write_policy": "candidate_only"})
            except (ValidationError, KeyError, TypeError, ValueError) as exc:
                rejected.append({"index": index, "label": entity.get("label") if isinstance(entity, dict) else None, "errors": [str(exc)]})
        reference = {k: v for k, v in source.items() if k != "segments"}
        reference.update({"authority_class": self.profile.get("source_authority", {}).get(source["kind"], source["kind"]),
                          "segment_count": len(source["segments"])})
        return {"protocol_version": request["protocol_version"], "registry_fingerprint": request["registry_fingerprint"],
                "source": reference, "candidates": candidates, "unresolved": unresolved, "rejected": rejected,
                "unmapped_facts": unmapped, "write_performed": False}
