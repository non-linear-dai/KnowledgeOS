import json

import yaml

import pytest

from knowledge_os.core import ValidationError
from knowledge_os.connector import Connector
from knowledge_os.decisions import evaluate_table, validate_table
from knowledge_os.templates import TemplateWorkbench
from test_templates import pcb_template


def decision():
    return {"id": "layer_requirement", "version": "1.0.0", "label": "Layer requirement", "hit_policy": "UNIQUE",
            "inputs": [{"id": "layers", "field": "layers", "source": "facts", "type": "number"}],
            "outputs": [{"id": "required", "type": "boolean"}],
            "rules": [{"id": "simple", "when": {"layers": {"op": "lt", "value": "4"}}, "then": {"required": False}},
                      {"id": "multi", "when": {"layers": {"op": "gte", "value": "4"}}, "then": {"required": True}}]}


def test_typed_decisions_hit_policies_overlap_and_missing_inputs(service):
    table = decision()
    assert evaluate_table(table, {"layers": "8"}, {}, service.registry.units)["matched_rules"] == ["multi"]
    table["rules"][0]["when"]["layers"] = {"op": "lte", "value": "4"}
    with pytest.raises(ValidationError, match="overlap"):
        validate_table(table, service.registry.units)
    table["hit_policy"] = "FIRST"
    assert evaluate_table(table, {"layers": 4}, {}, service.registry.units)["result"] == {"required": False}
    table["hit_policy"] = "COLLECT"
    assert len(evaluate_table(table, {"layers": 4}, {}, service.registry.units)["result"]) == 2
    with pytest.raises(ValidationError, match="missing decision input"):
        evaluate_table(table, {}, {}, service.registry.units)
    table = decision(); table["inputs"][0]["unit"] = "hour"
    assert evaluate_table(table, {"layers": {"literal": "240", "unit": "minute"}}, {}, service.registry.units)["result"]["required"]
    with pytest.raises(ValidationError, match="incompatible"):
        evaluate_table(table, {"layers": {"literal": "1", "unit": "kW"}}, {}, service.registry.units)


def test_decision_route_bypass_and_sequential_repeat(service):
    route = pcb_template()
    route["decisions"] = [decision()]
    route["groups"][0]["decision_binding"] = {"ref": "decision:layer_requirement:1.0.0", "output": "required", "purpose": "enabled"}
    route["steps"].insert(0, {"id": "prepare", "label": "Prepare", "parent": None,
                             "operation_ref": "operation:laminate:1.0.0", "cost_basis": "board"})
    route["edges"].insert(0, {"from": "prepare", "to": "inner_circuit"})
    route["cases"][0]["facts"]["layers"] = 2
    result = TemplateWorkbench(service).preview(route, route["cases"][0])
    assert result["operation_count"] == 2
    assert result["edges"] == [{"from": "prepare", "to": "lamination"}]
    assert result["decisions"][0]["matched_rules"] == ["simple"]
    route["cases"][0]["facts"]["layers"] = 4
    route["groups"][0]["execution_mode"] = "sequential"
    result = TemplateWorkbench(service).preview(route, route["cases"][0])
    assert {"from": "inner_circuit/L2/inner_etch", "to": "inner_circuit/L3/inner_etch"} in result["edges"]
    assert {"from": "prepare", "to": "inner_circuit/L2/inner_etch"} in result["edges"]
    assert {"from": "inner_circuit/L3/inner_etch", "to": "lamination"} in result["edges"]
    assert [item["id"] for item in result["instances"]] == [
        "prepare", "inner_circuit/L2/inner_etch", "inner_circuit/L3/inner_etch", "lamination"]


def test_conditional_group_compound_inputs_and_operation_choice(service):
    route = pcb_template(); table = decision()
    table["outputs"] = [{"id": "method", "type": "string"}]
    table["rules"][0]["then"] = {"method": "operation:etch:1.0.0"}
    table["rules"][1]["then"] = {"method": "operation:laminate:1.0.0"}
    route["decisions"] = [table]
    route["groups"][0].update(group_mode="conditional", when={"all": [
        {"source": "facts", "field": "layers", "op": "gte", "value": 4},
        {"source": "facts", "field": "enabled", "op": "eq", "value": True}]})
    route["steps"][0]["decision_binding"] = {"ref": "decision:layer_requirement:1.0.0", "output": "method", "purpose": "operation"}
    route["cases"][0]["facts"] = {"layers": 4, "enabled": True}
    result = TemplateWorkbench(service).preview(route, route["cases"][0])
    assert result["operation_count"] == 2
    assert result["operation_counts"] == {"operation:laminate:1.0.0": 2}
    route["cases"][0]["facts"]["enabled"] = False
    assert TemplateWorkbench(service).preview(route, route["cases"][0])["operation_count"] == 1


def test_decision_package_roundtrip_isolation_compiler_and_ledger(service, workspace):
    route = pcb_template(); route["decisions"] = [decision()]
    for case in route["cases"]:
        case["facts"]["layers"] = 4
    route["groups"][0]["decision_binding"] = {"ref": "decision:layer_requirement:1.0.0", "output": "required", "purpose": "enabled"}
    row = service.template_propose(actor="expert:a", template=route, reason="Decision version")["data"]
    service.review_changeset(id=row["id"], reviewer="expert:b", decision="approved")
    revision = service.apply_changeset(id=row["id"], publisher="expert:c")["data"]["source_revision"]
    assert service.template_catalog()["data"]["decisions"] == []
    service.publish_changeset(id=row["id"], publisher="expert:c", source_revision=revision)
    restored = service.template_get("route:rigid_multilayer:1.0.0")["data"]
    assert restored["decisions"] == route["decisions"]
    assert restored["groups"][0]["decision_binding"] == route["groups"][0]["decision_binding"]
    assert service.template_catalog()["data"]["decisions"] == route["decisions"]
    assert service.ledger.verify()["valid"]
    # Reuse the published decision without reauthoring it in the next package.
    restored["version"] = "1.1.0"; restored["decisions"] = []
    replacement = dict(route["operations"][1], version="1.1.0")
    restored["operations"] = [replacement]
    restored["steps"][1]["operation_ref"] = "operation:laminate:1.1.0"
    reused = service.template_propose(actor="expert:a", template=restored, reason="Reuse governed decision")["data"]
    service.review_changeset(id=reused["id"], reviewer="expert:b", decision="approved")
    revision = service.apply_changeset(id=reused["id"], publisher="expert:c")["data"]["source_revision"]
    service.publish_changeset(id=reused["id"], publisher="expert:c", source_revision=revision)
    assert service.template_preview(restored, restored["cases"][0])["data"]["operation_count"] == 3
    assert len(service.template_catalog()["data"]["templates"]) == 2
    assert {o["id"] for o in service.template_catalog()["data"]["operations"]} == {
        "operation:etch:1.0.0", "operation:laminate:1.0.0", "operation:laminate:1.1.0"}
    path = workspace / "knowledge/decisions/layer_requirement/v1.0.0.md"
    path.write_text(path.read_text().replace('UNIQUE', 'BOGUS'))
    with pytest.raises(ValidationError, match="hit policies"):
        service.compile()


def test_costing_batch_setup_units_rounding_and_incomplete_inputs(service):
    route = pcb_template(); route["groups"] = []; route["edges"] = []; route["steps"] = [route["steps"][1]]
    route["operations"] = [route["operations"][1]]
    op = route["operations"][0]
    op["processing"] = {"duration": {"value": "15", "unit": "minute"}, "setup": {"value": "30", "unit": "minute"}, "batch_size": "10"}
    op["resources"] = [{"kind": "equipment", "ref": "press", "amount": "1", "unit": "hour", "basis": "time"},
                       {"kind": "material", "ref": "resin", "amount": "2", "unit": "one", "basis": "piece"}]
    scenario = {"quantity": "21", "currency": "CNY", "margin": "0.1", "operating_ratio": "0.1",
                "rates": {"press": {"value": "120", "unit": "hour", "currency": "CNY"}, "resin": {"value": "3", "unit": "one", "currency": "CNY"}}}
    before = service.db.first("SELECT count(*) n FROM derived_result")["n"]
    result = service.template_calculate(route, route["cases"][0], scenario)["data"]
    assert result["rows"][0]["cycles"] == "3.0"
    assert result["rows"][0]["duration_seconds"] == "4500.0"
    assert result["total_cost"] == "276.0" and result["unit_cost"] == "13.14"
    assert result["reasonable_unit_price"] == "16.43"
    assert result["material_cost"] == "126.0" and result["manufacturing_cost"] == "150.0"
    assert service.db.first("SELECT count(*) n FROM derived_result")["n"] == before
    scenario.update(margin="0.333333333333333333333333333333", operating_ratio="0.666666666666666666666666666666")
    assert service.template_calculate(route, route["cases"][0], scenario)["data"]["status"] == "complete"
    scenario.update(margin="0.1", operating_ratio="0.1")
    del scenario["rates"]["resin"]
    result = service.template_calculate(route, route["cases"][0], scenario)["data"]
    assert result["status"] == "incomplete" and result["total_cost"] is None
    assert result["known_cost"] == "150.0" and result["missing"][0]["field"] == "resin"
    scenario["rates"]["press"]["currency"] = "USD"
    with pytest.raises(ValidationError, match="scenario currency"):
        service.template_calculate(route, route["cases"][0], scenario)


def test_pinned_time_model_uses_mapped_inputs(service):
    route = pcb_template(); op = route["operations"][1]
    op["time_model_ref"] = "operation_cycle_time@1.0.0"
    op["processing"] = {"batch_size": "10", "time_inputs": {"baseline_time": {"source": "facts", "field": "baseline"}, "workload_factor": {"source": "facts", "field": "load"}}}
    op["resources"] = [{"kind": "labor", "ref": "worker", "amount": "1", "unit": "hour", "basis": "time"}]
    route["operations"][0]["resources"] = [{"kind": "material", "ref": "film", "amount": "1", "unit": "one", "basis": "cycle"}]
    route["cases"][0]["facts"] = {"baseline": {"literal": "15", "unit": "minute"}, "load": "2"}
    result = service.template_calculate(route, route["cases"][0], {"quantity": "11", "currency": "CNY", "rates": {
        "worker": {"value": "60", "unit": "hour", "currency": "CNY"}, "film": {"value": "2", "unit": "one", "currency": "CNY"}}})["data"]
    assert result["status"] == "complete" and result["total_cost"] == "104.0"
    assert result["model_traces"][0]["model_ref"] == "operation_cycle_time@1.0.0"
    assert result["model_traces"][0]["trace"][0]["conversions"]


def test_energy_quantity_model_uses_total_time_without_double_charging(service):
    route = pcb_template(); route["groups"] = []; route["edges"] = []; route["steps"] = [route["steps"][1]]
    route["operations"] = [route["operations"][1]]
    route["operations"][0].update(processing={"duration": {"value": "15", "unit": "minute"}, "setup": {"value": "30", "unit": "minute"}, "batch_size": "10"}, resources=[{
        "kind": "energy", "ref": "power", "unit": "kWh", "basis": "batch", "quantity_model_ref": "operation_energy@1.0.0",
        "quantity_inputs": {"effective_power": {"source": "facts", "field": "power"}, "elapsed_time": {"source": "process", "field": "duration_seconds"}}}])
    route["cases"][0]["facts"]["power"] = {"literal": "2", "unit": "kW"}
    result = service.template_calculate(route, route["cases"][0], {"quantity": "21", "currency": "CNY", "rates": {"power": {"value": "0.8", "unit": "kWh", "currency": "CNY"}}})["data"]
    assert result["total_cost"] == "2.0"
    assert result["rows"][0]["resources"][0]["quantity"] == "2.5"


def test_source_backed_rate_retains_provenance_and_enforces_as_of_freshness(service, workspace):
    # The isolated fixture models a governed price feed with automatic confirmation.
    predicate_path = workspace / "control/predicates/unit_cost.yaml"
    predicate = yaml.safe_load(predicate_path.read_text())
    predicate["policy"]["write"] = "auto"
    predicate_path.write_text(yaml.safe_dump(predicate))
    service.compile()
    mapping = workspace / "connectors/examples/test-price.mapping.yaml"
    mapping.write_text("""source_system: erp_operational
source_object: resource_price
record_id_field: resource_id
version_field: version
timestamp_field: observed_at
authority_class: erp_operational
node: { id_prefix: resource, type: product, kind: entity, id_field: resource_id, key_field: resource_id, label_field: resource_id }
assertions:
  unit_cost:
    field: price
    unit_field: unit
    kind: measurement
    qualifiers: { per_unit: one }
""")
    records = workspace / "connectors/examples/test-price.ndjson"
    records.write_text(json.dumps({"resource_id": "resin", "version": "1", "observed_at": "2026-09-29T00:00:00Z", "price": "3", "unit": "CNY"}) + "\n")
    assert Connector(service).ingest_ndjson(records, mapping)["ingested"] == 1
    route = pcb_template(); route["groups"] = []; route["edges"] = []
    route["steps"] = [route["steps"][1]]; route["operations"] = [route["operations"][1]]
    route["operations"][0]["resources"] = [{"kind": "material", "ref": "resin", "amount": "2", "unit": "one", "basis": "piece"}]
    scenario = {"quantity": "10", "currency": "CNY", "rates": {"resin": {"source": {"node_id": "resource:resin", "predicate": "unit_cost", "as_of": "2026-09-29T12:00:00Z"}}}}
    result = service.template_calculate(route, route["cases"][0], scenario)["data"]
    assert result["total_cost"] == "60.0"
    provenance = result["rows"][0]["resources"][0]["provenance"]
    assert provenance["source_backed"] and provenance["source_refs"] == ["erp_operational:resource_price:resin"]
    assert provenance["evidence_refs"] and provenance["source_hash"]
    assert service.ledger.verify()["valid"]
    scenario["rates"]["resin"]["source"]["as_of"] = "2026-09-28T00:00:00Z"
    with pytest.raises(ValidationError, match="no confirmed"):
        service.template_calculate(route, route["cases"][0], scenario)
    scenario["rates"]["resin"]["source"]["as_of"] = "2026-11-01T00:00:00Z"
    with pytest.raises(ValidationError, match="stale"):
        service.template_calculate(route, route["cases"][0], scenario)


def test_dynamic_operation_reference_is_validated_even_when_case_does_not_select_it(service):
    route = pcb_template(); table = decision()
    table["outputs"] = [{"id": "method", "type": "string"}]
    table["rules"][0]["then"] = {"method": "operation:etch:1.0.0"}
    table["rules"][1]["then"] = {"method": "operation:missing:1.0.0"}
    route["decisions"] = [table]
    route["steps"][0]["decision_binding"] = {"ref": "decision:layer_requirement:1.0.0", "output": "method", "purpose": "operation"}
    route["cases"][0]["facts"]["layers"] = 2
    with pytest.raises(ValidationError, match="unknown or unpublished operation"):
        TemplateWorkbench(service).preview(route, route["cases"][0])


def test_unpublished_decisions_and_operations_cannot_be_reused(service):
    route = pcb_template(); route["decisions"] = [decision()]
    row = service.template_propose(actor="expert:a", template=route, reason="Pending package")["data"]
    service.review_changeset(id=row["id"], reviewer="expert:b", decision="approved")
    service.apply_changeset(id=row["id"], publisher="expert:c")
    service.compile()
    borrowed = pcb_template(); borrowed["id"] = "borrowed"; borrowed["operations"] = []
    with pytest.raises(ValidationError, match="unpublished operation"):
        TemplateWorkbench(service).preview(borrowed, borrowed["cases"][0])
    borrowed["operations"] = route["operations"]
    borrowed["groups"][0]["decision_binding"] = {"ref": "decision:layer_requirement:1.0.0", "output": "required", "purpose": "enabled"}
    with pytest.raises(ValidationError, match="decision is not published"):
        TemplateWorkbench(service).preview(borrowed, borrowed["cases"][0])
