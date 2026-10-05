"""Domain-independent authoring and deterministic expansion of expert route templates."""
from __future__ import annotations

import re
from collections import Counter, defaultdict, deque
from decimal import Decimal, InvalidOperation
from pathlib import Path

import yaml

from .core import ConflictError, NotFoundError, ValidationError, frontmatter
from .decisions import REF as DECISION_REF, evaluate_table, validate_table, scalar
from .models import decimal


KEY = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
SAMPLE_ITEM = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,63}$")
VERSION = re.compile(r"^[1-9][0-9]*\.[0-9]+\.[0-9]+$")
OPERATION_REF = re.compile(r"^operation:[a-z][a-z0-9_-]{0,63}:[1-9][0-9]*\.[0-9]+\.[0-9]+$")
FIELD = re.compile(r"^[a-z][a-z0-9_]*$")
COMPARISONS = {"eq", "ne", "gt", "gte", "lt", "lte"}
MAX_ITEMS = 200
MAX_EXPANDED = 2000
RESOURCE_TYPES = {"material", "part", "equipment", "energy", "labor", "facility"}


def _key(value, name):
    if not isinstance(value, str) or not KEY.fullmatch(value):
        raise ValidationError(f"{name} must be a lowercase stable key")
    return value


def _nonempty(value, name):
    if not isinstance(value, str) or not value.strip():
        raise ValidationError(f"{name} is required")
    return value.strip()


def _condition(value, name, depth=0):
    if value is None:
        return None
    if isinstance(value, dict) and set(value) in ({"all"}, {"any"}):
        key = next(iter(value))
        if depth >= 4 or not isinstance(value[key], list) or not 1 <= len(value[key]) <= 20:
            raise ValidationError(f"{name} has an unbounded compound condition")
        for child in value[key]:
            if child is None:
                raise ValidationError("compound condition cannot contain null")
            _condition(child, name, depth + 1)
        return value
    if not isinstance(value, dict) or set(value) != {"source", "field", "op", "value"}:
        raise ValidationError(f"{name} must have source, field, op, and value")
    if value["source"] not in ("facts", "item") or not isinstance(value["field"], str) or not FIELD.fullmatch(value["field"]) or value["op"] not in COMPARISONS:
        raise ValidationError(f"{name} has an unsupported comparison")
    if not isinstance(value["value"], (str, int, float, bool)) or isinstance(value["value"], float) and not 0 <= abs(value["value"]) < float("inf"):
        raise ValidationError(f"{name} has an invalid literal")
    return value


def _compare(condition, facts, item):
    if not condition:
        return True
    if "all" in condition:
        return all([_compare(child, facts, item) for child in condition["all"]])
    if "any" in condition:
        return any([_compare(child, facts, item) for child in condition["any"]])
    source = facts if condition["source"] == "facts" else item
    if condition["field"] not in source:
        raise ValidationError(f"missing condition input: {condition['source']}.{condition['field']}")
    left, right = source[condition["field"]], condition["value"]
    op = condition["op"]
    if op in ("eq", "ne"):
        result = type(left) is type(right) and left == right
        return result if op == "eq" else not result
    if isinstance(left, bool) or isinstance(right, bool):
        raise ValidationError("ordered condition cannot compare booleans")
    try:
        a, b = Decimal(str(left)), Decimal(str(right))
    except (InvalidOperation, TypeError) as exc:
        raise ValidationError("ordered condition requires numeric values") from exc
    if not a.is_finite() or not b.is_finite():
        raise ValidationError("ordered condition requires finite numeric values")
    return {"gt": a > b, "gte": a >= b, "lt": a < b, "lte": a <= b}[op]


def uses_item(condition):
    return bool(condition) and (condition.get("source") == "item" or any(uses_item(c) for c in condition.get("all", condition.get("any", []))))


def validate_processing(processing, registry):
    if not isinstance(processing, dict) or set(processing) - {"duration", "setup", "batch_size", "time_inputs"}:
        raise ValidationError("invalid operation processing configuration")
    if "batch_size" in processing and (decimal(processing["batch_size"]) < 1 or decimal(processing["batch_size"]) % 1):
        raise ValidationError("processing batch_size must be a positive integer")
    for key in ("duration", "setup"):
        if key in processing:
            value = processing[key]
            if not isinstance(value, dict) or set(value) != {"value", "unit"} or decimal(value["value"]) < 0 or registry.units.get(value["unit"], {}).get("dimension") != "duration":
                raise ValidationError("processing times must be nonnegative with a duration unit")
    bindings = processing.get("time_inputs", {})
    if not isinstance(bindings, dict) or len(bindings) > 20:
        raise ValidationError("time model inputs must be a bounded mapping")
    for binding in bindings.values():
        if not isinstance(binding, dict) or set(binding) != {"source", "field"} or binding["source"] not in ("facts", "item") or not FIELD.fullmatch(str(binding["field"])):
            raise ValidationError("time input mapping needs fact or item field")


def _acyclic(node_ids, edges, name):
    remaining = {node_id: 0 for node_id in node_ids}
    successors = defaultdict(list)
    for edge in edges:
        successors[edge["from"]].append(edge["to"])
        remaining[edge["to"]] += 1
    ready = deque(node_id for node_id, count in remaining.items() if count == 0)
    ordered = []
    while ready:
        current = ready.popleft()
        ordered.append(current)
        for target in successors[current]:
            remaining[target] -= 1
            if remaining[target] == 0:
                ready.append(target)
    if len(ordered) != len(node_ids):
        raise ValidationError(f"{name} contains a precedence cycle")
    return ordered


def validate_resource_plan(resources, registry):
    if not isinstance(resources, list) or len(resources) > MAX_ITEMS:
        raise ValidationError("resource plan must be a bounded list")
    for resource in resources:
        if not isinstance(resource, dict) or resource.get("kind") not in RESOURCE_TYPES:
            raise ValidationError("resource requirement has an unknown kind")
        if resource.get("basis", "cycle") not in ("cycle", "piece", "time", "batch"):
            raise ValidationError("unsupported resource quantity basis")
        if resource.get("ref") is not None and (not isinstance(resource["ref"], str) or not resource["ref"].strip()):
            raise ValidationError("resource requirement ref must be a nonempty reference")
        has_amount, has_unit = "amount" in resource, "unit" in resource
        if has_amount != has_unit and not (resource.get("quantity_model_ref") and has_unit and not has_amount):
            raise ValidationError("resource amount and registered unit must be supplied together")
        if has_amount:
            try:
                amount = Decimal(str(resource["amount"]))
            except (InvalidOperation, TypeError) as exc:
                raise ValidationError("resource amount must be a decimal number") from exc
            if not amount.is_finite() or amount <= 0 or resource["unit"] not in registry.units:
                raise ValidationError("resource amount must be positive and use a registered unit")
        if has_unit and resource.get("basis") == "time" and registry.units.get(resource["unit"], {}).get("dimension") != "duration":
            raise ValidationError("time resource basis requires a duration unit")
        model_ref = resource.get("quantity_model_ref")
        if model_ref:
            _validate_model_ref(model_ref, registry)
            if resource.get("unit") not in registry.units:
                raise ValidationError("quantity model resource needs a registered output unit")
            model = registry.models[model_ref.rsplit("@", 1)[0]]
            if registry.units.get(model["output_unit"], {}).get("dimension") != registry.units[resource["unit"]]["dimension"]:
                raise ValidationError("quantity model output and resource unit have incompatible dimensions")
            bindings = resource.get("quantity_inputs", {})
            if not isinstance(bindings, dict) or len(bindings) > 20:
                raise ValidationError("quantity model inputs must be a bounded mapping")
            for binding in bindings.values():
                if not isinstance(binding, dict) or set(binding) != {"source", "field"} or binding["source"] not in ("facts", "item", "process") or not FIELD.fullmatch(str(binding["field"])):
                    raise ValidationError("quantity input mapping requires facts, item or process field")


def _validate_model_ref(value, registry, *, output_dimension=None):
    if not isinstance(value, str) or "@" not in value:
        raise ValidationError("model reference must be model_id@version")
    identifier, version = value.rsplit("@", 1)
    model = registry.models.get(identifier)
    if not model or model.get("version") != version:
        raise ValidationError(f"unknown version-pinned model reference: {value}")
    if output_dimension and registry.units.get(model["output_unit"], {}).get("dimension") != output_dimension:
        raise ValidationError(f"model reference {value} must output {output_dimension}")


def _node(node_id, typ, key, label, attrs, relations):
    parts = node_id.split(":")
    # Stable route keys are local to their route version; reusable assets are
    # local to their own version. Preserve those keys without identity clashes.
    namespace = f"expert_template:{parts[1]}:{parts[2]}" if typ in ("route_group", "route_step") else f"expert_template:{parts[-1]}"
    return {"data": {
        "base": {"schema": {"ckm": "3.0"},
                 "node": {"id": node_id, "kind": "entity", "type": typ, "key": key,
                          "key_namespace": namespace, "label": label, "aliases": []},
                 "classification": {"tags": ["expert_template"]},
                 "lifecycle": {"state": "active"}, "version": {"entity_revision": 1}},
        "knowledge": {"attrs": attrs, "assertions": [], "relations": relations, "logic_refs": []},
        "external": {"assertions_ref": None}}, "body": ""}


class TemplateWorkbench:
    def __init__(self, service):
        self.service = service
        self.root = service.config.root

    @staticmethod
    def route_id(spec):
        return f"route:{spec['id']}:{spec['version']}"

    @staticmethod
    def operation_id(operation):
        return f"operation:{operation['id']}:{operation['version']}"

    @staticmethod
    def decision_id(table):
        return f"decision:{table['id']}:{table['version']}"

    def decision(self, ref, spec):
        table = next((d for d in spec.get("decisions", []) if self.decision_id(d) == ref), None)
        if table:
            return table
        if not isinstance(ref, str) or not DECISION_REF.fullmatch(ref):
            raise ValidationError("decision reference must pin a version")
        if ref in getattr(self.service, "_unpublished_decision_assets", lambda: set())():
            raise ValidationError(f"decision is not published: {ref}")
        _, key, version = ref.split(":")
        path = self.root / f"knowledge/decisions/{key}/v{version}.md"
        if not path.exists():
            raise ValidationError(f"unknown decision reference: {ref}")
        data, _ = frontmatter(path)
        return data["knowledge"]["attrs"]["decision_definition"]

    def validate_binding(self, binding, spec, *, allow_item, operation=False):
        if not isinstance(binding, dict) or set(binding) - {"ref", "output", "purpose", "equals"} or binding.get("purpose") not in ("enabled", "operation"):
            raise ValidationError("invalid route decision binding")
        if not operation and binding["purpose"] == "operation":
            raise ValidationError("only steps can select an operation")
        table = self.decision(binding.get("ref"), spec)
        validate_table(table, self.service.registry.units)
        if not allow_item and any(c["source"] == "item" for c in table["inputs"]):
            raise ValidationError("this route decision cannot use current item inputs")
        column = next((c for c in table["outputs"] if c["id"] == binding.get("output")), None)
        if not column:
            raise ValidationError("decision binding references an unknown output column")
        if binding["purpose"] == "operation":
            if table["hit_policy"] == "COLLECT" or column["type"] != "string":
                raise ValidationError("operation choice requires a single string output")
            choices = [r["then"][column["id"]] for r in table["rules"]]
            if table.get("default_output"):
                choices.append(table["default_output"][column["id"]])
            for ref in choices:
                if not isinstance(ref, str) or not OPERATION_REF.fullmatch(ref):
                    raise ValidationError("operation decision outputs must be version-pinned operation references")
        else:
            scalar(binding.get("equals", True), column["type"])

    @staticmethod
    def route_directory(spec):
        return f"knowledge/templates/{spec['id']}/v{spec['version']}"

    def validate(self, spec, *, check_refs=True):
        if not isinstance(spec, dict):
            raise ValidationError("template must be an object")
        _key(spec.get("id"), "template id")
        if not isinstance(spec.get("version"), str) or not VERSION.fullmatch(spec["version"]):
            raise ValidationError("template version must be semantic major.minor.patch")
        for name in ("label", "family", "output_basis"):
            _nonempty(spec.get(name), name)
        groups, steps, edges, operations, cases = (spec.get(name, []) for name in ("groups", "steps", "edges", "operations", "cases"))
        decisions = spec.get("decisions", [])
        if not isinstance(decisions, list) or len(decisions) > MAX_ITEMS:
            raise ValidationError("decisions must be a bounded list")
        decision_ids = set()
        for table in decisions:
            validate_table(table, self.service.registry.units)
            ref = self.decision_id(table)
            if ref in decision_ids:
                raise ValidationError("duplicate decision version")
            decision_ids.add(ref)
            if check_refs and (self.root / f"knowledge/decisions/{table['id']}/v{table['version']}.md").exists():
                raise ConflictError(f"decision version already exists: {ref}")
        if any(not isinstance(value, list) or len(value) > MAX_ITEMS for value in (groups, steps, edges, operations, cases)):
            raise ValidationError("template collections must be bounded arrays")
        if not steps or not cases:
            raise ValidationError("template requires steps and at least one expert sample case")
        ids = set()
        for group in groups:
            if not isinstance(group, dict):
                raise ValidationError("group must be an object")
            key = _key(group.get("id"), "group id")
            if key in ids:
                raise ValidationError(f"duplicate route item {key}")
            ids.add(key)
            _nonempty(group.get("label"), f"group {key} label")
            if group.get("group_mode", "repeat") not in ("repeat", "conditional"):
                raise ValidationError("unknown group mode")
            if group.get("group_mode", "repeat") == "repeat":
                _key(group.get("iteration_set"), f"group {key} iteration_set")
            if group.get("execution_mode", "parallel") not in ("parallel", "sequential"):
                raise ValidationError("unknown repeat execution mode")
            if group.get("join_policy", "all") != "all":
                raise ValidationError("only all-complete group joins are supported")
            _condition(group.get("when"), f"group {key} condition")
            if uses_item(group.get("when")):
                raise ValidationError("group inclusion condition must use scenario facts")
            if group.get("decision_binding"):
                self.validate_binding(group["decision_binding"], spec, allow_item=False)
        group_ids = {item["id"] for item in groups}
        for step in steps:
            if not isinstance(step, dict):
                raise ValidationError("step must be an object")
            key = _key(step.get("id"), "step id")
            if key in ids:
                raise ValidationError(f"duplicate route item {key}")
            ids.add(key)
            _nonempty(step.get("label"), f"step {key} label")
            _nonempty(step.get("cost_basis"), f"step {key} cost_basis")
            if not isinstance(step.get("operation_ref"), str) or not OPERATION_REF.fullmatch(step["operation_ref"]):
                raise ValidationError(f"step {key} requires a version-pinned operation template reference")
            if step.get("parent") is not None and step["parent"] not in group_ids:
                raise ValidationError(f"step {key} has an unknown parent group")
            _condition(step.get("when"), f"step {key} condition")
            allow_item = step.get("parent") is not None and next(g for g in groups if g["id"] == step["parent"]).get("group_mode", "repeat") == "repeat"
            if not allow_item and uses_item(step.get("when")):
                raise ValidationError("top-level step cannot use an item condition")
            if step.get("decision_binding"):
                self.validate_binding(step["decision_binding"], spec, allow_item=allow_item, operation=True)
        if any(not any(step.get("parent") == group_id for step in steps) for group_id in group_ids):
            raise ValidationError("every route group must contain at least one step")
        scope = {item["id"]: None for item in groups}
        scope.update({item["id"]: item.get("parent") for item in steps})
        edge_keys = set()
        for edge in edges:
            if not isinstance(edge, dict) or set(edge) != {"from", "to"}:
                raise ValidationError("precedence edge requires from and to")
            source, target = edge["from"], edge["to"]
            if source not in ids or target not in ids or source == target or scope[source] != scope[target]:
                raise ValidationError("precedence endpoints must be distinct peers in one route scope")
            if (source, target) in edge_keys:
                raise ValidationError("duplicate precedence edge")
            edge_keys.add((source, target))
        for parent in {None, *group_ids}:
            members = [key for key, value in scope.items() if value == parent]
            _acyclic(members, [e for e in edges if scope[e["from"]] == parent], str(parent or "route"))
        if len([key for key, value in scope.items() if value is None]) < 1:
            raise ValidationError("route has no top-level item")
        new_operation_ids = set()
        for operation in operations:
            if not isinstance(operation, dict):
                raise ValidationError("operation must be an object")
            _key(operation.get("id"), "operation id")
            if not isinstance(operation.get("version"), str) or not VERSION.fullmatch(operation["version"]):
                raise ValidationError("operation version must be semantic major.minor.patch")
            _nonempty(operation.get("label"), "operation label")
            _nonempty(operation.get("cost_basis"), "operation cost_basis")
            if operation.get("method") is not None:
                _nonempty(operation["method"], "operation method")
            validate_resource_plan(operation.get("resources", []), self.service.registry)
            if "processing" in operation:
                validate_processing(operation["processing"], self.service.registry)
            if operation.get("time_model_ref"):
                _validate_model_ref(operation["time_model_ref"], self.service.registry, output_dimension="duration")
            operation_id = self.operation_id(operation)
            if operation_id in new_operation_ids:
                raise ValidationError(f"duplicate operation {operation_id}")
            new_operation_ids.add(operation_id)
            if check_refs and (self.root / f"knowledge/operations/{operation['id']}/v{operation['version']}.md").exists():
                raise ConflictError(f"operation version already exists: {operation_id}")
        refs = {s["operation_ref"] for s in steps}
        for step in steps:
            binding = step.get("decision_binding")
            if binding and binding["purpose"] == "operation":
                table = self.decision(binding["ref"], spec)
                refs.update(r["then"][binding["output"]] for r in table["rules"])
                if table.get("default_output"):
                    refs.add(table["default_output"][binding["output"]])
        hidden = getattr(self.service, "_unpublished_template_assets", lambda: (set(), set()))()[1]
        for ref in refs - new_operation_ids:
            row = self.service.db.first("SELECT type FROM node WHERE id=?", (ref,))
            if not row or row["type"] != "operation_template" or ref in hidden:
                raise ValidationError(f"unknown or unpublished operation template {ref}")
        case_ids = set()
        for case in cases:
            if not isinstance(case, dict):
                raise ValidationError("sample case must be an object")
            key = _key(case.get("id"), "case id")
            if key in case_ids:
                raise ValidationError(f"duplicate sample case {key}")
            case_ids.add(key)
            if not isinstance(case.get("facts", {}), dict) or not isinstance(case.get("sets", {}), dict):
                raise ValidationError(f"case {key} requires facts and sets objects")
            for set_id, items in case.get("sets", {}).items():
                _key(set_id, "sample set id")
                if not isinstance(items, list) or len(items) > MAX_ITEMS or any(not isinstance(item, dict) or not isinstance(item.get("id"), str) for item in items):
                    raise ValidationError(f"case {key} set {set_id} requires bounded items with id")
                if len({item["id"] for item in items}) != len(items):
                    raise ValidationError(f"case {key} set {set_id} has duplicate item ids")
                for item in items:
                    if not SAMPLE_ITEM.fullmatch(item["id"]):
                        raise ValidationError("sample object id has invalid characters")
            expected = case.get("expected_operations")
            if not isinstance(expected, int) or isinstance(expected, bool) or expected < 0:
                raise ValidationError("expected_operations must be a nonnegative integer")
        return {"group_count": len(groups), "step_count": len(steps), "operation_count": len(operations), "case_count": len(cases)}

    def preview(self, spec, case):
        self.validate(spec, check_refs=False)
        if not isinstance(case, dict) or not isinstance(case.get("facts", {}), dict) or not isinstance(case.get("sets", {}), dict):
            raise ValidationError("preview case needs facts and sets objects")
        facts, sets = case.get("facts", {}), case.get("sets", {})
        for set_id, items in sets.items():
            if not isinstance(items, list) or len(items) > MAX_ITEMS:
                raise ValidationError(f"iteration set {set_id} must be a bounded array")
            identifiers = [item.get("id") if isinstance(item, dict) else None for item in items]
            if any(not isinstance(x, str) or not SAMPLE_ITEM.fullmatch(x) for x in identifiers) or len(set(identifiers)) != len(identifiers):
                raise ValidationError(f"iteration set {set_id} requires unique stable item ids")
        instances, links, traces, skipped, bounds = [], [], [], [], {}
        decision_cache = {}

        def inclusion(node, item):
            enabled, operation = _compare(node.get("when"), facts, item), node.get("operation_ref")
            binding = node.get("decision_binding")
            if enabled and binding:
                cache_key = (binding["ref"], id(item))
                if cache_key not in decision_cache:
                    decision_cache[cache_key] = evaluate_table(self.decision(binding["ref"], spec), facts, item, self.service.registry.units, validated=True)
                result = decision_cache[cache_key]
                traces.append({**result, "route_key": node["id"], "item_id": item.get("id")})
                if binding["purpose"] == "operation":
                    operation = result["result"][binding["output"]]
                else:
                    table = self.decision(binding["ref"], spec)
                    typ = next(c["type"] for c in table["outputs"] if c["id"] == binding["output"])
                    values = result["result"] if isinstance(result["result"], list) else [result["result"]]
                    enabled = any(scalar(v[binding["output"]], typ) == scalar(binding.get("equals", True), typ) for v in values)
            if not enabled:
                skipped.append({"route_key": node["id"], "item_id": item.get("id"), "label": node["label"]})
            return enabled, operation

        def contract(local_bounds, edges):
            # A skipped optional node is a pass-through, never a broken dependency.
            successors = defaultdict(list)
            for edge in edges:
                successors[edge["from"]].append(edge["to"])
            result = []
            for source, (_, exits) in local_bounds.items():
                if not exits:
                    continue
                pending, seen = list(successors[source]), set()
                while pending:
                    target = pending.pop()
                    if target in seen:
                        continue
                    seen.add(target)
                    entries = local_bounds[target][0]
                    if entries:
                        result.extend({"from": x, "to": y} for x in exits for y in entries)
                        if len(result) > MAX_EXPANDED * 10:
                            raise ValidationError("expanded dependencies exceed route limit")
                    else:
                        pending.extend(successors[target])
            return result

        def expand_steps(steps, item, prefix="", group_id=None):
            local_bounds = {}
            for step in steps:
                enabled, operation = inclusion(step, item)
                identifier = prefix + step["id"]
                local_bounds[step["id"]] = ([identifier], [identifier]) if enabled else ([], [])
                if enabled:
                    if len(instances) >= MAX_EXPANDED:
                        raise ValidationError("expanded route exceeds operation limit")
                    instances.append({"id": identifier, "step_key": step["id"], "label": step["label"],
                                      "operation_ref": operation, "cost_basis": step["cost_basis"], "item_id": item.get("id"),
                                      **({"group_id": group_id} if group_id else {})})
            ids = set(local_bounds)
            internal = contract(local_bounds, [e for e in spec["edges"] if e["from"] in ids and e["to"] in ids])
            return local_bounds, internal

        top_steps = [s for s in spec["steps"] if s.get("parent") is None]
        bounds, _ = expand_steps(top_steps, {})
        for group in spec["groups"]:
            key = group["id"]
            enabled, _ = inclusion(group, {})
            entries, exits = [], []
            if enabled:
                if group.get("group_mode", "repeat") == "conditional":
                    items = [{}]
                else:
                    source = group["iteration_set"]
                    if source not in sets:
                        raise ValidationError(f"missing iteration set: {source}")
                    items = sets[source]
                for item in items:
                    prefix = f"{key}/{item['id']}/" if item else f"{key}/"
                    children = [s for s in spec["steps"] if s.get("parent") == key]
                    local, internal = expand_steps(children, item, prefix, key)
                    active = [x for starts, _ in local.values() for x in starts]
                    incoming, outgoing = {e["to"] for e in internal}, {e["from"] for e in internal}
                    starts, ends = [x for x in active if x not in incoming], [x for x in active if x not in outgoing]
                    links.extend(internal)
                    if group.get("execution_mode", "parallel") == "sequential":
                        if starts:
                            links.extend({"from": x, "to": y} for x in exits for y in starts)
                            if not entries:
                                entries = starts
                            exits = ends
                    else:
                        entries.extend(starts); exits.extend(ends)
            bounds[key] = (entries, exits)
        top_ids = set(bounds)
        links.extend(contract(bounds, [e for e in spec["edges"] if e["from"] in top_ids and e["to"] in top_ids]))
        links = list({(e["from"], e["to"]): e for e in links}.values())
        if len(links) > MAX_EXPANDED * 10:
            raise ValidationError("expanded dependencies exceed route limit")
        ordered = _acyclic([i["id"] for i in instances], links, "expanded route")
        by_id = {i["id"]: i for i in instances}
        instances = [by_id[key] for key in ordered]
        counts = dict(sorted(Counter(i["operation_ref"] for i in instances).items()))
        expected = case.get("expected_operations")
        return {"template_id": self.route_id(spec), "case_id": case.get("id"), "instances": instances, "edges": links,
                "operation_counts": counts, "operation_count": len(instances),
                "matches_expected": expected is None or expected == len(instances), "decisions": traces, "skipped": skipped}

    def render(self, spec, actor):
        self.validate(spec)
        _nonempty(actor, "template author")
        directory = self.route_directory(spec)
        route_id = self.route_id(spec)
        if (self.root / directory / "template.md").exists():
            raise ConflictError("template version already exists; create a new version")
        if self.service.db.first("SELECT 1 FROM node WHERE id=?", (route_id,)):
            raise ConflictError("template id already exists in another source")
        group_ids = {group["id"]: f"route-group:{spec['id']}:{spec['version']}:{group['id']}" for group in spec["groups"]}
        step_ids = {step["id"]: f"route-step:{spec['id']}:{spec['version']}:{step['id']}" for step in spec["steps"]}
        for node_id in [*group_ids.values(), *step_ids.values(), *(self.operation_id(item) for item in spec.get("operations", [])),
                        *(self.decision_id(item) for item in spec.get("decisions", []))]:
            if self.service.db.first("SELECT 1 FROM node WHERE id=?", (node_id,)):
                raise ConflictError(f"template package node already exists: {node_id}")
        item_ids = {**group_ids, **step_ids}
        documents = {}
        top = [{"predicate": "route_contains", "target": item_ids[item["id"]]} for item in [*spec["groups"], *spec["steps"]] if item.get("parent") is None]
        attrs = {"template_family": spec["family"], "template_version": spec["version"],
                "output_basis": spec["output_basis"], "template_cases": {"cases": spec["cases"]}, "template_author": actor}
        if spec.get("decisions"):
            attrs["decision_assets"] = {"refs": [self.decision_id(d) for d in spec["decisions"]]}
        if spec.get("summary"):
            attrs["summary"] = spec["summary"]
        documents[f"{directory}/template.md"] = _node(route_id, "route_template", spec["id"], spec["label"], attrs, top)
        for group in spec["groups"]:
            key = group["id"]
            relations = [{"predicate": "group_contains", "target": step_ids[step["id"]]} for step in spec["steps"] if step.get("parent") == key]
            relations += [{"predicate": "route_precedes", "target": item_ids[e["to"]]} for e in spec["edges"] if e["from"] == key]
            attrs = {"route_key": key, "iteration_set": group.get("iteration_set", "items"), "join_policy": "all"}
            for field, predicate in (("decision_binding", "decision_binding"),):
                if group.get(field):
                    attrs[predicate] = group[field]
            if "group_mode" in group or "execution_mode" in group:
                attrs["group_execution"] = {"group_mode": group.get("group_mode", "repeat"), "execution_mode": group.get("execution_mode", "parallel")}
            if group.get("when"):
                attrs["template_condition"] = group["when"]
            documents[f"{directory}/groups/{key}.md"] = _node(group_ids[key], "route_group", key, group["label"], attrs, relations)
        for step in spec["steps"]:
            key = step["id"]
            relations = [{"predicate": "route_uses_operation", "target": step["operation_ref"]}]
            relations += [{"predicate": "route_precedes", "target": item_ids[e["to"]]} for e in spec["edges"] if e["from"] == key]
            attrs = {"route_key": key, "cost_basis": step["cost_basis"]}
            if step.get("decision_binding"):
                attrs["decision_binding"] = step["decision_binding"]
            if step.get("when"):
                attrs["template_condition"] = step["when"]
            documents[f"{directory}/steps/{key}.md"] = _node(step_ids[key], "route_step", key, step["label"], attrs, relations)
        for operation in spec.get("operations", []):
            operation_id = self.operation_id(operation)
            op_attrs = {"cost_basis": operation["cost_basis"]}
            if operation.get("method"):
                op_attrs["operation_method"] = operation["method"]
            if operation.get("resources"):
                op_attrs["resource_plan"] = {"resources": operation["resources"]}
            if operation.get("time_model_ref"):
                op_attrs["time_model_ref"] = operation["time_model_ref"]
            if operation.get("processing"):
                op_attrs["operation_processing"] = operation["processing"]
            if operation.get("summary"):
                op_attrs["summary"] = operation["summary"]
            documents[f"knowledge/operations/{operation['id']}/v{operation['version']}.md"] = _node(
                operation_id, "operation_template", operation["id"], operation["label"], op_attrs, [])
        for table in spec.get("decisions", []):
            documents[f"knowledge/decisions/{table['id']}/v{table['version']}.md"] = _node(
                self.decision_id(table), "decision_table", table["id"], table["label"], {"decision_definition": table}, [])
        return documents

    def list_templates(self):
        result = []
        for path in sorted((self.root / "knowledge/templates").glob("*/v*/template.md")):
            data, _ = frontmatter(path)
            node = data["base"]["node"]
            attrs = data["knowledge"]["attrs"]
            result.append({"id": node["id"], "key": node["key"], "label": node["label"],
                           "version": attrs["template_version"], "family": attrs["template_family"],
                           "output_basis": attrs["output_basis"], "author": attrs["template_author"],
                           "source_path": str(path.relative_to(self.root))})
        return result

    def list_operations(self):
        result = []
        for path in sorted((self.root / "knowledge/operations").glob("*/v*.md")):
            data, _ = frontmatter(path)
            node = data["base"]["node"]
            result.append({"id": node["id"], "label": node["label"],
                           "cost_basis": data["knowledge"]["attrs"]["cost_basis"],
                           "method": data["knowledge"]["attrs"].get("operation_method"),
                           "resources": data["knowledge"]["attrs"].get("resource_plan", {}).get("resources", []),
                           "processing": data["knowledge"]["attrs"].get("operation_processing"),
                           "time_model_ref": data["knowledge"]["attrs"].get("time_model_ref")})
        return result

    def list_decisions(self):
        return [frontmatter(p)[0]["knowledge"]["attrs"]["decision_definition"] for p in sorted((self.root / "knowledge/decisions").glob("*/v*.md"))]

    def get(self, template_id):
        match = re.fullmatch(r"route:([a-z][a-z0-9_-]{0,63}):([1-9][0-9]*\.[0-9]+\.[0-9]+)", str(template_id))
        if not match:
            raise ValidationError("invalid route template id")
        directory = self.root / f"knowledge/templates/{match[1]}/v{match[2]}"
        root_path = directory / "template.md"
        if not root_path.is_file():
            raise NotFoundError(f"route template not found: {template_id}")
        root, _ = frontmatter(root_path)
        attrs = root["knowledge"]["attrs"]
        spec = {"id": match[1], "version": match[2], "label": root["base"]["node"]["label"],
                "family": attrs["template_family"], "output_basis": attrs["output_basis"],
                "summary": attrs.get("summary", ""), "author": attrs["template_author"],
                "groups": [], "steps": [], "edges": [], "operations": [], "cases": attrs["template_cases"]["cases"]}
        if attrs.get("decision_assets"):
            spec["decisions"] = []
            for ref in attrs["decision_assets"]["refs"]:
                if not DECISION_REF.fullmatch(ref):
                    raise ValidationError("invalid decision asset reference")
                _, key, version = ref.split(":")
                spec["decisions"].append(frontmatter(self.root / f"knowledge/decisions/{key}/v{version}.md")[0]["knowledge"]["attrs"]["decision_definition"])
        parents = {}
        for path in sorted((directory / "groups").glob("*.md")):
            data, _ = frontmatter(path)
            node, values = data["base"]["node"], data["knowledge"]["attrs"]
            spec["groups"].append({"id": values["route_key"], "label": node["label"],
                                   "iteration_set": values["iteration_set"], "join_policy": values["join_policy"],
                                   **({"when": values["template_condition"]} if "template_condition" in values else {})})
            spec["groups"][-1].update(values.get("group_execution", {}))
            if values.get("decision_binding"):
                spec["groups"][-1]["decision_binding"] = values["decision_binding"]
            for relation in data["knowledge"]["relations"]:
                if relation["predicate"] == "group_contains":
                    if relation["target"] in parents:
                        raise ValidationError("route step belongs to multiple groups")
                    parents[relation["target"]] = values["route_key"]
        for path in sorted((directory / "steps").glob("*.md")):
            data, _ = frontmatter(path)
            node, values = data["base"]["node"], data["knowledge"]["attrs"]
            refs = [r["target"] for r in data["knowledge"]["relations"] if r["predicate"] == "route_uses_operation"]
            if len(refs) != 1:
                raise ValidationError("each route step must reference exactly one operation template")
            spec["steps"].append({"id": values["route_key"], "label": node["label"],
                                  "cost_basis": values["cost_basis"], "operation_ref": refs[0],
                                  "parent": parents.get(node["id"]),
                                  **({"when": values["template_condition"]} if "template_condition" in values else {})})
            if values.get("decision_binding"):
                spec["steps"][-1]["decision_binding"] = values["decision_binding"]
        id_to_key = {f"route-group:{match[1]}:{match[2]}:{g['id']}": g["id"] for g in spec["groups"]}
        id_to_key.update({f"route-step:{match[1]}:{match[2]}:{s['id']}": s["id"] for s in spec["steps"]})
        actual_top = {r["target"] for r in root["knowledge"]["relations"] if r["predicate"] == "route_contains"}
        expected_top = {node_id for node_id, key in id_to_key.items()
                        if key in {group["id"] for group in spec["groups"]} or
                        key in {step["id"] for step in spec["steps"] if step.get("parent") is None}}
        if actual_top != expected_top:
            raise ValidationError("route template containment does not match its authored group and step files")
        for path in [*(directory / "groups").glob("*.md"), *(directory / "steps").glob("*.md")]:
            data, _ = frontmatter(path)
            source = id_to_key[data["base"]["node"]["id"]]
            for relation in data["knowledge"]["relations"]:
                if relation["predicate"] == "route_precedes":
                    if relation["target"] not in id_to_key:
                        raise ValidationError("route precedence target is outside its template")
                    spec["edges"].append({"from": source, "to": id_to_key[relation["target"]]})
        return spec


def serialize_document(document):
    return "---\n" + yaml.safe_dump(document["data"], allow_unicode=True, sort_keys=False) + "---\n" + document.get("body", "")
