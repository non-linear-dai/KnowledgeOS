"""Bounded DMN-style tables: typed columns, deterministic matching and hit policies.

This is an explicit subset, not a FEEL interpreter or DMN XML implementation.
"""
from __future__ import annotations

import re
from itertools import combinations

from .core import ValidationError
from .models import decimal, decimal_text
from .units import convert

REF = re.compile(r"^decision:[a-z][a-z0-9_-]{0,63}:[1-9][0-9]*\.[0-9]+\.[0-9]+$")
TYPES = {"string", "number", "boolean"}
OPS = {"eq", "ne", "gt", "gte", "lt", "lte", "in", "between"}


def scalar(value, typ):
    if typ == "number":
        return decimal(value)
    if typ == "boolean" and type(value) is bool or typ == "string" and isinstance(value, str):
        return value
    raise ValidationError(f"decision value requires {typ}")


def match(cell, value, typ):
    if cell is None:
        return True
    op, raw = cell["op"], cell["value"]
    right = [scalar(x, typ) for x in raw] if op in ("in", "between") else scalar(raw, typ)
    return {"eq": lambda: value == right, "ne": lambda: value != right,
            "gt": lambda: value > right, "gte": lambda: value >= right,
            "lt": lambda: value < right, "lte": lambda: value <= right,
            "in": lambda: value in right, "between": lambda: right[0] <= value <= right[1]}[op]()


def _intervals(cell):
    if cell is None:
        return [(None, False, None, False)]
    op, value = cell["op"], cell["value"]
    if op == "in":
        return [(decimal(x), True, decimal(x), True) for x in value]
    if op == "between":
        return [(decimal(value[0]), True, decimal(value[1]), True)]
    x = decimal(value)
    return {"eq": [(x, True, x, True)], "ne": [(None, False, x, False), (x, False, None, False)],
            "gt": [(x, False, None, False)], "gte": [(x, True, None, False)],
            "lt": [(None, False, x, False)], "lte": [(None, False, x, True)]}[op]


def _overlap(left, right, typ):
    if typ != "number":
        if typ == "boolean":
            return any(match(left, value, typ) and match(right, value, typ) for value in (True, False))
        values = {""}
        for cell in (left, right):
            if cell:
                values.update(cell["value"] if cell["op"] == "in" else [cell["value"]])
        extra = "__other__"
        while extra in values:
            extra += "_"
        values.add(extra)
        return any(match(left, value, typ) and match(right, value, typ) for value in values)
    for a, ai, b, bi in _intervals(left):
        for c, ci, d, di in _intervals(right):
            lows = [(x, inclusive) for x, inclusive in ((a, ai), (c, ci)) if x is not None]
            highs = [(x, inclusive) for x, inclusive in ((b, bi), (d, di)) if x is not None]
            if not lows or not highs:
                return True
            low, high = max(x for x, _ in lows), min(x for x, _ in highs)
            if low < high or low == high and all(flag for x, flag in lows + highs if x == low):
                return True
    return False


def validate_table(table, units):
    if not isinstance(table, dict) or not re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", str(table.get("id", ""))):
        raise ValidationError("decision requires a stable id")
    if not re.fullmatch(r"[1-9][0-9]*\.[0-9]+\.[0-9]+", str(table.get("version", ""))) or not str(table.get("label", "")).strip():
        raise ValidationError("decision requires label and semantic version")
    if table.get("hit_policy") not in ("UNIQUE", "FIRST", "COLLECT"):
        raise ValidationError("supported decision hit policies: UNIQUE, FIRST, COLLECT")
    for name in ("inputs", "outputs"):
        columns = table.get(name)
        if not isinstance(columns, list) or not 1 <= len(columns) <= 20:
            raise ValidationError(f"decision {name} requires 1-20 columns")
        names = set()
        for column in columns:
            if not isinstance(column, dict) or not re.fullmatch(r"[a-z][a-z0-9_]*", str(column.get("id", ""))) or column["id"] in names:
                raise ValidationError("decision column ids must be unique stable keys")
            names.add(column["id"])
            if column.get("type") not in TYPES:
                raise ValidationError("decision column has unsupported type")
            if name == "inputs":
                if column.get("source") not in ("facts", "item") or not re.fullmatch(r"[a-z][a-z0-9_]*", str(column.get("field", ""))):
                    raise ValidationError("decision input needs a fact or item field")
                if column.get("unit") and (column["unit"] not in units or column["type"] != "number"):
                    raise ValidationError("decision units require a registered numeric unit")
    rules = table.get("rules")
    if not isinstance(rules, list) or not 1 <= len(rules) <= 200:
        raise ValidationError("decision requires 1-200 rules")
    input_ids, output_ids = ({c["id"] for c in table[name]} for name in ("inputs", "outputs"))
    rule_ids = set()
    for rule in rules:
        if not isinstance(rule, dict) or not re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", str(rule.get("id", ""))) or rule["id"] in rule_ids:
            raise ValidationError("decision rule ids must be unique stable keys")
        rule_ids.add(rule["id"])
        if not isinstance(rule.get("when"), dict) or set(rule["when"]) != input_ids or not isinstance(rule.get("then"), dict) or set(rule["then"]) != output_ids:
            raise ValidationError("decision rules must supply every input and output cell")
        for column in table["inputs"]:
            cell = rule["when"][column["id"]]
            if cell is None:
                continue
            if not isinstance(cell, dict) or set(cell) != {"op", "value"} or cell["op"] not in OPS:
                raise ValidationError("invalid decision input cell")
            if cell["op"] in ("gt", "gte", "lt", "lte", "between") and column["type"] != "number":
                raise ValidationError("ordered decision tests require a numeric column")
            if cell["op"] in ("in", "between"):
                values = cell["value"]
                if not isinstance(values, list) or not 1 <= len(values) <= 100 or cell["op"] == "between" and len(values) != 2:
                    raise ValidationError("invalid decision range or list")
                converted = [scalar(x, column["type"]) for x in values]
                if cell["op"] == "between" and converted[0] > converted[1]:
                    raise ValidationError("decision range is reversed")
            else:
                scalar(cell["value"], column["type"])
        for column in table["outputs"]:
            scalar(rule["then"][column["id"]], column["type"])
    default = table.get("default_output")
    if default is not None:
        if not isinstance(default, dict) or set(default) != output_ids:
            raise ValidationError("decision default must supply every output")
        for column in table["outputs"]:
            scalar(default[column["id"]], column["type"])
    if table["hit_policy"] == "UNIQUE":
        for left, right in combinations(rules, 2):
            if all(_overlap(left["when"][c["id"]], right["when"][c["id"]], c["type"]) for c in table["inputs"]):
                raise ValidationError(f"UNIQUE decision rules overlap: {left['id']}, {right['id']}")
    return table


def evaluate_table(table, facts, item, units, *, validated=False):
    if not validated:
        validate_table(table, units)
    inputs = {}
    for column in table["inputs"]:
        source = facts if column["source"] == "facts" else item
        if column["field"] not in source:
            raise ValidationError(f"missing decision input: {column['source']}.{column['field']}")
        value = source[column["field"]]
        if isinstance(value, dict):
            if not column.get("unit") or not value.get("unit"):
                raise ValidationError("typed decision input requires declared units")
            value, _ = convert(decimal(value.get("literal")), value["unit"], column["unit"], units)
        inputs[column["id"]] = scalar(value, column["type"])
    hits = [rule for rule in table["rules"] if all(match(rule["when"][c["id"]], inputs[c["id"]], c["type"]) for c in table["inputs"])]
    if table["hit_policy"] == "FIRST":
        hits = hits[:1]
    outputs = [hit["then"] for hit in hits]
    if not outputs and table.get("default_output") is not None:
        outputs = [table["default_output"]]
    if not outputs and table["hit_policy"] != "COLLECT":
        raise ValidationError(f"decision {table['id']} has no matching rule or default")
    canonical = [{c["id"]: decimal_text(scalar(output[c["id"]], c["type"])) if c["type"] == "number" else output[c["id"]]
                  for c in table["outputs"]} for output in outputs]
    return {"decision_ref": f"decision:{table['id']}:{table['version']}", "hit_policy": table["hit_policy"],
            "inputs": {k: decimal_text(v) if table["inputs"][list(inputs).index(k)]["type"] == "number" else v for k, v in inputs.items()},
            "matched_rules": [hit["id"] for hit in hits], "used_default": not hits and bool(outputs),
            "result": canonical if table["hit_policy"] == "COLLECT" else canonical[0]}
