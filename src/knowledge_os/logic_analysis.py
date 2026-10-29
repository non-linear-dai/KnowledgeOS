"""Deterministic static analysis for business logic drafts.

Used by the authoring assistance layer, but independent of any model: every
finding here is computed from the definition itself, so a draft written by a
person and a draft written by an Agent are checked the same way.

Decision tables
    Each input cell is constant on the elementary regions induced by the
    boundary values that appear in that column (numbers), by the literal values
    mentioned (strings, plus "any other value") or by true/false (booleans).
    Evaluating one representative per region combination is therefore an exact
    coverage check, not sampling: it finds input combinations that no rule and
    no default covers, and rules that a FIRST hit policy can never reach.
    Boundary cases put the engine's own output in front of the expert for
    confirmation; they become regression cases only after a person accepts them.

Business rules and constraints
    Literal comparisons on the same input are intersected to find conditions
    that can never be satisfied.
"""
from __future__ import annotations

import copy
from decimal import Decimal
from itertools import product

from .core import ValidationError
from .decisions import evaluate_table, match, scalar, validate_table
from .models import decimal

MAX_POINTS = 4096
MAX_GAPS = 20
MAX_BOUNDARY_CASES = 30
OTHER = "（其他取值）"


def _number_text(value: Decimal) -> str:
    rendered = format(value.normalize(), "f") if value == value.to_integral() else format(value, "f")
    if "." in rendered:
        rendered = rendered.rstrip("0").rstrip(".")
    return rendered or "0"


def _numeric_boundaries(cells):
    values = set()
    for cell in cells:
        if cell is None:
            continue
        raw = cell["value"]
        for item in (raw if cell["op"] in ("in", "between") else [raw]):
            values.add(decimal(item))
    return sorted(values)


def _representatives(column, cells):
    """[(raw input value, human readable region)] covering every equivalence class."""
    typ = column["type"]
    if typ == "boolean":
        return [(True, "是"), (False, "否")]
    if typ == "string":
        values = []
        for cell in cells:
            if cell is None:
                continue
            for item in (cell["value"] if cell["op"] == "in" else [cell["value"]]):
                if item not in values:
                    values.append(item)
        other = OTHER
        while other in values:
            other += "_"
        return [(value, f"= {value}") for value in values] + [(other, "其他取值")]
    bounds = _numeric_boundaries(cells)
    if not bounds:
        return [("0", "任意值")]
    reps = [(_number_text(bounds[0] - max(Decimal(1), abs(bounds[0]) / 10)), f"< {_number_text(bounds[0])}")]
    for index, bound in enumerate(bounds):
        reps.append((_number_text(bound), f"= {_number_text(bound)}"))
        if index + 1 < len(bounds):
            upper = bounds[index + 1]
            reps.append((_number_text((bound + upper) / 2), f"{_number_text(bound)} 与 {_number_text(upper)} 之间（不含端点）"))
    reps.append((_number_text(bounds[-1] + max(Decimal(1), abs(bounds[-1]) / 10)), f"> {_number_text(bounds[-1])}"))
    return reps


def _hits(table, values):
    hits = []
    for rule in table["rules"]:
        if all(match(rule["when"][column["id"]], scalar(values[column["id"]], column["type"]), column["type"]) for column in table["inputs"]):
            hits.append(rule["id"])
    return hits


def _case_input(table, values):
    facts, item = {}, {}
    for column in table["inputs"]:
        (facts if column["source"] == "facts" else item)[column["field"]] = values[column["id"]]
    return {"facts": facts, "item": item} if item else {"facts": facts}


def _merge_regions(points, reps, columns):
    """Collapse gap regions where a column takes every value: shown as 任意."""
    regions = [tuple(point) for point in points]
    for index, column in enumerate(columns):
        labels = {label for _, label in reps[index]}
        grouped = {}
        for region in regions:
            grouped.setdefault(region[:index] + region[index + 1:], set()).add(region[index])
        merged = []
        for rest, values in grouped.items():
            if values >= labels and len(labels) > 1:
                merged.append(rest[:index] + ("任意",) + rest[index:])
            else:
                merged.extend(rest[:index] + (value,) + rest[index:] for value in sorted(values))
        regions = merged
    return [{column["id"]: region[index] for index, column in enumerate(columns)} for region in regions]


def analyze_decision_table(table, units):
    """Coverage, reachability and boundary cases for a validated decision table."""
    validate_table(table, units)
    columns = table["inputs"]
    reps = [_representatives(column, [rule["when"][column["id"]] for rule in table["rules"]]) for column in columns]
    total = 1
    for values in reps:
        total *= len(values)
    exhaustive = total <= MAX_POINTS
    if exhaustive:
        combinations = list(product(*[range(len(values)) for values in reps]))
    else:
        # Too many regions for an exact check: analyse every rule's own regions
        # with the remaining columns at their first representative.
        combinations = set()
        for rule in table["rules"]:
            base = []
            for index, column in enumerate(columns):
                cell = rule["when"][column["id"]]
                inside = [position for position, (value, _) in enumerate(reps[index]) if match(cell, scalar(value, column["type"]), column["type"])]
                base.append(inside or [0])
            for index in range(len(columns)):
                for position in base[index]:
                    combinations.add(tuple(position if i == index else base[i][0] for i in range(len(columns))))
        combinations = sorted(combinations)
    gaps, reached = [], set()
    first = table["hit_policy"] == "FIRST"
    for combination in combinations:
        values = {column["id"]: reps[index][combination[index]][0] for index, column in enumerate(columns)}
        hits = _hits(table, values)
        if not hits and table.get("default_output") is None:
            gaps.append(tuple(reps[index][combination[index]][1] for index in range(len(columns))))
        if hits:
            reached.update(hits[:1] if first else hits)
    shadowed = [rule["id"] for rule in table["rules"] if first and rule["id"] not in reached] if exhaustive else []
    gap_regions = _merge_regions(gaps, reps, columns) if gaps else []
    return {"exhaustive": exhaustive, "regions_checked": len(combinations), "regions_total": total,
            "gaps": gap_regions[:MAX_GAPS], "gap_count": len(gap_regions),
            "has_default": table.get("default_output") is not None,
            "unreachable_rules": shadowed,
            "boundary_cases": boundary_cases(table, units, reps)}


def boundary_cases(table, units, reps=None):
    """Representative and boundary inputs with the engine's actual output, for expert confirmation."""
    columns = table["inputs"]
    reps = reps or [_representatives(column, [rule["when"][column["id"]] for rule in table["rules"]]) for column in columns]
    seen, cases = set(), []

    def add(values, label, rule_id):
        key = tuple(str(values[column["id"]]) for column in columns)
        if key in seen or len(cases) >= MAX_BOUNDARY_CASES:
            return
        seen.add(key)
        case_input = _case_input(table, values)
        entry = {"id": f"boundary_{len(cases) + 1}", "label": label, "rule": rule_id, "input": case_input}
        try:
            result = evaluate_table(table, case_input["facts"], case_input.get("item", {}), units, validated=True)
            entry["actual"] = {"result": result["result"]}
            entry["matched_rules"] = result["matched_rules"]
            entry["used_default"] = result["used_default"]
        except ValidationError as exc:
            entry["error"] = str(exc)
        cases.append(entry)

    for rule in table["rules"]:
        inside = {}
        for index, column in enumerate(columns):
            cell = rule["when"][column["id"]]
            candidates = [value for value, _ in reps[index] if match(cell, scalar(value, column["type"]), column["type"])]
            inside[column["id"]] = candidates[0] if candidates else reps[index][0][0]
        add(dict(inside), f"规则 {rule['id']} 的典型情况", rule["id"])
        for index, column in enumerate(columns):
            cell = rule["when"][column["id"]]
            if column["type"] != "number" or cell is None or cell["op"] not in ("gt", "gte", "lt", "lte", "between"):
                continue
            edges = cell["value"] if cell["op"] == "between" else [cell["value"]]
            for edge in edges:
                point = _number_text(decimal(edge))
                name = column.get("label") or column["id"]
                add({**inside, column["id"]: point}, f"{name} 恰好等于边界值 {point}", rule["id"])
                neighbours = [value for value, _ in reps[index]]
                position = neighbours.index(point) if point in neighbours else -1
                for step in (-1, 1):
                    if 0 <= position + step < len(neighbours):
                        add({**inside, column["id"]: neighbours[position + step]},
                            f"{name} 取 {neighbours[position + step]}（{'低于' if step < 0 else '高于'}边界值 {point}）", rule["id"])
    return cases


def _interval(operator, value):
    return {"eq": (value, True, value, True), "gt": (value, False, None, False), "gte": (value, True, None, False),
            "lt": (None, False, value, False), "lte": (None, False, value, True)}.get(operator)


def _intersect(intervals):
    low, low_inclusive, high, high_inclusive = None, False, None, False
    for a, ai, b, bi in intervals:
        if a is not None and (low is None or a > low or a == low and not ai):
            low, low_inclusive = a, ai
        if b is not None and (high is None or b < high or b == high and not bi):
            high, high_inclusive = b, bi
    if low is None or high is None:
        return True
    return low < high or low == high and low_inclusive and high_inclusive


def _literal_number(value, units):
    factor = Decimal(1)
    if isinstance(value, dict):
        if "unit" in value:
            unit = (units or {}).get(value["unit"])
            if not unit:
                return None
            factor = decimal(unit.get("factor_to_base", "1"))
        value = value.get("value", value.get("literal"))
    try:
        return decimal(value) * factor
    except ValidationError:
        return None


def analyze_business(definition, units=None):
    """Unsatisfiable literal conditions in a business rule or constraint (quantities compared in base units)."""
    by_input = {}
    for index, check in enumerate(definition.get("checks", [])):
        right = check.get("right") or {}
        if "value" in right:
            by_input.setdefault(check["left"], []).append((index, check["operator"], right["value"]))
    contradictions = []
    for input_id, checks in by_input.items():
        numeric = [(index, operator, _literal_number(value, units)) for index, operator, value in checks if operator != "ne"]
        intervals = [_interval(operator, value) for _, operator, value in numeric if value is not None]
        if len(intervals) == len(numeric) and len(intervals) > 1 and not _intersect(intervals):
            contradictions.append({"input": input_id, "checks": [index for index, _, _ in numeric],
                                   "message": f"输入 {input_id} 的数值条件互相矛盾，任何取值都无法同时满足"})
            continue
        equals = {str(value) for _, operator, value in checks if operator == "eq" and not isinstance(value, dict)}
        excluded = {str(value) for _, operator, value in checks if operator == "ne" and not isinstance(value, dict)}
        if len(equals) > 1 or equals & excluded:
            contradictions.append({"input": input_id, "checks": [index for index, _, _ in checks],
                                   "message": f"输入 {input_id} 同时要求等于不同的值（或等于又不等于同一值）"})
    return {"contradictions": contradictions}


def confirm_cases(cases, accepted_ids):
    """Turn confirmed boundary cases into regression cases with the observed output as expectation."""
    accepted = set(accepted_ids or [])
    result = []
    for case in cases:
        if case["id"] in accepted and "actual" in case:
            result.append({"id": case["id"], "label": case["label"], "input": copy.deepcopy(case["input"]),
                           "expected": copy.deepcopy(case["actual"])})
    return result


__all__ = ["analyze_decision_table", "analyze_business", "boundary_cases", "confirm_cases"]
