"""Read-only draft validation, competency checks and portable ontology assistance."""
from __future__ import annotations

import copy
import json
import re
import shutil
import tempfile
from pathlib import Path

import jsonschema
import yaml

from .compiler import Compiler
from .core import Config, ConflictError, Registry, ValidationError, digest, frontmatter, load_yaml
from .governance import ChangePlan
from .semantic_contract import SemanticContract, public

PROTOCOL = "knowledgeos.ontology-assist.v1"


class SemanticWorkbench:
    def __init__(self, service):
        self.service = service
        self.registry = service.registry

    def impact(self, *, target_source, patch, operations=None):
        """Compile a candidate checkout in isolation, including connector replay.

        Neither the live projection nor durable databases are modified. Native
        compiler checks and SHACL share the same Registry in the candidate.
        """
        self.service.refresh_registry()
        baseline = self.registry.fingerprint
        documents = ChangePlan(self.service).staged_documents(target_source, patch, operations or [])
        if any(not name.startswith("control/") for name in documents):
            raise ValidationError("semantic impact preview accepts control sources only")
        issues = []
        counts = {"concepts": 0, "predicates": 0, "relations": 0, "entities": 0}
        from .service import Service
        with tempfile.TemporaryDirectory(prefix="knowledgeos-preview-") as directory:
            root = Path(directory)
            for name in ("control", "knowledge", "connectors"):
                if (self.service.config.root / name).exists():
                    shutil.copytree(self.service.config.root / name, root / name)
            for name, document in documents.items():
                target = root / name
                target.parent.mkdir(parents=True, exist_ok=True)
                if document is None:
                    target.unlink(missing_ok=True)
                elif target.suffix == ".json":
                    target.write_text(json.dumps(document["data"], ensure_ascii=False), encoding="utf-8")
                else:
                    target.write_text(yaml.safe_dump(document["data"], allow_unicode=True, sort_keys=False), encoding="utf-8")
            candidate = None
            try:
                candidate = Service(Config(root))
                # Durable connector records are replay inputs, never Markdown facts.
                for row in self.service.db.execute("SELECT * FROM connector_record"):
                    candidate.db.write("INSERT INTO durable.connector_record(source_ref_id,source_hash,version,observed_at,record_json,mapping_json) VALUES(?,?,?,?,?,?)",
                                       tuple(row[k] for k in ("source_ref_id", "source_hash", "version", "observed_at", "record_json", "mapping_json")))
                candidate.compile(rebuild=True)
                counts = {"concepts": len(candidate.registry.ontology.get("concept_types", [])),
                          "predicates": len(candidate.registry.predicates),
                          "relations": len(candidate.registry.ontology.get("relation_types", [])),
                          "entities": candidate.db.first("SELECT count(*) AS n FROM node")["n"]}
                cases = self.evaluate(candidate.registry)
                issues.extend({"scope": "competency", "message": c["question"], "case_id": c["id"]} for c in cases["cases"] if not c["passed"])
            except (ValidationError, ConflictError, jsonschema.ValidationError, ValueError, KeyError, TypeError) as exc:
                issues.append({"scope": "candidate_compile", "message": str(exc)})
            finally:
                if candidate:
                    candidate.close()
        if self.registry.source_fingerprint() != baseline:
            raise ConflictError("control sources changed during impact preview; retry")
        changed = [{"path": name, "before_hash": digest(ChangePlan._read(path)),
                    "after_hash": digest(documents[name]), "action": "delete" if documents[name] is None else "update" if path else "create"}
                   for name, path in ChangePlan(self.service).paths(target_source).items()]
        return {"compatible": not issues, "issues": issues, "changed_sources": changed,
                "candidate_counts": counts, "registry_fingerprint": baseline,
                "preview_hash": digest([baseline, documents]), "write_performed": False,
                "migration_required": bool(issues), "automatic_migration": False}

    def evaluate(self, registry=None):
        registry = registry or self.registry
        path = registry.config.control / "semantics/evaluation.yaml"
        spec = load_yaml(path) if path.exists() else {"format": "knowledgeos.competency.v1", "cases": []}
        if spec.get("format") != "knowledgeos.competency.v1" or not isinstance(spec.get("cases"), list) or len(spec["cases"]) > 100:
            raise ValidationError("invalid competency suite; at most 100 cases")
        concepts = {c["id"]: c for c in registry.ontology.get("concept_types", [])}
        results, seen = [], set()
        for case in spec["cases"]:
            if not isinstance(case, dict) or set(case) != {"id", "question", "check", "parameters", "expected"} or case["id"] in seen:
                raise ValidationError("invalid or duplicate competency case")
            seen.add(case["id"])
            params = case["parameters"]
            if not isinstance(params, dict) or not isinstance(case["question"], str):
                raise ValidationError("competency parameters must be an object")
            check = case["check"]
            if check == "concept_binding":
                actual = any(b["predicate"] == params["predicate"] and (not params.get("required") or b.get("required")) for b in concepts.get(params["concept"], {}).get("properties", []))
            elif check == "relation_endpoint":
                actual = any(r["id"] == params["relation"] and any(e["source_type"] == params["source"] and e["target_type"] == params["target"] for e in r.get("connections", [])) for r in registry.ontology.get("relation_types", []))
            elif check == "vocabulary_label":
                actual = any(t.get("pref_labels", {}).get(params["language"]) == params["label"] for t in registry.vocabularies.get(params["scheme"], {}).get("terms", []))
            elif check == "iri_roundtrip":
                contract = SemanticContract(registry)
                actual = contract.decode_iri(contract.iri(params["kind"], params["id"])) == {"kind": params["kind"], "id": params["id"]}
            else:
                raise ValidationError("unsupported competency check")
            results.append({"id": case["id"], "question": case["question"], "passed": actual == case["expected"], "actual": actual, "expected": case["expected"]})
        return {"format": "knowledgeos.competency-report.v1", "cases": results,
                "passed": sum(c["passed"] for c in results), "total": len(results),
                "conforms": all(c["passed"] for c in results), "write_performed": False,
                "scope": "configured deterministic competency cases"}

    def prepare(self, *, goal, material):
        if not isinstance(goal, str) or not goal.strip() or len(goal) > 4000 or not isinstance(material, str) or not material.strip() or len(material) > 30000:
            raise ValidationError("provide a goal and supporting modeling material within the size limits")
        contract = SemanticContract(self.registry)
        output_schema = {"type": "object", "required": ["protocol_version", "request_id", "operations"], "additionalProperties": False,
                         "properties": {"protocol_version": {"const": PROTOCOL}, "request_id": {"type": "string"},
                                        "operations": {"type": "array", "minItems": 1, "maxItems": 20,
                                                       "items": {"type": "object", "required": ["operation", "evidence"], "additionalProperties": False,
                                                                 "properties": {"operation": {"type": "object"},
                                                                                "evidence": {"type": "array", "minItems": 1, "items": {"type": "object", "required": ["segment_id", "quote"], "additionalProperties": False, "properties": {"segment_id": {"const": "material:1"}, "quote": {"type": "string", "minLength": 1}}}}}}}}}
        request = {"protocol_version": PROTOCOL, "registry_fingerprint": self.registry.fingerprint,
                   "goal": goal.strip(), "segments": [{"id": "material:1", "text": material.strip()}],
                   "contract": contract.summary(), "definition_examples": self.service.studio()["data"]["definitions"],
                   "output_schema": output_schema,
                   "instructions": ["Use create/update/deprecate Studio operations for concept, predicate or relation only.",
                                    "Every operation must cite exact quotes from the supplied material.",
                                    "Register predicates before binding them. Never add operational entity facts.",
                                    "Preserve stable identifiers and canonical source envelope. Use YAML files under the matching control root.",
                                    "Return a proposal only. Validation, impact analysis and independent review occur on the server."],
                   "write_performed": False}
        request["request_id"] = "ontology-request:" + digest(request)
        request["request_hash"] = digest(request)
        return request

    def finalize(self, *, request, model_output):
        if not isinstance(request, dict) or not isinstance(model_output, dict):
            raise ValidationError("ontology request and output must be objects")
        if request.get("protocol_version") != PROTOCOL or request.get("registry_fingerprint") != self.registry.fingerprint:
            raise ConflictError("ontology request is stale; refresh the draft")
        if request.get("request_hash") != digest({k: v for k, v in request.items() if k != "request_hash"}):
            raise ValidationError("ontology request integrity check failed")
        try:
            jsonschema.validate(model_output, request["output_schema"])
        except jsonschema.ValidationError as exc:
            raise ValidationError(f"ontology output: {exc.message}") from exc
        if model_output["request_id"] != request["request_id"]:
            raise ValidationError("ontology output request id mismatch")
        operations, targets, evidence = [], [], []
        segments = {s["id"]: s["text"] for s in request["segments"]}
        for item in model_output["operations"]:
            operation = item["operation"]
            kind = operation.get("targetKind")
            definition = operation.get("after") or {}
            identifier = operation.get("targetId")
            if kind not in ("concept", "predicate", "relation") or operation.get("type") not in ("create", "update", "deprecate"):
                raise ValidationError("ontology assistance supports create, update and deprecate of concepts, predicates and relations")
            if not isinstance(identifier, str) or not re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", identifier) or definition.get("id") != identifier or definition.get("kind") != kind:
                raise ValidationError("ontology operation requires matching stable kind and id")
            root = "predicates" if kind == "predicate" else "ontology"
            path = definition.get("sourcePath") or definition.get("source_path")
            if not isinstance(path, str) or not path.startswith(f"control/{root}/") or not path.endswith(".yaml"):
                raise ValidationError("ontology definition source must match its control root")
            if kind == "predicate" and not {"value_type", "cardinality", "storage_mode", "authority", "freshness", "provenance_tier", "temporal", "evidence", "history", "write"}.issubset(definition.get("config", {})):
                raise ValidationError("Agent predicate drafts require complete value, storage and policy contracts")
            for quote in item["evidence"]:
                if quote["segment_id"] not in segments or quote["quote"] not in segments[quote["segment_id"]]:
                    raise ValidationError("ontology evidence must quote supplied material exactly")
            operations.append(copy.deepcopy(operation)); targets.append(path); evidence.append(copy.deepcopy(item["evidence"]))
        target_source = ",".join(dict.fromkeys(targets))
        impact = self.impact(target_source=target_source, patch={"op": "studio_batch"}, operations=operations)
        return {"operations": operations, "target_source": target_source, "evidence": evidence,
                "request_id": request["request_id"], "registry_fingerprint": self.registry.fingerprint,
                "impact": impact, "write_performed": False}

    def propose(self, *, request, model_output, actor, reason, idempotency_key=None, assistance=None):
        from .authoring import assistance_metadata
        assisted = assistance_metadata(assistance, model_output) if assistance is not None else None
        proposal = self.finalize(request=request, model_output=model_output)
        if not proposal["impact"]["compatible"]:
            raise ValidationError("ontology proposal breaks existing contracts or data; resolve preview issues before submitting")
        return self.service.propose(actor=actor, reason=reason, title=request["goal"][:120],
                                    target_source=proposal["target_source"], patch={"op": "studio_batch"},
                                    operations=proposal["operations"], risk="high", base_revision=proposal["registry_fingerprint"],
                                    idempotency_key=idempotency_key,
                                    proposal_metadata={"kind": "ontology_assistance", "request_id": request["request_id"], "evidence": proposal["evidence"], "material_hash": digest(request["segments"]), "source_segments": request["segments"],
                                                       **({"assistance": assisted} if assisted else {})})

    def vocabulary_propose(self, *, vocabulary, actor, reason, base_revision, idempotency_key=None):
        from .semantic_contract import validate_vocabularies
        if not isinstance(vocabulary, dict):
            raise ValidationError("vocabulary must be an object")
        identifier = vocabulary.get("id", "")
        validate_vocabularies({identifier: vocabulary}, self.registry.ontology)
        path = f"control/vocabularies/{identifier}.yaml"
        patch = {"op": "semantic_control", "vocabulary": vocabulary}
        impact = self.impact(target_source=path, patch=patch)
        if not impact["compatible"]:
            raise ValidationError("vocabulary proposal does not pass the compatibility preview")
        return self.service.propose(actor=actor, target_source=path, patch=patch, operations=[], reason=reason, title=f"术语：{vocabulary['label']}", risk="high", base_revision=base_revision, idempotency_key=idempotency_key)
