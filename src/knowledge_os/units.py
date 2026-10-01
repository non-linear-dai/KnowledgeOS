"""Exact, versioned physical-unit definitions from the control plane."""
from __future__ import annotations

from decimal import Decimal, InvalidOperation
import re

from .core import ValidationError


def _positive_decimal(value, label):
    try:
        number = Decimal(str(value))
    except (InvalidOperation, TypeError) as exc:
        raise ValidationError(f"{label} must be a finite positive decimal") from exc
    if not number.is_finite() or number <= 0:
        raise ValidationError(f"{label} must be a finite positive decimal")
    return number


def validate_units(units):
    bases = {}
    for identifier, definition in units.items():
        if not re.fullmatch(r"\d+\.\d+\.\d+", str(definition.get("version", ""))):
            raise ValidationError(f"unit {identifier} requires a semantic version")
        dimension = definition.get("dimension")
        if not isinstance(dimension, str) or not dimension:
            raise ValidationError(f"unit {identifier} requires a dimension")
        factor = _positive_decimal(definition.get("factor_to_base"), f"unit {identifier} factor_to_base")
        if definition.get("base"):
            if factor != 1 or dimension in bases:
                raise ValidationError(f"unit {identifier} has invalid or duplicate base for {dimension}")
            bases[dimension] = identifier
    for identifier, definition in units.items():
        if definition["dimension"] not in bases:
            raise ValidationError(f"unit {identifier} has no base for {definition['dimension']}")


def validate_currencies(currencies):
    for identifier, definition in currencies.items():
        if not re.fullmatch(r"[A-Z]{3}", identifier):
            raise ValidationError(f"invalid currency code {identifier}")
        if not re.fullmatch(r"\d+\.\d+\.\d+", str(definition.get("version", ""))):
            raise ValidationError(f"currency {identifier} requires a semantic version")
        minor_units = definition.get("minor_units")
        if isinstance(minor_units, bool) or not isinstance(minor_units, int) or not 0 <= minor_units <= 6:
            raise ValidationError(f"currency {identifier} has invalid minor_units")


def convert(value, source, target, units):
    if source not in units or target not in units:
        raise ValidationError(f"unknown physical unit conversion {source} to {target}")
    if source == target:
        return Decimal(str(value)), {"from": source, "to": target, "factor": "1"}
    left, right = units[source], units[target]
    if left["dimension"] != right["dimension"]:
        raise ValidationError(f"incompatible dimensions {source} and {target}")
    factor = Decimal(str(left["factor_to_base"])) / Decimal(str(right["factor_to_base"]))
    return Decimal(str(value)) * factor, {"from": source, "to": target, "factor": format(factor, "f"),
                                          "dimension": left["dimension"]}
