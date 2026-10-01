import copy
import json

import pytest
import yaml

from knowledge_os.core import ValidationError
from knowledge_os.connector import Connector
from knowledge_os.governance import ChangePlan


def test_business_preview_uses_registered_units_and_three_valued_results(service):
    rule = copy.deepcopy(service.registry.rules["equipment_power_match"])
    result = service.preview_business(rule, "business_rule", {
        "subject": {"rated_power": {"value": 10, "unit": "kW"}},
        "candidates": [{"id": "large", "rated_power": {"literal": 12000, "unit": "W"}},
                       {"id": "small", "rated_power": {"value": 8, "unit": "kW"}},
                       {"id": "unknown"}],
    })["data"]
    assert [item["verdict"] for item in result["results"]] == ["eligible", "condition_failed", "unknown"]
    assert result["eligible_candidate_ids"] == ["large"]
    assert result["results"][0]["checks"][0]["left_value"] == "12000"
    constraint = service.registry.constraints["equipment_power_positive"]
    assert service.preview_business(constraint, "business_constraint", {"subject": {"rated_power": {"value": -1, "unit": "W"}}})["data"]["results"][0]["verdict"] == "invalid"
    with pytest.raises(ValidationError, match="incompatible business unit"):
        service.preview_business(rule, "business_rule", {"subject": {"rated_power": {"value": 1, "unit": "hour"}}, "candidates": [{"id": "test", "rated_power": {"value": 2, "unit": "W"}}]})


def test_rule_distinguishes_subject_scope_from_candidate_failure(service):
    rule = {
        "format": "knowledgeos.business-rule.v1", "id": "country_match",
        "label": "Country match", "description": "Example rule scoped to China organizations.",
        "version": "1.0.0", "status": {"lifecycle": "draft"},
        "scope": {"subject_concept": "organization", "candidate_concept": "organization"},
        "inputs": [{"id": "subject_country", "role": "subject", "predicate": "country"},
                   {"id": "candidate_country", "role": "candidate", "predicate": "country"}],
        "checks": [{"left": "subject_country", "operator": "eq", "right": {"value": "China"}},
                   {"left": "candidate_country", "operator": "eq", "right": {"input": "subject_country"}}],
    }
    candidates = [{"id": "same", "country": "China"},
                  {"id": "different", "country": "Singapore"},
                  {"id": "missing"}]

    applicable = service.preview_business(rule, "business_rule", {
        "subject": {"country": "China"}, "candidates": candidates,
    })["data"]
    assert [item["verdict"] for item in applicable["results"]] == [
        "eligible", "condition_failed", "unknown",
    ]
    assert applicable["eligible_candidate_ids"] == ["same"]
    assert [check["stage"] for check in applicable["results"][0]["checks"]] == [
        "subject_match", "candidate_condition",
    ]

    outside_scope = service.preview_business(rule, "business_rule", {
        "subject": {"country": "Singapore"}, "candidates": candidates,
    })["data"]
    assert all(item["verdict"] == "not_matched" for item in outside_scope["results"])
    assert outside_scope["eligible_candidate_ids"] == []

    missing_subject = service.preview_business(rule, "business_rule", {
        "subject": {}, "candidates": candidates,
    })["data"]
    assert all(item["verdict"] == "unknown" for item in missing_subject["results"])


def test_business_changeset_requires_version_review_and_publishes(service, workspace):
    before = next(item for item in service.studio()["data"]["definitions"] if item["id"] == "business_rule:equipment_power_match")
    after = copy.deepcopy(before)
    after["config"]["version"] = "1.0.1"
    after["lifecycle"] = "active"
    path = before["source_path"]
    operation = {"id": "activate-rule", "type": "update", "targetId": before["id"], "targetKind": "business_rule", "before": before, "after": after}
    plan = ChangePlan(service)
    unchanged = copy.deepcopy(operation)
    unchanged["after"]["config"]["version"] = "1.0.0"
    with pytest.raises(ValidationError, match="higher semantic version"):
        plan.staged_documents(path, {"op": "studio_batch"}, [unchanged])
    proposed = service.propose(actor="business:author", target_source=path, patch={"op": "studio_batch"},
                               operations=[operation], reason="Approve tested equipment matching logic", risk="low")["data"]
    assert proposed["status"] == "review_required"
    with pytest.raises(ValidationError, match="independent reviewer"):
        service.review_changeset(id=proposed["id"], reviewer="business:author", decision="approved")
    service.review_changeset(id=proposed["id"], reviewer="ontology:reviewer", decision="approved")
    applied = service.apply_changeset(id=proposed["id"], publisher="ontology:publisher")["data"]
    assert applied["source_revision"].startswith("sha256:")
    service.publish_changeset(id=proposed["id"], publisher="ontology:publisher", source_revision=applied["source_revision"])
    published = yaml.safe_load((workspace / path).read_text())
    assert published["version"] == "1.0.1" and published["status"]["lifecycle"] == "active"
    assert service.studio()["data"]["coverage"]["business_rule"] == 1
    with pytest.raises(ValidationError, match="deprecated, not deleted"):
        plan.staged_documents(path, {"op": "studio_batch"}, [{**operation, "type": "delete", "after": None}])


def test_active_business_constraint_blocks_invalid_canonical_fact(service, workspace):
    path = workspace / "control/constraints/business/org_name_guard.yaml"
    path.write_text(yaml.safe_dump({
        "format": "knowledgeos.business-constraint.v1", "id": "org_name_guard",
        "label": "Organization name guard", "description": "Checks a confirmed organization name.",
        "version": "1.0.0", "status": {"lifecycle": "active"},
        "scope": {"subject_concept": "organization"},
        "inputs": [{"id": "name", "role": "subject", "predicate": "legal_name"}],
        "checks": [{"left": "name", "operator": "eq", "right": {"value": "impossible-name"}}],
    }))
    with pytest.raises(ValidationError, match="business constraint org_name_guard is invalid"):
        service.compile()


def test_published_business_rule_derives_candidate_subset_with_source_refs(service, workspace):
    path = workspace / "control/rules/business/organization_name_match.yaml"
    path.write_text(yaml.safe_dump({
        "format": "knowledgeos.business-rule.v1", "id": "organization_name_match",
        "label": "Name match", "description": "Select organizations with the same registered name.",
        "version": "1.0.0", "status": {"lifecycle": "active"},
        "scope": {"subject_concept": "organization", "candidate_concept": "organization"},
        "inputs": [{"id": "subject_name", "role": "subject", "predicate": "legal_name"},
                   {"id": "candidate_name", "role": "candidate", "predicate": "legal_name"}],
        "checks": [{"left": "candidate_name", "operator": "eq", "right": {"input": "subject_name"}}],
    }))
    service.refresh_registry()
    result = service.evaluate_business_definition("business_rule", "organization_name_match", "org:acme")["data"]
    assert "org:acme" in result["eligible_candidate_ids"]
    assert result["source_refs"]["subject"]["legal_name"]["source_path"] == "knowledge/entities/org-acme.md"
    assert result["results"][0]["verdict"] in ("eligible", "not_matched", "condition_failed", "unknown")
    impact = service.impact_business(service.registry.rules["organization_name_match"], "business_rule", "org:acme")["data"]
    assert "org:acme" in impact["eligible_candidate_ids"]
    assert impact["verdict_counts"]["eligible"] >= 1
    assert set(impact["verdict_counts"]) == {"eligible", "not_matched", "condition_failed", "unknown"}


def test_multiple_business_files_apply_together(service, workspace):
    baseline = service.studio()["data"]["definitions"]
    operations = []
    paths = []
    for kind, suffix, directory in (("business_constraint", "second_power_guard", "constraints"),
                                    ("business_rule", "second_power_match", "rules")):
        template = copy.deepcopy(next(item for item in baseline if item["kind"] == kind))
        template.update({"id": f"{kind}:{suffix}", "label": suffix, "description": "A separately reviewed test definition.",
                         "source_path": f"control/{directory}/business/{suffix}.yaml", "files": [], "lifecycle": "draft"})
        paths.append(template["source_path"])
        operations.append({"type": "create", "targetKind": kind, "targetId": template["id"], "before": None, "after": template})
    target_source = ", ".join(paths)
    proposed = service.propose(actor="business:author", target_source=target_source, patch={"op": "studio_batch"},
                               operations=operations, reason="Add reviewed business examples")["data"]
    service.review_changeset(id=proposed["id"], reviewer="ontology:reviewer", decision="approved")
    applied = service.apply_changeset(id=proposed["id"], publisher="ontology:publisher")["data"]
    assert all((workspace / path).exists() for path in paths)
    assert service.publish_changeset(id=proposed["id"], publisher="ontology:publisher", source_revision=applied["source_revision"])["data"]["status"] == "published"


def test_active_business_constraint_rejects_invalid_connector_record_atomically(service, workspace):
    path = workspace / "control/constraints/business/supplier_country_guard.yaml"
    path.write_text(yaml.safe_dump({
        "format": "knowledgeos.business-constraint.v1", "id": "supplier_country_guard",
        "label": "Supplier country guard", "description": "Example connector fact acceptance condition.",
        "version": "1.0.0", "status": {"lifecycle": "active"},
        "scope": {"subject_concept": "organization"},
        "inputs": [{"id": "country", "role": "subject", "predicate": "country"}],
        "checks": [{"left": "country", "operator": "eq", "right": {"value": "China"}}],
    }))
    service.refresh_registry()
    mapping = workspace / "connectors/examples/erp-suppliers.mapping.yaml"
    records = workspace / "connectors/examples/erp-suppliers.ndjson"
    with pytest.raises(ValidationError, match="business constraint supplier_country_guard is invalid"):
        Connector(service).ingest_ndjson(records, mapping)
    assert service.db.first("SELECT id FROM source_ref WHERE id='erp:supplier_master:SUP-001'") is None
    assert service.db.first("SELECT source_ref_id FROM connector_record WHERE source_ref_id='erp:supplier_master:SUP-001'") is None


def test_new_active_constraint_checks_existing_connector_entities(service, workspace):
    mapping = workspace / "connectors/examples/erp-suppliers.mapping.yaml"
    record = json.loads((workspace / "connectors/examples/erp-suppliers.ndjson").read_text().splitlines()[0])
    record.update({"supplier_id": "SUP-900", "canonical_id": "vendor900"})
    records = workspace / "connectors/examples/vendor900.ndjson"
    records.write_text(json.dumps(record) + "\n")
    assert Connector(service).ingest_ndjson(records, mapping)["ingested"] == 1
    assert service.db.first("SELECT source_class FROM node WHERE id='org:vendor900'")["source_class"] != "git_authored"
    constraint = {
        "format": "knowledgeos.business-constraint.v1", "id": "existing_country_guard",
        "label": "Existing country guard", "description": "Checks previously ingested supplier records.",
        "version": "1.0.0", "status": {"lifecycle": "active"},
        "scope": {"subject_concept": "organization"},
        "inputs": [{"id": "country", "role": "subject", "predicate": "country"}],
        "checks": [{"left": "country", "operator": "eq", "right": {"value": "China"}}],
    }
    (workspace / "control/constraints/business/existing_country_guard.yaml").write_text(yaml.safe_dump(constraint))
    with pytest.raises(ValidationError, match="connector node org:vendor900: business constraint existing_country_guard is invalid"):
        service.compile()
