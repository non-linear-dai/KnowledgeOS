"""Deterministic model execution with decimal and explicit unit handling."""
from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP, localcontext

from .core import NotFoundError, Registry, ValidationError, digest, json_text, utcnow
from .model_contract import unit_scale
from .storage import Audit, Database


CONVERSIONS = {("minute_per_unit", "hour_per_unit"): (1, 60),
               ("hour_per_unit", "minute_per_unit"): (60, 1),
               ("hour", "calendar_days"): (1, 24),
               ("calendar_days", "hour"): (24, 1)}


def decimal(value):
    if isinstance(value, bool) or not isinstance(value, (int, float, str, Decimal)):
        raise ValidationError("decimal value must be a number or decimal string")
    try:
        result = Decimal(str(value))
    except InvalidOperation as exc:
        raise ValidationError("invalid decimal") from exc
    if not result.is_finite():
        raise ValidationError("finite decimal required")
    return result


def decimal_text(value: Decimal) -> str:
    """Match the canonical decimal wire form used by existing model results."""
    rendered = format(value, "f")
    if "." in rendered:
        rendered = rendered.rstrip("0").rstrip(".")
    return rendered if "." in rendered else rendered + ".0"


def model_number(raw, expected_unit, units=None, conversions=None):
    if not isinstance(raw, dict):
        return decimal(raw)
    unit = raw.get("unit")
    if not unit:
        raise ValidationError("typed model input requires unit")
    currency = raw.get("currency")
    prefix = unit.split("_", 1)[0]
    if currency and len(prefix) == 3 and prefix.isupper() and currency != prefix:
        raise ValidationError("currency disagrees with unit")
    factor = (1, 1) if unit == expected_unit else CONVERSIONS.get((unit, expected_unit))
    if expected_unit == "currency" and len(unit) == 3 and unit.isupper():
        factor = (1, 1)
    if expected_unit.startswith("currency_") and len(prefix) == 3 and prefix.isupper() and "currency_" + unit.split("_", 1)[1] == expected_unit:
        factor = (1, 1)
    if factor:
        result = decimal(raw["literal"]) * Decimal(factor[0]) / Decimal(factor[1])
        if unit != expected_unit and conversions is not None:
            conversions.append({"from": unit, "to": expected_unit, "factor": f"{factor[0]}/{factor[1]}"})
        return result
    if units and unit in units and expected_unit in units:
        from .units import convert
        result, conversion = convert(decimal(raw["literal"]), unit, expected_unit, units)
        if conversions is not None:
            conversions.append(conversion)
        return result
    raise ValidationError(f"incompatible units {unit} and {expected_unit}")


class ModelEngine:
    def __init__(self, registry: Registry, db: Database, audit: Audit):
        self.registry, self.db, self.audit = registry, db, audit

    def calculate(self, model_id, inputs, scenario="default", *, input_refs=None, output_currency=None, governed=False):
        if model_id not in self.registry.models:
            raise NotFoundError(f"model not found: {model_id}")
        model = {k: v for k, v in self.registry.models[model_id].items() if not k.startswith("_")}
        if model.get("status", {}).get("lifecycle", "active") in ("draft", "retired"):
            raise ValidationError(f"model {model_id} is not active")
        if model.get("governed_only") and not governed:
            raise ValidationError(f"model {model_id} requires governed source inputs")
        if not isinstance(inputs, dict):
            raise ValidationError("model inputs must be an object")
        currencies = set()
        for raw in inputs.values():
            if isinstance(raw, dict):
                prefix = raw.get("unit", "").split("_", 1)[0]
                currency = raw.get("currency") or (prefix if len(prefix) == 3 and prefix.isupper() else None)
                if currency:
                    currencies.add(currency)
        if len(currencies) > 1:
            raise ValidationError("mixed currencies require an explicit exchange-rate model")
        if any(currency not in self.registry.currencies for currency in currencies):
            raise ValidationError("unregistered model input currency")
        if output_currency and output_currency not in self.registry.currencies:
            raise ValidationError("unregistered output currency")
        normalized = {}
        conversions = []
        for definition in model["inputs"]:
            identifier = definition["id"]
            if identifier not in inputs:
                raise ValidationError(f"missing model input: {identifier}")
            raw = inputs[identifier]
            if definition.get("type") == "date":
                try:
                    normalized[identifier] = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
                except ValueError as exc:
                    raise ValidationError(f"invalid date model input: {identifier}") from exc
            else:
                normalized[identifier] = model_number(raw, definition.get("unit", ""), self.registry.units, conversions)
        if currencies and any(d.get("unit", "").startswith("currency_") and not isinstance(inputs[d["id"]], dict) for d in model["inputs"]):
            raise ValidationError("all monetary inputs must declare currency when using typed currency values")
        normalized_hash = {k: str(v) for k, v in sorted(normalized.items())}
        input_refs = input_refs or {}
        currency_definition = {k: v for k, v in self.registry.currencies.get(output_currency, {}).items() if not k.startswith("_")}
        input_hash = digest([normalized_hash, sorted(currencies), model, input_refs, output_currency, currency_definition])
        existing = self.db.first("SELECT * FROM derived_result WHERE model_id=? AND model_version=? AND input_hash=? AND scenario=?",
                                 (model_id, str(model["version"]), input_hash, scenario))
        if existing:
            return self._decode(existing)
        trace = [{"op": "input_sources", "refs": input_refs, "conversions": conversions,
                  "output_currency_definition": currency_definition or None}]
        precision = int(currency_definition["minor_units"]) if output_currency else int(model.get("precision", 2))
        with localcontext() as context:
            context.prec = 60
            base_output = self._evaluate(model["formula"], normalized, trace, model)
            output = base_output / unit_scale(model["output_unit"], self.registry.units)
            rounded = output.quantize(Decimal(1).scaleb(-precision), rounding=ROUND_HALF_UP)
        trace.append({"op": "output_unit", "from_basis": decimal_text(base_output),
                      "unit": model["output_unit"], "value": decimal_text(output)})
        result = {"value": decimal_text(rounded), "unit": model.get("output_unit"), "precision": precision}
        if output_currency:
            if not governed or not model.get("governed_only"):
                raise ValidationError("output currency override requires a governed conversion model")
            result["currency"] = output_currency
        elif currencies:
            result["currency"] = next(iter(currencies))
        if model.get("bands"):
            for band in model["bands"]:
                if "min" in band and rounded < decimal(band["min"]):
                    continue
                if "max" in band and rounded > decimal(band["max"]):
                    continue
                result["classification"] = band["id"]
                break
            if "classification" not in result:
                raise ValidationError(f"model {model_id} bands do not cover output")
        identifier = hashlib.sha256(f"{model_id}:{model['version']}:{input_hash}:{scenario}".encode()).hexdigest()
        run_id, now = str(uuid.uuid4()), utcnow()
        with self.db.transaction():
            self.db.write("INSERT INTO derived_result(id,model_id,model_version,input_hash,scenario,output_json,trace_json,run_id,calculated_at,status,input_refs_json,model_snapshot_json) VALUES(?,?,?,?,?,?,?,?,?,'current',?,?)",
                          (identifier, model_id, str(model["version"]), input_hash, scenario, json_text(result), json_text(trace), run_id, now,
                           json_text(input_refs), json_text(model)))
            self.audit.stage(event_type="model_run", actor="rule", target_id=identifier, after_hash=digest(result),
                             reason="deterministic model execution", payload={"model_id": model_id, "version": str(model["version"]), "input_hash": input_hash,
                                                                           "scenario": scenario, "run_id": run_id, "result": result, "trace": trace,
                                                                           "input_refs": input_refs})
        self.audit.flush()
        return self._decode(self.db.first("SELECT * FROM derived_result WHERE id=?", (identifier,)))

    def preview(self, model, inputs):
        if model.get("governed_only"):
            raise ValidationError("governed currency conversion requires a source-backed exchange quote")
        if not isinstance(inputs, dict):
            raise ValidationError("model inputs must be an object")
        currencies = set()
        for raw in inputs.values():
            if isinstance(raw, dict):
                prefix = str(raw.get("unit", "")).split("_", 1)[0]
                currency = raw.get("currency") or (prefix if len(prefix) == 3 and prefix.isupper() else None)
                if currency:
                    currencies.add(currency)
        if len(currencies) > 1 or any(currency not in self.registry.currencies for currency in currencies):
            raise ValidationError("preview has mixed or unregistered currencies")
        normalized, conversions = {}, []
        for definition in model["inputs"]:
            identifier = definition["id"]
            if identifier not in inputs:
                raise ValidationError(f"missing model input: {identifier}")
            if definition.get("type") == "date":
                try:
                    normalized[identifier] = datetime.fromisoformat(str(inputs[identifier]).replace("Z", "+00:00"))
                except ValueError as exc:
                    raise ValidationError(f"invalid date model input: {identifier}") from exc
            else:
                normalized[identifier] = model_number(inputs[identifier], definition["unit"], self.registry.units, conversions)
        trace = [{"op": "preview_inputs", "conversions": conversions}]
        with localcontext() as context:
            context.prec = 60
            base_output = self._evaluate(model["formula"], normalized, trace, model)
            output = base_output / unit_scale(model["output_unit"], self.registry.units)
            rounded = output.quantize(Decimal(1).scaleb(-int(model.get("precision", 2))), rounding=ROUND_HALF_UP)
        trace.append({"op": "output_unit", "from_basis": decimal_text(base_output),
                      "unit": model["output_unit"], "value": decimal_text(output)})
        result = {"value": decimal_text(rounded), "unit": model["output_unit"]}
        if currencies:
            result["currency"] = next(iter(currencies))
        return {"output": result, "trace": trace,
                "persisted": False}

    def _evaluate(self, node, inputs, trace, model):
        op = node["op"]
        if op == "input":
            value = inputs[node["id"]]
            if isinstance(value, Decimal):
                unit = next(item["unit"] for item in model["inputs"] if item["id"] == node["id"])
                value *= unit_scale(unit, self.registry.units)
        elif op == "const":
            value = decimal(node["value"]) * unit_scale(node.get("unit", "one"), self.registry.units)
        elif op in ("add", "sum"):
            value = sum((self._evaluate(child, inputs, trace, model) for child in node["args"]), Decimal(0))
        elif op == "multiply":
            value = Decimal(1)
            for child in node["args"]:
                value *= self._evaluate(child, inputs, trace, model)
        elif op == "subtract":
            values = [self._evaluate(child, inputs, trace, model) for child in node["args"]]
            value = values[0] - sum(values[1:], Decimal(0))
        elif op == "divide":
            left, right = (self._evaluate(node[key], inputs, trace, model) for key in ("left", "right"))
            if not right:
                raise ValidationError("division by zero")
            value = left / right
        elif op == "date_diff_days":
            start, end = (self._evaluate(node[key], inputs, trace, model) for key in ("start", "end"))
            delta = end - start
            value = Decimal(delta.days * 86400 + delta.seconds) + Decimal(delta.microseconds) / Decimal(1000000)
        else:
            raise ValidationError(f"unsupported deterministic operation: {op}")
        trace.append({"op": op, "value": decimal_text(value) if isinstance(value, Decimal) else value.isoformat()})
        return value

    @staticmethod
    def _decode(row):
        return {**{k: row[k] for k in ("id", "model_id", "model_version", "input_hash", "scenario", "run_id", "calculated_at", "status")},
                "output": json.loads(row["output_json"]), "trace": json.loads(row["trace_json"]),
                "input_refs": json.loads(row.get("input_refs_json") or "{}"),
                "model_snapshot": json.loads(row.get("model_snapshot_json") or "null")}
