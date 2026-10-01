"""Semantic tests for physical units, bound formulas, and source-backed FX."""
import copy
import json

import pytest

from knowledge_os.connector import Connector
from knowledge_os.core import ConflictError, ValidationError
from knowledge_os.units import convert
from knowledge_os.values import validate_value


def test_physical_unit_contract_and_preview(service):
    units = service.registry.units
    result, trace = convert("7.5", "kW", "W", units)
    assert str(result) == "7500.0"
    assert trace["factor"] == "1000"
    with pytest.raises(ValidationError, match="incompatible dimensions"):
        convert("1", "kW", "kWh", units)
    predicate = service.registry.predicates["rated_power"]
    assert validate_value(predicate, {"type": "quantity", "literal": "7.5", "unit": "kW"}, units)["unit"] == "kW"
    with pytest.raises(ValidationError, match="unsupported unit"):
        validate_value(predicate, {"type": "quantity", "literal": 7.5, "unit": "kWh"}, units)
    preview = service.preview_model({"id": "power_to_watts", "version": "1.0.0", "applies_to": "equipment",
                                     "output_predicate": "rated_power", "inputs": [
                                         {"id": "power", "unit": "W", "source": "subject", "predicate": "rated_power"}],
                                     "formula": {"op": "input", "id": "power"}, "output_unit": "W"},
                                    {"power": {"literal": "7.5", "unit": "kW"}})["data"]
    assert preview["output"]["value"] == "7500.0"
    assert preview["trace"][0]["conversions"][0]["dimension"] == "power"
    assert preview["persisted"] is False


def test_formula_uses_common_unit_basis_for_arithmetic(service):
    additions = service.preview_model({
        "id": "mixed_power", "version": "1.0.0",
        "inputs": [{"id": "a", "unit": "W"}, {"id": "b", "unit": "kW"}],
        "formula": {"op": "add", "args": [{"op": "input", "id": "a"}, {"op": "input", "id": "b"}]},
        "output_unit": "kW", "precision": 3,
    }, {"a": {"literal": "1000", "unit": "W"}, "b": {"literal": "1", "unit": "kW"}})["data"]
    assert additions["output"]["value"] == "2.0"
    energy = service.preview_model({
        "id": "energy_from_power", "version": "1.0.0",
        "inputs": [{"id": "power", "unit": "W"}, {"id": "time", "unit": "hour"}],
        "formula": {"op": "multiply", "args": [{"op": "input", "id": "power"},
                                                {"op": "input", "id": "time"}]},
        "output_unit": "kWh", "precision": 3,
    }, {"power": {"literal": "1000", "unit": "W"},
        "time": {"literal": "1", "unit": "hour"}})["data"]
    assert energy["output"]["value"] == "1.0"
    with pytest.raises(ValidationError, match="incompatible predicate dimension"):
        service.preview_model({
            "id": "wrong_power_output", "version": "1.0.0", "applies_to": "equipment",
            "output_predicate": "rated_power",
            "inputs": [{"id": "power", "unit": "W", "source": "subject", "predicate": "rated_power"},
                       {"id": "time", "unit": "hour", "source": "subject", "predicate": "useful_life_hours"}],
            "formula": {"op": "multiply", "args": [{"op": "input", "id": "power"},
                                                   {"op": "input", "id": "time"}]}, "output_unit": "J",
        }, {"power": {"literal": "1", "unit": "W"}, "time": {"literal": "1", "unit": "hour"}})


def test_bound_empirical_formula_uses_assertion_sources(service, workspace):
    path = workspace / "knowledge/entities/test-equipment.md"
    path.write_text("""---
base:
  schema: { ckm: '3.0' }
  node: { id: 'equipment:test', kind: entity, type: equipment, key: TEST-EQUIPMENT, label: Test equipment }
  lifecycle: { state: active }
  version: { entity_revision: 1 }
knowledge:
  attrs: {}
  assertions:
    - { id: 'assertion:acquisition', predicate: acquisition_cost, value: { type: currency, literal: '120000', unit: CNY }, temporal: { observed_at: '2026-09-01T00:00:00Z' }, epistemic: { assertion_kind: measurement, status: confirmed }, provenance: { source_refs: ['erp:equipment:test'], evidence_refs: ['snapshot:equipment:test'] } }
    - { id: 'assertion:residual', predicate: residual_value, value: { type: currency, literal: '20000', unit: CNY }, temporal: { observed_at: '2026-09-01T00:00:00Z' }, epistemic: { assertion_kind: estimate, status: confirmed }, provenance: { source_refs: ['erp:equipment:test'], evidence_refs: ['snapshot:equipment:test'] } }
    - { id: 'assertion:hours', predicate: useful_life_hours, value: { type: quantity, literal: '10000', unit: hour }, temporal: { observed_at: '2026-09-01T00:00:00Z' }, epistemic: { assertion_kind: estimate, status: confirmed }, provenance: { source_refs: ['erp:equipment:test'], evidence_refs: ['snapshot:equipment:test'] } }
    - { id: 'assertion:utilization', predicate: utilization_factor, value: { type: quantity, literal: '0.8', unit: one }, temporal: { observed_at: '2026-09-01T00:00:00Z', valid_to: '2027-01-01T00:00:00Z' }, epistemic: { assertion_kind: estimate, status: confirmed }, provenance: { source_refs: ['research:utilization:test'], evidence_refs: ['research:utilization:test#1'] } }
  relations: []
  logic_refs: [equipment_hourly_depreciation]
---
""")
    service.compile()
    result = service.calculate_for_entity("equipment_hourly_depreciation", "equipment:test",
                                          as_of="2026-10-01T00:00:00Z")["data"]
    assert result["output"]["value"] == "12.5"
    assert result["output"]["currency"] == "CNY"
    assert result["input_refs"]["utilization_factor"]["assertion_id"] == "assertion:utilization"
    assert result["model_snapshot"]["version"] == "1.0.0"
    assert service.ledger.verify()["valid"]
    with pytest.raises(ValidationError, match="no confirmed utilization_factor"):
        service.calculate_for_entity("equipment_hourly_depreciation", "equipment:test", as_of="2027-02-01T00:00:00Z")


def test_connector_exchange_quote_and_governed_conversion(service, workspace):
    mapping = workspace / "connectors/examples/test-fx.mapping.yaml"
    mapping.write_text("""source_system: fx_provider
source_object: spot_quote
record_id_field: quote_id
version_field: version
timestamp_field: observed_at
authority_class: fx_provider
locator_prefix: 'fx://quote/'
node: { id_prefix: fx, id_field: quote_id, kind: entity, type: exchange_quote, key_field: quote_id, label_field: quote_id }
assertions:
  exchange_rate:
    field: rate
    unit_field: rate_unit
    kind: measurement
    qualifiers:
      base_currency: { field: base_currency }
      quote_currency: { field: quote_currency }
      rate_type: { field: rate_type }
""")
    records = workspace / "connectors/examples/test-fx.ndjson"
    records.write_text(json.dumps({"quote_id": "usd-cny-mid", "version": "1", "observed_at": "2026-09-29T00:00:00Z",
                                   "rate": "7.2", "rate_unit": "CNY_per_USD", "base_currency": "USD",
                                   "quote_currency": "CNY", "rate_type": "mid"}) + "\n")
    assert Connector(service).ingest_ndjson(records, mapping)["ingested"] == 1
    result = service.convert_currency({"literal": "100", "unit": "USD"}, "fx:usd-cny-mid",
                                      as_of="2026-09-29T12:00:00Z")["data"]
    assert result["output"]["value"] == "720.0"
    assert result["output"]["currency"] == "CNY"
    assert result["input_refs"]["rate"]["source_refs"]
    assert service.ledger.verify()["valid"]
    with pytest.raises(ValidationError, match="does not match quote"):
        service.convert_currency({"literal": "100", "unit": "EUR"}, "fx:usd-cny-mid",
                                 as_of="2026-09-29T12:00:00Z")
    with pytest.raises(ValidationError, match="governed source"):
        service.calculate("fx_conversion", {"amount": {"literal": "100", "unit": "USD"}, "rate": "7.2"})
    with pytest.raises(ValidationError, match="stale"):
        service.convert_currency({"literal": "100", "unit": "USD"}, "fx:usd-cny-mid",
                                 as_of="2026-11-15T00:00:00Z")


def test_model_changeset_requires_version_and_valid_formula(service):
    definition = next(item for item in service.studio()["data"]["definitions"] if item["id"] == "model:equipment_hourly_depreciation")
    changed = copy.deepcopy(definition)
    changed["config"]["version"] = "1.0.1"
    changed["description"] = "Reviewed utilization formula."
    operation = {"type": "update", "targetKind": "model", "targetId": definition["id"],
                 "before": definition, "after": changed}
    proposal = service.propose(actor="expert:test", target_source=definition["source_path"],
                               patch={"op": "studio_batch"}, operations=[operation], reason="Versioned formula update")["data"]
    assert proposal["status"] == "review_required"
    with pytest.raises(ValidationError, match="independent reviewer"):
        service.review_changeset(id=proposal["id"], reviewer="expert:test", decision="approved")
    assert service.review_changeset(id=proposal["id"], reviewer="reviewer:test", decision="approved")["data"]["status"] == "approved"
    stale = copy.deepcopy(changed)
    stale["config"]["version"] = "1.0.0"
    with pytest.raises(ValidationError, match="new semantic version"):
        service.propose(actor="expert:test", target_source=definition["source_path"], patch={"op": "studio_batch"},
                        operations=[{**operation, "after": stale}], reason="Unversioned change")
    invalid = copy.deepcopy(changed)
    invalid["config"]["formula"] = {"op": "python", "code": "print(1)"}
    with pytest.raises(ValidationError, match="unsupported formula"):
        service.propose(actor="expert:test", target_source=definition["source_path"], patch={"op": "studio_batch"},
                        operations=[{**operation, "after": invalid}], reason="Invalid formula")
    applied = service.apply_changeset(id=proposal["id"], publisher="publisher:test")["data"]
    assert applied["source_revision"].startswith("sha256:")
    assert service.apply_changeset(id=proposal["id"], publisher="publisher:test")["data"]["source_revision"] == applied["source_revision"]
    published = service.publish_changeset(id=proposal["id"], publisher="publisher:test",
                                          source_revision=applied["source_revision"])["data"]
    assert published["status"] == "published"
    assert service.registry.models["equipment_hourly_depreciation"]["version"] == "1.0.1"
    assert service.ledger.verify()["valid"]


def test_unit_and_currency_changesets_validate_version(service, workspace):
    definitions = service.studio()["data"]["definitions"]
    stale_application = None
    for kind, config_change in (("unit", {"factor_to_base": "1001"}), ("currency", {"minor_units": 3})):
        original = next(item for item in definitions if item["id"] == ("unit:kW" if kind == "unit" else "currency:CNY"))
        after = copy.deepcopy(original)
        after["config"].update(config_change)
        operation = {"type": "update", "targetKind": kind, "targetId": original["id"], "before": original, "after": after}
        with pytest.raises(ValidationError, match="higher semantic version"):
            service.propose(actor="expert:test", target_source=original["source_path"],
                            patch={"op": "studio_batch"}, operations=[operation], reason="Unversioned correction")
        after["config"]["version"] = "1.0.1"
        proposal = service.propose(actor="expert:test", target_source=original["source_path"],
                                   patch={"op": "studio_batch"}, operations=[operation], reason="Reviewed correction")["data"]
        assert proposal["status"] == "review_required"
        if kind == "unit":
            stale_application = (proposal["id"], original["source_path"])
    assert stale_application is not None
    proposal_id, source_path = stale_application
    service.review_changeset(id=proposal_id, reviewer="reviewer:test", decision="approved")
    target = workspace / source_path
    target.write_text(target.read_text() + "\n")
    with pytest.raises(ConflictError, match="source changed"):
        service.apply_changeset(id=proposal_id, publisher="publisher:test")


def test_model_history_keeps_prior_definition_after_recompile(service, workspace):
    original = service.model_history("equipment_hourly_depreciation")["data"]
    assert len(original) == 1 and original[0]["version"] == "1.0.0"
    path = workspace / "control/models/equipment_hourly_depreciation.yaml"
    path.write_text(path.read_text().replace('version: "1.0.0"', 'version: "1.0.1"'))
    service.compile()
    history = service.model_history("equipment_hourly_depreciation")["data"]
    assert {item["version"] for item in history} == {"1.0.0", "1.0.1"}
    assert history[0]["definition"]["formula"] == history[1]["definition"]["formula"]
