"""Shared, versioned business logic authoring and bounded decision execution.

The workbench adapts existing assets instead of introducing another source
envelope. Decision graphs retain validated dependency snapshots so a later
rule/formula revision cannot silently change a published decision.
"""
from __future__ import annotations

import copy
import json
import re
import uuid
from pathlib import Path

from .business_logic import evaluate_business, validate_business, business_roles
from .core import ConflictError, NotFoundError, ValidationError, digest, frontmatter, json_text, utcnow
from .decisions import evaluate_table, validate_table
from .model_contract import validate_model

KINDS = {"business_constraint", "business_rule", "model", "decision_table", "decision_model"}
KEY = re.compile(r"[a-z][a-z0-9_-]{0,63}")
VERSION = re.compile(r"[1-9][0-9]*\.[0-9]+\.[0-9]+")


def public(value):
    if isinstance(value, dict):
        return {k: public(v) for k, v in value.items() if not k.startswith("_")}
    if isinstance(value, list):
        return [public(v) for v in value]
    return value


def asset_ref(kind, definition):
    if kind == "decision_table":
        return f"decision:{definition['id']}:{definition['version']}"
    return f"{kind}:{definition['id']}@{definition['version']}"


def definition_bindings(kind, definition):
    values = []
    if kind in ("business_rule", "business_constraint"):
        values = [*business_roles(definition).values(), *(item["predicate"] for item in definition["inputs"]), definition["scope"].get("relation_type")]
    elif kind == "model":
        values = [definition.get("applies_to"), definition.get("output_predicate"), *(item.get("predicate") for item in definition["inputs"])]
    elif kind == "decision_model":
        values = [value for node in definition["nodes"] for value in definition_bindings(node["kind"], node["snapshot"])]
    return sorted(set(filter(None, values)))


def validate_definition(kind, definition, registry):
    if kind not in KINDS or not isinstance(definition, dict):
        raise ValidationError("unknown business logic asset kind")
    if kind in ("business_rule", "business_constraint"):
        validate_business(definition, kind, registry.predicates, registry.ontology, registry.units)
    elif kind == "model":
        validate_model(definition, registry.predicates, registry.ontology, registry.units)
    elif kind == "decision_table":
        validate_table(definition, registry.units)
    else:
        validate_decision_model(definition, registry)
    cases = definition.get("test_cases", [])
    if not isinstance(cases, list) or len(cases) > 50:
        raise ValidationError("logic assets accept up to 50 regression cases")
    seen = set()
    for case in cases:
        if not isinstance(case, dict) or not KEY.fullmatch(str(case.get("id", ""))) or case["id"] in seen:
            raise ValidationError("regression cases require unique stable ids")
        if not isinstance(case.get("input"), dict) or not isinstance(case.get("expected"), dict) or not case["expected"]:
            raise ValidationError("regression cases require input and nonempty expected result objects")
        seen.add(case["id"])
    return definition


def binding_dependencies(binding, node_ids):
    if not isinstance(binding, dict) or binding.get("source") not in ("facts", "node", "literal"):
        raise ValidationError("decision bindings require facts, node or literal source")
    source = binding["source"]
    if source == "literal":
        if set(binding) != {"source", "value"}:
            raise ValidationError("literal bindings require exactly source and value")
        return set()
    allowed = {"source", "path", "node"} if source == "node" else {"source", "path"}
    path = binding.get("path", [])
    if set(binding) - allowed or not isinstance(path, list) or len(path) > 8 or any(
        not isinstance(key, str) or not key or len(key) > 100 for key in path
    ):
        raise ValidationError("decision bindings require a bounded list of path keys")
    if source == "node":
        if binding.get("node") not in node_ids:
            raise ValidationError("decision binding references an unknown node")
        return {binding["node"]}
    return set()


def validate_decision_model(definition, registry):
    if definition.get("format") != "knowledgeos.decision-model.v1" or not KEY.fullmatch(str(definition.get("id", ""))):
        raise ValidationError("decision model requires format and stable id")
    if not VERSION.fullmatch(str(definition.get("version", ""))) or not str(definition.get("label", "")).strip():
        raise ValidationError("decision model requires label and semantic version")
    if definition.get("status", {}).get("lifecycle") not in ("draft", "active", "deprecated"):
        raise ValidationError("decision model requires draft, active or deprecated lifecycle")
    nodes = definition.get("nodes")
    if not isinstance(nodes, list) or not 1 <= len(nodes) <= 30:
        raise ValidationError("decision model requires 1-30 nodes")
    ids = [node.get("id") if isinstance(node, dict) else None for node in nodes]
    if any(not KEY.fullmatch(str(key or "")) for key in ids) or len(set(ids)) != len(ids):
        raise ValidationError("decision nodes require unique stable ids")
    deps = {}
    for node in nodes:
        kind, snapshot = node.get("kind"), node.get("snapshot")
        if kind not in KINDS - {"decision_model"} or not isinstance(snapshot, dict):
            raise ValidationError("decision nodes require a supported asset and pinned snapshot")
        validate_definition(kind, snapshot, registry)
        if node.get("ref") != asset_ref(kind, snapshot):
            raise ValidationError("decision node reference disagrees with its snapshot version")
        if snapshot.get("status", {}).get("lifecycle", "active") != "active":
            raise ValidationError("decision nodes may reference active definitions only")
        bindings = node.get("bindings")
        if not isinstance(bindings, dict) or not bindings or len(bindings) > 25:
            raise ValidationError("decision nodes require bounded input bindings")
        deps[node["id"]] = set().union(*(binding_dependencies(value, ids) for value in bindings.values()))
    remaining, ordered = set(ids), []
    while remaining:
        ready = [key for key in ids if key in remaining and not deps[key].intersection(remaining)]
        if not ready:
            raise ValidationError("decision model contains a dependency cycle")
        ordered.extend(ready)
        remaining.difference_update(ready)
    outputs = definition.get("outputs")
    if not isinstance(outputs, dict) or not 1 <= len(outputs) <= 20:
        raise ValidationError("decision model requires named output bindings")
    for key, value in outputs.items():
        if not KEY.fullmatch(str(key)):
            raise ValidationError("decision outputs require stable names")
        binding_dependencies(value, ids)
    return ordered


def read_binding(binding, facts, results):
    if binding["source"] == "literal":
        return copy.deepcopy(binding["value"])
    value = facts if binding["source"] == "facts" else results[binding["node"]]
    for key in binding.get("path", []):
        if isinstance(value, dict) and key in value:
            value = value[key]
        elif isinstance(value, list) and key.isdigit() and int(key) < len(value):
            value = value[int(key)]
        else:
            raise ValidationError(f"missing decision binding input: {'.'.join(binding.get('path', []))}")
    return copy.deepcopy(value)


def contains_expected(actual, expected):
    if isinstance(expected, dict):
        return isinstance(actual, dict) and all(key in actual and contains_expected(actual[key], value) for key, value in expected.items())
    if isinstance(expected, list):
        return isinstance(actual, list) and len(actual) == len(expected) and all(contains_expected(a, b) for a, b in zip(actual, expected))
    return type(actual) is type(expected) and actual == expected


class LogicWorkbench:
    def __init__(self, service):
        self.service, self.registry = service, service.registry

    def _assets(self):
        for kind, collection in (("business_rule", self.registry.rules), ("business_constraint", self.registry.constraints), ("model", self.registry.models)):
            for definition in collection.values():
                if kind != "model" and definition.get("format") != {"business_rule": "knowledgeos.business-rule.v1", "business_constraint": "knowledgeos.business-constraint.v1"}[kind]:
                    continue
                yield kind, public(definition), Path(definition["_path"]).relative_to(self.service.config.root).as_posix()
        from .templates import TemplateWorkbench
        hidden = self.service._unpublished_decision_assets()
        for definition in TemplateWorkbench(self.service).list_decisions():
            if asset_ref("decision_table", definition) not in hidden:
                yield "decision_table", definition, f"knowledge/decisions/{definition['id']}/v{definition['version']}.md"
        hidden_paths = set()
        for row in self.service.db.execute("SELECT target_source,status,patch_json FROM changeset"):
            patch = json.loads(row["patch_json"])
            if isinstance(patch, dict) and patch.get("op") == "logic_asset" and row["status"] != "published":
                hidden_paths.add(row["target_source"])
        for definition in self.registry.decision_models.values():
            path = Path(definition["_path"]).relative_to(self.service.config.root).as_posix()
            if path not in hidden_paths:
                yield "decision_model", public(definition), path

    def resolve(self, ref):
        for kind, definition, path in self._assets():
            if asset_ref(kind, definition) == ref:
                return kind, definition, path
        raise NotFoundError(f"published logic asset not found: {ref}")

    def prepare(self, kind, definition):
        if kind not in KINDS or not isinstance(definition, dict):
            raise ValidationError("logic asset requires a supported kind and definition object")
        definition = public(copy.deepcopy(definition))
        if kind == "decision_model":
            # A supplied snapshot is never trusted. Recover it from a published
            # asset or from a previously published graph that already pinned it.
            trusted = {node["ref"]: node["snapshot"] for asset_kind, graph, _ in self._assets() if asset_kind == "decision_model" for node in graph["nodes"]}
            for node in definition.get("nodes", []):
                try:
                    asset_kind, snapshot, _ = self.resolve(node.get("ref"))
                except NotFoundError:
                    snapshot = trusted.get(node.get("ref"))
                    if snapshot is None:
                        raise
                    asset_kind = node.get("kind")
                if node.get("kind") != asset_kind:
                    raise ValidationError("decision node kind disagrees with referenced asset")
                node["snapshot"] = copy.deepcopy(snapshot)
        validate_definition(kind, definition, self.registry)
        return definition

    def catalog(self):
        from .templates import TemplateWorkbench
        entries = []
        assets = list(self._assets())
        hidden_routes = self.service._unpublished_template_assets()[0]
        routes = [item for item in TemplateWorkbench(self.service).list_templates() if item["id"] not in hidden_routes]
        route_specs = {item["id"]: TemplateWorkbench(self.service).get(item["id"]) for item in routes}
        hidden_operations = self.service._unpublished_template_assets()[1]
        operations = {item["id"]: item for item in TemplateWorkbench(self.service).list_operations() if item["id"] not in hidden_operations}
        for kind, definition, path in assets:
            ref, uses, bindings = asset_ref(kind, definition), [], definition_bindings(kind, definition)
            model_operations = set()
            if kind == "model":
                model_ref = f"{definition['id']}@{definition['version']}"
                for operation in operations.values():
                    if operation.get("time_model_ref") == model_ref or any(resource.get("quantity_model_ref") == model_ref for resource in operation.get("resources", [])):
                        model_operations.add(operation["id"])
                        uses.append({"kind": "operation", "id": operation["id"], "label": operation["label"], "href": f"/instances?id={operation['id']}"})
            for asset_kind, graph, _ in assets:
                if asset_kind == "decision_model" and any(node["ref"] == ref for node in graph["nodes"]):
                    uses.append({"kind": "decision_model", "id": asset_ref(asset_kind, graph), "label": graph["label"], "href": f"/rules?asset={asset_ref(asset_kind, graph)}"})
            for route in routes:
                spec = route_specs[route["id"]]
                direct = kind == "decision_table" and any(item.get("decision_binding", {}).get("ref") == ref for item in [*spec["groups"], *spec["steps"]])
                referenced_operations = {step["operation_ref"] for step in spec["steps"]}
                for step in spec["steps"]:
                    binding = step.get("decision_binding", {})
                    if binding.get("purpose") == "operation":
                        table = TemplateWorkbench(self.service).decision(binding["ref"], spec)
                        referenced_operations.update(output.get(binding["output"]) for output in [*(row["then"] for row in table["rules"]), table.get("default_output", {})])
                model_use = bool(model_operations & referenced_operations)
                if direct or model_use:
                    uses.append({"kind": "template", "id": route["id"], "label": route["label"], "href": f"/templates?template={route['id']}"})
            entries.append({"ref": ref, "kind": kind, "id": definition["id"], "label": definition.get("label", definition.get("description", definition["id"])),
                            "version": definition["version"], "lifecycle": definition.get("status", {}).get("lifecycle", "active"),
                            "source_path": path, "definition": definition, "bindings": sorted(set(filter(None, bindings))), "used_by": uses})
        return {"assets": entries, "registry_fingerprint": self.registry.fingerprint,
                "capabilities": {"writes": "changeset_only", "decision_tables": "bounded_dmn_subset", "max_decision_nodes": 30,
                                 "actual_assignments": False, "preview_writes": False}}

    def evaluate(self, kind, definition, facts):
        if not isinstance(facts, dict):
            raise ValidationError("logic inputs must be an object")
        validate_definition(kind, definition, self.registry)
        if kind in ("business_rule", "business_constraint"):
            result = evaluate_business(definition, kind, facts, self.registry.predicates, self.registry.ontology, self.registry.units)
            result["eligible_candidates"] = [facts["candidates"][row["candidate_index"]] for row in result["results"] if row["verdict"] == "eligible"]
            result["status"] = "incomplete" if any(row["verdict"] == "unknown" for row in result["results"]) else "blocked" if kind == "business_constraint" and any(row["verdict"] == "invalid" for row in result["results"]) else "complete"
            return result
        if kind == "model":
            return {"status": "complete", **self.service.models.preview(definition, facts.get("inputs", facts))}
        if kind == "decision_table":
            return {"status": "complete", **evaluate_table(definition, facts.get("facts", facts), facts.get("item", {}), self.registry.units)}
        results, trace = {}, []
        order = validate_decision_model(definition, self.registry)
        nodes = {node["id"]: node for node in definition["nodes"]}
        for key in order:
            node = nodes[key]
            inputs = {name: read_binding(binding, facts, results) for name, binding in node["bindings"].items()}
            outcome = self.evaluate(node["kind"], node["snapshot"], inputs)
            if node["kind"] == "business_rule" and outcome["status"] == "complete" and not outcome["eligible_candidate_ids"]:
                outcome = {**outcome, "status": "blocked", "reason": "no_eligible_candidates"}
            results[key] = outcome
            trace.append({"node": key, "ref": node["ref"], "input": inputs, "result": outcome})
            if outcome["status"] != "complete":
                return {"status": outcome["status"], "output": {}, "nodes": results, "trace": trace, "stopped_at": key}
        return {"status": "complete", "output": {name: read_binding(binding, facts, results) for name, binding in definition["outputs"].items()}, "nodes": results, "trace": trace}

    def tests(self, kind, definition):
        rows = []
        for case in definition.get("test_cases", []):
            try:
                result = self.evaluate(kind, definition, case["input"])
                rows.append({"id": case["id"], "passed": contains_expected(result, case["expected"]), "expected": case["expected"], "actual": result})
            except (ValidationError, KeyError, TypeError, ValueError) as exc:
                rows.append({"id": case["id"], "passed": False, "error": str(exc), "expected": case["expected"]})
        return {"cases": rows, "passed": bool(rows) and all(row["passed"] for row in rows)}

    def bind_entities(self, kind, definition, bindings, as_of=None):
        if kind not in ("business_rule", "business_constraint"):
            raise ValidationError("direct instance binding is supported for rules and constraints; decision graphs receive explicit input mappings")
        if not isinstance(bindings, dict) or not bindings.get("subject_id"):
            raise ValidationError("instance binding requires a subject id")
        as_of = as_of or utcnow()
        subject, subject_refs = self.service._business_facts_for(definition, bindings["subject_id"], "subject", as_of)
        facts, refs = {"subject": subject, "contexts": {}, "candidates": []}, {"subject": subject_refs, "contexts": {}, "candidates": {}}
        facts["contexts"], refs["contexts"] = self.service._business_context_facts(definition, bindings.get("context_ids"), as_of)
        if kind == "business_rule":
            for identifier in self.service._business_candidate_ids(definition, bindings.get("candidate_ids")):
                candidate, source = self.service._business_facts_for(definition, identifier, "candidate", as_of)
                facts["candidates"].append(candidate)
                refs["candidates"][identifier] = source
        return facts, refs, as_of

    def preview(self, kind, definition, facts=None, entity_bindings=None, as_of=None):
        definition = self.prepare(kind, definition)
        refs = {}
        if entity_bindings is not None:
            if facts is not None:
                raise ValidationError("choose scenario facts or confirmed instance bindings")
            facts, refs, as_of = self.bind_entities(kind, definition, entity_bindings, as_of)
        result = self.evaluate(kind, definition, facts or {}) if facts is not None else None
        return {"definition": definition, "result": result, "tests": self.tests(kind, definition), "source_refs": refs, "as_of": as_of,
                "definition_hash": digest(definition), "input_hash": digest(facts) if facts is not None else None}

    def render_asset(self, kind, definition):
        validate_definition(kind, definition, self.registry)
        if kind == "decision_table":
            from .templates import _node
            path = f"knowledge/decisions/{definition['id']}/v{definition['version']}.md"
            document = _node(asset_ref(kind, definition), "decision_table", definition["id"], definition["label"], {"decision_definition": definition}, [])
        elif kind == "decision_model":
            path = f"control/decision-models/{definition['id']}/v{definition['version']}.yaml"
            document = {"data": definition, "body": ""}
        else:
            raise ValidationError("standalone logic packages accept decision tables or decision models")
        return {path: document}

    def propose(self, *, actor, kind, definition, reason, base_revision, idempotency_key=None):
        definition = self.prepare(kind, definition)
        tests = self.tests(kind, definition)
        if not tests["passed"]:
            raise ValidationError("logic publication requires at least one passing regression case and no failing cases")
        if kind in ("decision_table", "decision_model"):
            documents = self.render_asset(kind, definition)
            target, patch, operations = next(iter(documents)), {"op": "logic_asset", "kind": kind, "definition": definition}, []
        else:
            prefix = {"model": "models", "business_rule": "rules/business", "business_constraint": "constraints/business"}[kind]
            target = f"control/{prefix}/{definition['id']}.yaml"
            before = next((item for item in self.service.studio()["data"]["definitions"] if item["id"] == f"{kind}:{definition['id']}"), None)
            if before:
                target = before["source_path"]
            after = {"id": f"{kind}:{definition['id']}", "kind": kind, "sourcePath": target,
                     "label": definition.get("label", definition["id"]), "description": definition.get("description", ""),
                     "lifecycle": definition.get("status", {}).get("lifecycle", "active"), "config": definition}
            patch = {"op": "studio_batch"}
            operations = [{"type": "update" if before else "create", "targetKind": kind, "targetId": after["id"], "before": before, "after": after}]
            impact = self.service.semantic_impact(target_source=target, patch=patch, operations=operations)["data"]
            if not impact["compatible"]:
                raise ValidationError(f"logic change fails compilation or existing-instance checks: {impact['issues']}")
        return self.service.propose(actor=actor, target_source=target, patch=patch, operations=operations,
                                    reason=reason, risk="high", title=f"业务逻辑 · {definition.get('label', definition['id'])} · {definition['version']}",
                                    base_revision=base_revision, idempotency_key=idempotency_key,
                                    proposal_metadata={"workbench": "business_logic", "tests": tests})

    def run(self, *, ref, actor, facts=None, entity_bindings=None, as_of=None, idempotency_key=None):
        if not isinstance(idempotency_key, str) or not idempotency_key.strip() or len(idempotency_key) > 200:
            raise ValidationError("recorded logic runs require a bounded idempotency key")
        kind, definition, path = self.resolve(ref)
        if definition.get("status", {}).get("lifecycle", "active") != "active":
            raise ValidationError("recorded runs require an active published asset")
        request_hash = digest([ref, facts, entity_bindings, as_of])
        previous = self.service.db.first("SELECT * FROM durable.logic_run WHERE actor=? AND request_key=?", (actor, idempotency_key))
        if previous:
            if previous["request_hash"] != request_hash:
                raise ConflictError("logic run idempotency key already used with different inputs")
            return self.decode_run(previous)
        refs = {}
        mode = "scenario"
        if entity_bindings is not None:
            if facts is not None:
                raise ValidationError("choose scenario facts or confirmed instance bindings")
            facts, refs, as_of = self.bind_entities(kind, definition, entity_bindings, as_of)
            mode = "instance_facts"
        if not isinstance(facts, dict):
            raise ValidationError("recorded runs require explicit inputs")
        result = self.evaluate(kind, definition, facts)
        identifier, now = str(uuid.uuid4()), utcnow()
        with self.service.db.transaction():
            self.service.db.write("INSERT INTO durable.logic_run(id,asset_ref,actor,request_key,request_hash,recorded_at,record_json) VALUES(?,?,?,?,?,?,?)",
                                   (identifier, ref, actor, idempotency_key, request_hash, now, json_text({"kind": kind, "mode": mode,
                                    "definition": definition, "source_path": path, "definition_hash": digest(definition), "input": facts,
                                    "input_hash": digest(facts), "entity_bindings": entity_bindings, "source_refs": refs, "as_of": as_of, "result": result,
                                    "registry_snapshot": {name: public(getattr(self.registry, name)) for name in ("predicates", "ontology", "units", "currencies")}})))
            self.service.audit.stage(event_type="decision_run", actor=actor, target_id=identifier, source_ref=path,
                                     after_hash=digest(result), reason="deterministic business logic execution; no actual assignment written",
                                     payload={"asset_ref": ref, "definition_hash": digest(definition), "input_hash": digest(facts), "mode": mode})
        self.service.audit.flush()
        return self.decode_run(self.service.db.first("SELECT * FROM durable.logic_run WHERE id=?", (identifier,)))

    @staticmethod
    def decode_run(row):
        return {key: value for key, value in row.items() if key != "record_json"} | {"record": json.loads(row["record_json"])}

    def runs(self, ref=None, subject_id=None):
        clauses, params = [], []
        if ref:
            clauses.append("asset_ref=?")
            params.append(ref)
        if subject_id:
            clauses.append("json_extract(record_json,'$.entity_bindings.subject_id')=?")
            params.append(subject_id)
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        return [self.decode_run(row) for row in self.service.db.execute("SELECT * FROM durable.logic_run" + where + " ORDER BY recorded_at DESC,id DESC LIMIT 100", params)]

    def replay(self, identifier):
        row = self.service.db.first("SELECT * FROM durable.logic_run WHERE id=?", (identifier,))
        if not row:
            raise NotFoundError("logic run not found")
        record = json.loads(row["record_json"])
        service = copy.copy(self.service)
        service.registry = copy.copy(self.registry)
        for name, value in record["registry_snapshot"].items():
            setattr(service.registry, name, value)
        from .models import ModelEngine
        service.models = ModelEngine(service.registry, service.db, service.audit)
        result = LogicWorkbench(service).evaluate(record["kind"], record["definition"], record["input"])
        return {"id": identifier, "matches_recorded": digest(result) == digest(record["result"]), "result": result}
