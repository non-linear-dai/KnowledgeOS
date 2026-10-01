"""Bounded, deterministic business constraint and rule contracts."""
from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation

from .core import ValidationError


KINDS = {"business_constraint": "knowledgeos.business-constraint.v1",
         "business_rule": "knowledgeos.business-rule.v1"}
OPERATORS = {"eq", "ne", "gt", "gte", "lt", "lte"}
ROLES = {"subject", "candidate"}


def _version(value):
    if not re.fullmatch(r"\d+\.\d+\.\d+", str(value)):
        raise ValidationError("business definition requires a semantic version")
    return tuple(map(int, str(value).split(".")))


def validate_business(definition, kind, predicates, ontology, units):
    if kind not in KINDS or not isinstance(definition, dict) or definition.get("format") != KINDS[kind]:
        raise ValidationError("invalid business definition format")
    if not re.fullmatch(r"[a-z][a-z0-9_]*", str(definition.get("id", ""))):
        raise ValidationError("business definition id must use lowercase letters, digits, and underscores")
    if not str(definition.get("label", "")).strip() or not str(definition.get("description", "")).strip():
        raise ValidationError("business definition needs a label and description")
    _version(definition.get("version"))
    if definition.get("status", {}).get("lifecycle") not in ("draft", "active", "deprecated"):
        raise ValidationError("business lifecycle must be draft, active, or deprecated")
    scope = definition.get("scope")
    concepts = {item["id"] for item in ontology.get("concept_types", [])}
    if not isinstance(scope, dict) or scope.get("subject_concept") not in concepts:
        raise ValidationError("business scope requires a registered subject concept")
    if kind == "business_rule" and scope.get("candidate_concept") not in concepts:
        raise ValidationError("business rule requires a registered candidate concept")
    if kind == "business_rule" and scope.get("relation_type"):
        relation = next((item for item in ontology.get("relation_types", []) if item["id"] == scope["relation_type"]), None)
        if not relation or (scope["subject_concept"], scope["candidate_concept"]) not in {
            (edge["source_type"], edge["target_type"]) for edge in relation.get("connections", [])
        }:
            raise ValidationError("business relation type must connect the declared subject and candidate concepts")
    inputs = definition.get("inputs")
    if not isinstance(inputs, list) or not inputs or len(inputs) > 20:
        raise ValidationError("business definition requires 1-20 inputs")
    names = set()
    input_predicates = {}
    for item in inputs:
        if not isinstance(item, dict) or not re.fullmatch(r"[a-z][a-z0-9_]*", str(item.get("id", ""))):
            raise ValidationError("business input requires a stable id")
        if item["id"] in names:
            raise ValidationError(f"duplicate business input {item['id']}")
        names.add(item["id"])
        if item.get("predicate") not in predicates:
            raise ValidationError(f"unknown business input predicate {item.get('predicate')}")
        if predicates[item["predicate"]].get("value", {}).get("type") not in ("number", "decimal", "quantity", "string", "text", "enum", "boolean", "date", "datetime"):
            raise ValidationError("business input predicate has an unsupported value type")
        if item.get("role") not in ROLES or (kind == "business_constraint" and item["role"] != "subject"):
            raise ValidationError("invalid business input role")
        concept_id = scope[f"{item['role']}_concept"]
        concept = next(value for value in ontology["concept_types"] if value["id"] == concept_id)
        if concept.get("properties") and item["predicate"] not in {binding["predicate"] for binding in concept["properties"]}:
            raise ValidationError(f"predicate {item['predicate']} is not bound to concept {concept_id}")
        input_predicates[item["id"]] = predicates[item["predicate"]]
    checks = definition.get("checks")
    if not isinstance(checks, list) or not checks or len(checks) > 20:
        raise ValidationError("business definition requires 1-20 checks")
    for check in checks:
        if not isinstance(check, dict) or check.get("left") not in names or check.get("operator") not in OPERATORS:
            raise ValidationError("business check requires a registered left input and comparison operator")
        right = check.get("right")
        if not isinstance(right, dict) or set(right) not in ({"input"}, {"value"}):
            raise ValidationError("business check right side must be one input or one literal value")
        if "input" in right and right["input"] not in names:
            raise ValidationError(f"unknown business comparison input {right['input']}")
        if "input" in right:
            left_value = input_predicates[check["left"]].get("value", {})
            right_value = input_predicates[right["input"]].get("value", {})
            if left_value.get("type") != right_value.get("type") or left_value.get("dimension") != right_value.get("dimension"):
                raise ValidationError("business comparison inputs must have matching types and dimensions")
        else:
            _comparable(right["value"], input_predicates[check["left"]], units)
        if check["operator"] in ("gt", "gte", "lt", "lte") and input_predicates[check["left"]].get("value", {}).get("type") not in ("number", "decimal", "quantity", "date", "datetime"):
            raise ValidationError("ordered business comparisons require numeric or temporal values")
        if "message" in check and not isinstance(check["message"], str):
            raise ValidationError("business check message must be text")
    return definition


def _number(value):
    if isinstance(value, bool):
        raise ValidationError("boolean cannot be used as a numeric business value")
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError) as exc:
        raise ValidationError(f"invalid numeric business value {value}") from exc
    if not result.is_finite():
        raise ValidationError("business numeric value must be finite")
    return result


def _comparable(value, predicate, units):
    if isinstance(value, dict) and ("value" in value or "literal" in value) and "unit" in value:
        unit = units.get(value["unit"])
        if not unit or unit.get("dimension") != predicate.get("value", {}).get("dimension"):
            raise ValidationError(f"incompatible business unit {value['unit']}")
        return _number(value.get("value", value.get("literal"))) * _number(unit["factor_to_base"])
    value_type = predicate.get("value", {}).get("type")
    if value_type == "quantity":
        raise ValidationError("quantity business value requires value and unit")
    if isinstance(value, dict) and "literal" in value:
        value = value["literal"]
    if value_type in ("number", "decimal"):
        return _number(value)
    if value_type == "boolean" and not isinstance(value, bool):
        raise ValidationError("boolean business value must be true or false")
    if value_type in ("string", "text", "enum", "date", "datetime") and not isinstance(value, str):
        raise ValidationError("text or temporal business value must be text")
    return value


def evaluate_business(definition, kind, facts, predicates, ontology, units):
    validate_business(definition, kind, predicates, ontology, units)
    if not isinstance(facts, dict) or not isinstance(facts.get("subject"), dict):
        raise ValidationError("business facts require a subject object")
    candidates = facts.get("candidates", []) if kind == "business_rule" else [None]
    if kind == "business_rule" and (not isinstance(candidates, list) or len(candidates) > 500 or any(not isinstance(item, dict) for item in candidates)):
        raise ValidationError("business rule requires up to 500 candidate objects")
    input_roles = {item["id"]: item["role"] for item in definition["inputs"]}
    results = []
    for index, candidate in enumerate(candidates):
        bound, traces = {}, []
        for item in definition["inputs"]:
            source = facts["subject"] if item["role"] == "subject" else candidate
            raw = source.get(item["predicate"]) if source else None
            bound[item["id"]] = None if raw is None else _comparable(raw, predicates[item["predicate"]], units)
        for check in definition["checks"]:
            right = check["right"]
            subject_only = input_roles[check["left"]] == "subject" and (
                "input" not in right or input_roles[right["input"]] == "subject"
            )
            lhs = bound[check["left"]]
            rhs = bound[right["input"]] if "input" in right else _comparable(right["value"], predicates[next(item["predicate"] for item in definition["inputs"] if item["id"] == check["left"])], units)
            if lhs is None or rhs is None:
                passed = None
            else:
                try:
                    passed = {"eq": lambda: lhs == rhs, "ne": lambda: lhs != rhs, "gt": lambda: lhs > rhs,
                              "gte": lambda: lhs >= rhs, "lt": lambda: lhs < rhs, "lte": lambda: lhs <= rhs}[check["operator"]]()
                except TypeError as exc:
                    raise ValidationError("business comparison uses incompatible value types") from exc
            traces.append({"left": check["left"], "operator": check["operator"], "right": right,
                           "passed": passed, "stage": "subject_match" if subject_only else "candidate_condition",
                           "message": check.get("message", ""),
                           "left_value": str(lhs) if lhs is not None else None, "right_value": str(rhs) if rhs is not None else None})
        if kind == "business_constraint":
            if any(trace["passed"] is False for trace in traces):
                verdict = "invalid"
            elif any(trace["passed"] is None for trace in traces):
                verdict = "unknown"
            else:
                verdict = "valid"
        else:
            subject_checks = [trace["passed"] for trace in traces if trace["stage"] == "subject_match"]
            candidate_checks = [trace["passed"] for trace in traces if trace["stage"] == "candidate_condition"]
            if False in subject_checks:
                verdict = "not_matched"
            elif None in subject_checks:
                verdict = "unknown"
            elif False in candidate_checks:
                verdict = "condition_failed"
            elif None in candidate_checks:
                verdict = "unknown"
            else:
                verdict = "eligible"
        results.append({"candidate_index": index if candidate is not None else None,
                        "candidate_id": candidate.get("id") if candidate else None,
                        "verdict": verdict,
                        "checks": traces})
    return {"definition_id": definition["id"], "version": definition["version"],
            "kind": kind, "results": results,
            "eligible_candidate_ids": [item["candidate_id"] for item in results if item["verdict"] == "eligible" and item["candidate_id"]],
            "relation_type": definition["scope"].get("relation_type") if kind == "business_rule" else None}
