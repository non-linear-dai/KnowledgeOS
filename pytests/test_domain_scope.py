"""Domain browsing is a bounded projection of registered control dependencies."""
import pytest

from knowledge_os.core import Config, Registry, ValidationError
from knowledge_os.domain_scope import domain_definition_ids, domain_scope


def test_cost_scope_includes_dependencies_without_reverse_expansion(workspace):
    registry = Registry(Config(workspace))
    scope = domain_scope(registry, "cost")
    assert set(scope["concept"]) == {"product", "organization", "equipment"}
    assert set(scope["model"]) == {"cost_rollup", "equipment_hourly_depreciation", "fx_conversion"}
    assert {"acquisition_cost", "residual_value", "useful_life_hours", "depreciation_cost_per_hour", "legal_name"} <= set(scope["predicate"])
    assert "equipment_power_positive" in scope["business_constraint"]
    assert "equipment_power_match" in scope["business_rule"]
    ids = domain_definition_ids(scope)
    assert {"model:equipment_hourly_depreciation", "business_rule:equipment_power_match", "supplies"} <= set(ids)
    assert not {"project", "task", "market", "task_status", "model:project_risk_score", "model:operation_energy"} & set(ids)
    assert ids == sorted(set(ids))


def test_studio_scope_is_the_same_projection_used_by_the_directory(service):
    before = service.ledger.verify()
    definitions = service.studio()["data"]["definitions"]
    for definition in definitions:
        if definition["kind"] == "domain":
            identifier = definition["id"].removeprefix("domain:")
            assert definition["config"]["definition_scope"] == domain_definition_ids(domain_scope(service.registry, identifier))
    assert service.ledger.verify() == before


def test_unconfigured_and_new_domains_do_not_fall_back_to_all_content(workspace):
    registry = Registry(Config(workspace))
    with pytest.raises(ValidationError, match="unknown work domain"):
        domain_scope(registry, "unknown")
    registry.domains["focused"] = {"id": "focused", "concept_scopes": ["market"], "retrieval": {}, "required_models": []}
    scope = domain_scope(registry, "focused")
    assert scope["concept"] == ["market"]
    assert not scope["model"] and not scope["relation"]
    assert set(scope["predicate"]) == {"summary", "market_size", "finding"}
    registry.domains["empty"] = {"id": "empty"}
    assert all(not identifiers for identifiers in domain_scope(registry, "empty").values())
