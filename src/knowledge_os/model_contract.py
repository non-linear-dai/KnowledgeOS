"""Validation for the bounded declarative model language and ontology bindings."""
from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation

from .core import ValidationError


OPERATIONS = {"input", "const", "add", "sum", "multiply", "subtract", "divide", "date_diff_days"}
LEGACY_UNITS = {
    "currency": {"money": 1}, "currency_per_unit": {"money": 1, "piece": -1},
    "currency_per_hour": {"money": 1, "duration": -1}, "hour_per_unit": {"duration": 1, "piece": -1},
    "minute_per_unit": {"duration": 1, "piece": -1}, "calendar_days": {"duration": 1},
    "count": {"count": 1}, "risk_points": {"risk": 1}, "iso8601_date": {"date": 1},
    "risk_points_per_calendar_day": {"risk": 1, "duration": -1},
    "risk_points_per_count": {"risk": 1, "count": -1},
}
LEGACY_SCALES = {
    "currency": "1", "currency_per_unit": "1",
    "hour_per_unit": "3600", "minute_per_unit": "60", "calendar_days": "86400",
    "count": "1", "risk_points": "1", "iso8601_date": "1",
    "risk_points_per_count": "1",
}


def unit_scale(unit, units):
    """Return the exact multiplier to the shared arithmetic basis."""
    if unit in units:
        return Decimal(str(units[unit]["factor_to_base"]))
    if unit in ("currency_per_hour", "risk_points_per_calendar_day"):
        return Decimal(1) / Decimal(3600 if unit == "currency_per_hour" else 86400)
    if unit in LEGACY_SCALES:
        return Decimal(LEGACY_SCALES[unit])
    raise ValidationError(f"unknown model unit {unit}")


def _signature(unit, units):
    if unit == "one":
        return {}
    if unit in units:
        dimension = units[unit]["dimension"]
        if dimension == "energy":
            return {"power": 1, "duration": 1}
        return {} if dimension == "dimensionless" else {dimension: 1}
    if unit in LEGACY_UNITS:
        return LEGACY_UNITS[unit]
    raise ValidationError(f"unknown model unit {unit}; register physical units before use")


def _combine(left, right, sign=1):
    result = left.copy()
    for key, exponent in right.items():
        result[key] = result.get(key, 0) + sign * exponent
        if result[key] == 0:
            del result[key]
    return result


def validate_model(model, predicates, ontology, units):
    identifier = model.get("id")
    if not isinstance(identifier, str) or not re.fullmatch(r"[a-z][a-z0-9_]*", identifier):
        raise ValidationError("model requires a stable lowercase id")
    if not re.fullmatch(r"\d+\.\d+\.\d+", str(model.get("version", ""))):
        raise ValidationError(f"model {identifier} requires a semantic version")
    if model.get("status", {}).get("lifecycle", "active") not in ("draft", "active", "deprecated", "retired"):
        raise ValidationError(f"model {identifier} has an invalid lifecycle")
    inputs = model.get("inputs")
    if not isinstance(inputs, list) or not inputs:
        raise ValidationError(f"model {identifier} requires inputs")
    names = set()
    for definition in inputs:
        if not isinstance(definition, dict) or not isinstance(definition.get("id"), str) or definition["id"] in names:
            raise ValidationError(f"model {identifier} has invalid or duplicate input")
        names.add(definition["id"])
        if not isinstance(definition.get("unit"), str) or not definition["unit"]:
            raise ValidationError(f"model {identifier} input {definition['id']} requires unit")
        _signature(definition["unit"], units)
        if definition.get("predicate") and definition["predicate"] not in predicates:
            raise ValidationError(f"model {identifier} input {definition['id']} has unknown predicate")
    if not isinstance(model.get("output_unit"), str) or not model["output_unit"]:
        raise ValidationError(f"model {identifier} requires output_unit")
    expected_signature = _signature(model["output_unit"], units)
    if model.get("output_predicate") and model["output_predicate"] not in predicates:
        raise ValidationError(f"model {identifier} has unknown output predicate")
    if model.get("applies_to"):
        concept = next((item for item in ontology.get("concept_types", []) if item.get("id") == model["applies_to"]), None)
        if not concept:
            raise ValidationError(f"model {identifier} has unknown applies_to concept")
        bound = {item["predicate"] for item in concept.get("properties", [])}
        if model.get("output_predicate") not in bound:
            raise ValidationError(f"model {identifier} output predicate is not bound to {model['applies_to']}")
        for definition in inputs:
            if definition.get("source") != "subject" or definition.get("predicate") not in bound:
                raise ValidationError(f"model {identifier} input {definition['id']} requires a bound subject predicate")
            if predicates[definition["predicate"]].get("storage", {}).get("mode") not in ("assertion", "external"):
                raise ValidationError(f"model {identifier} input {definition['id']} requires an assertion predicate")
        if predicates[model["output_predicate"]].get("storage", {}).get("mode") not in ("assertion", "external"):
            raise ValidationError(f"model {identifier} output requires an assertion predicate")
    elif any(item.get("predicate") or item.get("source") for item in inputs) or model.get("output_predicate"):
        raise ValidationError(f"model {identifier} predicate bindings require applies_to")
    referenced = set()

    def walk(node):
        if not isinstance(node, dict) or node.get("op") not in OPERATIONS:
            raise ValidationError(f"model {identifier} contains an unsupported formula operation")
        op = node["op"]
        if op == "input":
            if node.get("id") not in names:
                raise ValidationError(f"model {identifier} references an unknown input")
            referenced.add(node["id"])
            return _signature(next(item["unit"] for item in inputs if item["id"] == node["id"]), units)
        elif op == "const":
            try:
                value = Decimal(str(node["value"]))
            except (KeyError, InvalidOperation, TypeError) as exc:
                raise ValidationError(f"model {identifier} has an invalid constant") from exc
            if not value.is_finite():
                raise ValidationError(f"model {identifier} has a non-finite constant")
            return _signature(node.get("unit", "one"), units)
        elif op in ("add", "sum", "multiply", "subtract"):
            args = node.get("args")
            if not isinstance(args, list) or len(args) < (2 if op in ("multiply", "subtract") else 1):
                raise ValidationError(f"model {identifier} operation {op} requires arguments")
            signatures = [walk(child) for child in args]
            if op in ("add", "sum", "subtract"):
                if any(signature != signatures[0] for signature in signatures[1:]):
                    raise ValidationError(f"model {identifier} adds incompatible dimensions")
                return signatures[0]
            result = {}
            for signature in signatures:
                result = _combine(result, signature)
            return result
        elif op in ("divide", "date_diff_days"):
            left, right = (walk(node.get(key)) for key in (("left", "right") if op == "divide" else ("start", "end")))
            if op == "date_diff_days":
                if left != {"date": 1} or right != {"date": 1}:
                    raise ValidationError(f"model {identifier} date_diff_days requires date inputs")
                return {"duration": 1}
            return _combine(left, right, -1)
    actual_signature = walk(model.get("formula"))
    if actual_signature != expected_signature:
        raise ValidationError(f"model {identifier} output unit has incompatible dimensions")
    if referenced != names:
        raise ValidationError(f"model {identifier} has unused inputs: {', '.join(sorted(names - referenced))}")
    if model.get("precision") is not None and (not isinstance(model["precision"], int) or not 0 <= model["precision"] <= 12):
        raise ValidationError(f"model {identifier} precision must be 0..12")
    if model.get("rounding", "half_up") != "half_up":
        raise ValidationError(f"model {identifier} has unsupported rounding")
    for definition in inputs:
        predicate = predicates.get(definition.get("predicate"))
        if not predicate:
            continue
        value = predicate.get("value", {})
        unit = definition["unit"]
        if value.get("dimension") and (unit not in units or units[unit]["dimension"] != value["dimension"]):
            raise ValidationError(f"model {identifier} input {definition['id']} has incompatible dimension")
        if value.get("units") and unit not in value["units"]:
            raise ValidationError(f"model {identifier} input {definition['id']} is outside predicate units")
        if value.get("type") == "currency" and unit != "currency":
            raise ValidationError(f"model {identifier} input {definition['id']} requires currency unit")
    output_predicate = predicates.get(model.get("output_predicate"))
    if output_predicate:
        value = output_predicate.get("value", {})
        unit = model["output_unit"]
        if value.get("dimension") and (unit not in units or units[unit]["dimension"] != value["dimension"]):
            raise ValidationError(f"model {identifier} output has incompatible predicate dimension")
        if value.get("units") and unit not in value["units"]:
            raise ValidationError(f"model {identifier} output is outside predicate units")
        if value.get("type") == "currency" and unit != "currency":
            raise ValidationError(f"model {identifier} output requires currency unit")
