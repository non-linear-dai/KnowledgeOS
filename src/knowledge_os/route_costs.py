"""Read-only route costing: explicit quantities, units, currency and source bindings."""
from __future__ import annotations

import json
from collections import defaultdict
from decimal import Decimal, ROUND_CEILING, ROUND_HALF_UP, localcontext

from .core import ValidationError, utcnow
from .models import decimal, decimal_text
from .templates import TemplateWorkbench
from .units import convert


def calculate_route(service, template, case, scenario):
    with localcontext() as context:
        context.prec = 60
        return _calculate_route(service, template, case, scenario)


def _calculate_route(service, template, case, scenario):
    if not isinstance(scenario, dict) or set(scenario) - {"quantity", "currency", "rates", "margin", "operating_ratio"}:
        raise ValidationError("invalid costing scenario")
    currency = scenario.get("currency")
    if currency not in service.registry.currencies:
        raise ValidationError("costing requires a registered currency")
    quantity = decimal(scenario.get("quantity"))
    if quantity <= 0 or quantity % 1 or quantity > 1000000000:
        raise ValidationError("production quantity must be an integer between 1 and 1 billion")
    margin, operating = decimal(scenario.get("margin", "0")), decimal(scenario.get("operating_ratio", "0"))
    if margin < 0 or operating < 0 or margin + operating >= 1:
        raise ValidationError("revenue-based margin and operating ratio must be nonnegative and sum to less than one")
    rates = scenario.get("rates", {})
    if not isinstance(rates, dict) or len(rates) > 500:
        raise ValidationError("resource rates must be a bounded mapping")
    workbench = TemplateWorkbench(service)
    expanded = workbench.preview(template, case)
    operations = {item["id"]: item for item in service.template_catalog()["data"]["operations"]}
    operations.update({workbench.operation_id(item): item for item in template.get("operations", [])})
    groups = {g["id"]: g for g in template["groups"]}
    missing, rows, totals, model_traces = [], [], defaultdict(Decimal), []
    resolved_rates = {}
    units = service.registry.units
    precision = service.registry.currencies[currency]["minor_units"]

    def money(value):
        return decimal_text(value.quantize(Decimal(1).scaleb(-precision), rounding=ROUND_HALF_UP))

    def rate_for(ref):
        if ref in resolved_rates:
            return resolved_rates[ref]
        raw = rates.get(ref)
        if raw is None:
            return None
        if not isinstance(raw, dict) or set(raw) - {"value", "unit", "currency", "source"}:
            raise ValidationError(f"invalid rate: {ref}")
        provenance = {"kind": "scenario", "source_backed": False}
        if raw.get("source"):
            source = raw["source"]
            if not isinstance(source, dict) or set(source) - {"node_id", "predicate", "as_of"} or not source.get("node_id") or source.get("predicate") not in service.registry.predicates:
                raise ValidationError("rate source must identify an instance and registered predicate")
            assertion = service._effective_assertion(source["node_id"], source["predicate"], source.get("as_of") or utcnow())
            value = json.loads(assertion["value_json"])
            qualifiers = json.loads(assertion["qualifiers_json"])
            if not str(assertion["source_path"]).startswith("connector:"):
                raise ValidationError("source-backed rate must originate from a connector")
            freshness = service.registry.freshness_days(source["predicate"])
            observed = assertion["observed_at"]
            if freshness is not None and (not observed or (service._instant(source.get("as_of") or utcnow()) - service._instant(observed)).total_seconds() > freshness * 86400):
                raise ValidationError("source-backed resource rate is stale")
            declared_unit = value.get("unit", "")
            parts = declared_unit.split("_per_", 1)
            if len(parts) == 2:
                value_currency, rate_unit = parts
            else:
                value_currency, rate_unit = value.get("currency") or declared_unit, qualifiers.get("per_unit")
            raw = {"value": value.get("literal"), "currency": value_currency, "unit": rate_unit}
            provenance = {"kind": "connector", "source_backed": True, **service._assertion_ref(assertion)}
        elif raw.get("value") in (None, ""):
            return None
        if raw.get("currency") != currency or raw.get("unit") not in units or decimal(raw.get("value")) < 0:
            raise ValidationError(f"rate {ref} requires the scenario currency, a registered per-unit and a nonnegative price")
        result = {"value": decimal(raw["value"]), "unit": raw["unit"], "provenance": provenance}
        resolved_rates[ref] = result
        return result

    with localcontext() as context:
        context.prec = 60
        for instance in expanded["instances"]:
            operation = operations.get(instance["operation_ref"])
            if operation is None:
                missing.append({"instance_id": instance["id"], "field": "operation", "message": "标准工序版本不可用"})
                continue
            processing = operation.get("processing") or {}
            item = {}
            group = groups.get(instance.get("group_id"))
            if group and instance.get("item_id"):
                item = next(i for i in case["sets"][group["iteration_set"]] if i["id"] == instance["item_id"])
            duration = None
            if operation.get("time_model_ref"):
                key, version = operation["time_model_ref"].rsplit("@", 1)
                model = service.registry.models.get(key)
                if not model or model["version"] != version or model.get("status", {}).get("lifecycle", "active") != "active":
                    raise ValidationError("processing time model must resolve to its active pinned version")
                inputs = {}
                for definition in model["inputs"]:
                    binding = processing.get("time_inputs", {}).get(definition["id"])
                    source = (case.get("facts", {}) if binding and binding["source"] == "facts" else item)
                    if not binding or binding["field"] not in source:
                        missing.append({"instance_id": instance["id"], "field": definition["id"], "message": "缺少加工时间模型输入"})
                    else:
                        inputs[definition["id"]] = source[binding["field"]]
                if len(inputs) == len(model["inputs"]):
                    result = service.models.preview(model, inputs)
                    duration, _ = convert(decimal(result["output"]["value"]), result["output"]["unit"], "second", units)
                    if duration < 0:
                        raise ValidationError("processing time model returned a negative time")
                    model_traces.append({"instance_id": instance["id"], "model_ref": operation["time_model_ref"], **result})
            elif processing.get("duration"):
                duration, _ = convert(decimal(processing["duration"]["value"]), processing["duration"]["unit"], "second", units)
            setup = Decimal(0)
            if processing.get("setup"):
                setup, _ = convert(decimal(processing["setup"]["value"]), processing["setup"]["unit"], "second", units)
            cycles = (quantity / decimal(processing.get("batch_size", "1"))).to_integral_value(rounding=ROUND_CEILING)
            total_seconds = setup + duration * cycles if duration is not None else None
            subtotal, resource_rows = Decimal(0), []
            resources = operation.get("resources", [])
            if not resources:
                missing.append({"instance_id": instance["id"], "field": "resources", "message": "尚未维护生产资源"})
            for resource in resources:
                ref = resource.get("ref")
                if not ref or "amount" not in resource and not resource.get("quantity_model_ref"):
                    missing.append({"instance_id": instance["id"], "field": ref or resource["kind"], "message": "资源需要数量或数量模型、单位和价格标识"})
                    continue
                basis = resource.get("basis", "cycle")
                if basis == "time" and total_seconds is None:
                    missing.append({"instance_id": instance["id"], "field": "duration", "message": "按时间计费的资源缺少加工时长"})
                    continue
                amount = decimal(resource.get("amount", "1"))
                if resource.get("quantity_model_ref"):
                    key, version = resource["quantity_model_ref"].rsplit("@", 1)
                    model = service.registry.models.get(key)
                    if not model or model["version"] != version or model.get("status", {}).get("lifecycle", "active") != "active":
                        raise ValidationError("resource quantity model requires its active pinned version")
                    process = {"duration_seconds": {"literal": total_seconds, "unit": "second"} if total_seconds is not None else None,
                               "cycle_seconds": {"literal": duration, "unit": "second"} if duration is not None else None,
                               "cycles": {"literal": cycles, "unit": "one"}, "production_quantity": {"literal": quantity, "unit": "one"}}
                    inputs = {}
                    for definition in model["inputs"]:
                        binding = resource.get("quantity_inputs", {}).get(definition["id"])
                        source = {"facts": case.get("facts", {}), "item": item, "process": process}.get(binding.get("source") if binding else None, {})
                        if not binding or source.get(binding["field"]) is None:
                            missing.append({"instance_id": instance["id"], "field": definition["id"], "message": "缺少资源数量模型输入"})
                        else:
                            inputs[definition["id"]] = source[binding["field"]]
                    if len(inputs) != len(model["inputs"]):
                        continue
                    result = service.models.preview(model, inputs)
                    calculated, _ = convert(decimal(result["output"]["value"]), result["output"]["unit"], resource["unit"], units)
                    if calculated < 0:
                        raise ValidationError("resource quantity model returned a negative amount")
                    amount *= calculated
                    model_traces.append({"instance_id": instance["id"], "resource_ref": ref, "model_ref": resource["quantity_model_ref"], **result})
                if basis == "time":
                    elapsed, _ = convert(total_seconds, "second", resource["unit"], units)
                    amount *= elapsed
                else:
                    amount *= {"cycle": cycles, "piece": quantity, "batch": Decimal(1)}[basis]
                rate = rate_for(ref)
                if rate is None:
                    missing.append({"instance_id": instance["id"], "field": ref, "message": "缺少资源单价或有效价格观测"})
                    continue
                charged, conversion = convert(amount, resource["unit"], rate["unit"], units)
                cost = charged * rate["value"]
                subtotal += cost; totals[resource["kind"]] += cost
                resource_rows.append({"ref": ref, "kind": resource["kind"], "basis": basis, "quantity": decimal_text(charged),
                                      "unit": rate["unit"], "rate": decimal_text(rate["value"]), "cost": money(cost),
                                      "conversion": conversion, "provenance": rate["provenance"]})
            rows.append({**instance, "cycles": decimal_text(cycles), "duration_seconds": decimal_text(total_seconds) if total_seconds is not None else None,
                         "resources": resource_rows, "known_cost": money(subtotal)})
        total = sum(totals.values(), Decimal(0))
        complete = not missing and bool(expanded["instances"])
        return {"status": "complete" if complete else "incomplete", "preview_only": True, "currency": currency, "quantity": decimal_text(quantity),
                "route": expanded, "rows": rows, "missing": missing, "model_traces": model_traces,
                "known_cost": money(total), "total_cost": money(total) if complete else None,
                "unit_cost": money(total / quantity) if complete else None,
                "material_cost": money(totals["material"] + totals["part"]) if complete else None,
                "manufacturing_cost": money(sum((v for k, v in totals.items() if k not in ("material", "part")), Decimal(0))) if complete else None,
                "reasonable_unit_price": money(total / quantity / (1 - margin - operating)) if complete else None,
                "price_basis": {"margin": decimal_text(margin), "operating_ratio": decimal_text(operating), "denominator": "revenue"},
                "categories": {key: money(value) for key, value in totals.items()}}
