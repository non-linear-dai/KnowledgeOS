"""Canonical predicate value validation and normalization."""
from __future__ import annotations

import math
from datetime import date, datetime
from decimal import Decimal

from .core import ValidationError
from .models import decimal


def validate_value(predicate, raw, units=None, currencies=None):
    identifier = predicate["id"]
    definition = predicate.get("value", {})
    typ = definition.get("type")
    if predicate.get("storage", {}).get("mode") == "attr" and definition.get("cardinality") == "many":
        if not isinstance(raw, list):
            raise ValidationError(f"{identifier} requires an array")
        for item in raw:
            validate_value({**predicate, "value": {**definition, "cardinality": "one"}}, item, units, currencies)
        return raw
    if isinstance(raw, dict) and raw.get("type") and raw["type"] != typ:
        raise ValidationError(f"{identifier}: value type does not match predicate")
    value = raw.get("literal", raw.get("ref", raw)) if isinstance(raw, dict) else raw
    if typ in ("string", "text", "enum", "node_ref"):
        valid = isinstance(value, str)
    elif typ == "number":
        valid = isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)
    elif typ == "quantity":
        try:
            decimal(value)
            valid = True
        except ValidationError:
            valid = False
    elif typ in ("decimal", "currency"):
        try:
            decimal(value)
            valid = True
        except ValidationError:
            valid = False
    elif typ == "boolean":
        valid = isinstance(value, bool)
    elif typ == "date":
        try:
            date.fromisoformat(value)
            valid = isinstance(value, str) and len(value) == 10
        except (ValueError, TypeError):
            valid = False
    elif typ == "datetime":
        try:
            datetime.fromisoformat(value.replace("Z", "+00:00"))
            valid = True
        except (ValueError, TypeError, AttributeError):
            valid = False
    else:
        valid = typ == "json"
    if not valid:
        raise ValidationError(f"{identifier} expects {typ}")
    if typ in ("quantity", "currency"):
        unit = raw.get("unit") if isinstance(raw, dict) else None
        unit = unit or definition.get("default_unit")
        if not unit:
            raise ValidationError(f"{identifier} requires a unit")
        allowed_units = definition.get("units") or []
        if allowed_units and unit not in allowed_units:
            raise ValidationError(f"{identifier}: unsupported unit {unit}")
        dimension = definition.get("dimension")
        if dimension and (units is None or unit not in units or units[unit]["dimension"] != dimension):
            raise ValidationError(f"{identifier}: unit {unit} is not in dimension {dimension}")
        if typ == "currency" and (not isinstance(unit, str) or len(unit) != 3 or not unit.isalpha() or not unit.isupper()):
            raise ValidationError(f"{identifier}: currency unit must be a three-letter uppercase code")
        if typ == "currency" and currencies is not None and unit not in currencies:
            raise ValidationError(f"{identifier}: unregistered currency {unit}")
        if typ == "currency" and isinstance(raw, dict) and raw.get("currency") and raw["currency"] != unit:
            raise ValidationError(f"{identifier}: currency disagrees with unit")
    if typ in ("quantity", "currency", "decimal", "number"):
        number = decimal(value)
        if definition.get("minimum") is not None and number < decimal(definition["minimum"]):
            raise ValidationError(f"{identifier}: value is below minimum")
        if definition.get("maximum") is not None and number > decimal(definition["maximum"]):
            raise ValidationError(f"{identifier}: value is above maximum")
    allowed = definition.get("allowed") or []
    if allowed and value not in allowed:
        raise ValidationError(f"{identifier} must be one of {', '.join(map(str, allowed))}")
    if typ in ("quantity", "currency", "decimal"):
        result = dict(raw) if isinstance(raw, dict) else {"literal": raw}
        result["type"] = typ
        if typ != "decimal":
            result.setdefault("unit", definition.get("default_unit"))
        if typ in ("decimal", "currency"):
            result["literal"] = format(decimal(result["literal"]), "f")
        return result
    return raw
