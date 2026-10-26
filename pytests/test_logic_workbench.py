import copy
import http.client
import json
import threading

import pytest

from knowledge_os.api import AccessControl, KnowledgeServer
from knowledge_os.core import ConflictError, NotFoundError, ValidationError
from knowledge_os.logic_workbench import LogicWorkbench, asset_ref, validate_decision_model


def table():
    return {"id": "size_selection", "label": "按尺寸选择设备等级", "version": "1.0.0", "hit_policy": "UNIQUE",
            "inputs": [{"id": "size", "field": "size", "source": "facts", "type": "number"}],
            "outputs": [{"id": "equipment_class", "type": "string"}],
            "rules": [{"id": "small", "when": {"size": {"op": "lte", "value": "300"}}, "then": {"equipment_class": "small"}},
                      {"id": "large", "when": {"size": {"op": "gt", "value": "300"}}, "then": {"equipment_class": "large"}}],
            "test_cases": [{"id": "small", "input": {"facts": {"size": "100"}}, "expected": {"result": {"equipment_class": "small"}}},
                           {"id": "boundary", "input": {"facts": {"size": "300"}}, "expected": {"result": {"equipment_class": "small"}}},
                           {"id": "large", "input": {"facts": {"size": "600"}}, "expected": {"result": {"equipment_class": "large"}}}]}


def publish(service, kind, definition):
    proposal = LogicWorkbench(service).propose(actor="logic:author", kind=kind, definition=definition,
                                              reason="Verified expert regression cases", base_revision=service.registry.fingerprint)["data"]
    assert service.db.first("SELECT risk FROM changeset WHERE id=?", (proposal["id"],))["risk"] == "high"
    service.review_changeset(id=proposal["id"], reviewer="logic:reviewer", decision="approved", note="Independent case review")
    applied = service.apply_changeset(id=proposal["id"], publisher="logic:publisher")["data"]
    # Applied decision assets cannot be consumed before verification/publication.
    with pytest.raises(NotFoundError):
        LogicWorkbench(service).resolve(asset_ref(kind, definition))
    service.publish_changeset(id=proposal["id"], publisher="logic:publisher", source_revision=applied["source_revision"])
    return proposal


def graph(table_definition):
    return {"format": "knowledgeos.decision-model.v1", "id": "equipment_decision", "label": "设备选型决策", "description": "固定版本的设备等级决策",
            "version": "1.0.0", "status": {"lifecycle": "active"},
            "nodes": [{"id": "select", "kind": "decision_table", "ref": asset_ref("decision_table", table_definition),
                       "bindings": {"facts": {"source": "facts", "path": ["facts"]}}}],
            "outputs": {"selection": {"source": "node", "node": "select", "path": ["result", "equipment_class"]}},
            "test_cases": [{"id": "small", "input": {"facts": {"size": "100"}}, "expected": {"output": {"selection": "small"}}}]}


def test_catalog_preview_and_compiler_share_existing_assets_without_writes(service):
    workbench = LogicWorkbench(service)
    catalog = workbench.catalog()
    assert {item["kind"] for item in catalog["assets"]} >= {"business_rule", "business_constraint", "model"}
    assert all(item["source_path"] and item["ref"] for item in catalog["assets"])
    before = service.db.first("SELECT count(*) AS n FROM changeset")["n"]
    result = workbench.preview("decision_table", table(), {"facts": {"size": "600"}})
    assert result["result"]["result"] == {"equipment_class": "large"}
    assert result["tests"]["passed"]
    assert len(result["tests"]["cases"]) == 3
    assert service.db.first("SELECT count(*) AS n FROM changeset")["n"] == before
    assert service.db.first("SELECT count(*) AS n FROM durable.logic_run")["n"] == 0


def test_rule_binds_multiple_instance_roles_and_missing_context_is_unknown(service):
    workbench = LogicWorkbench(service)
    rule = copy.deepcopy(service.registry.rules["equipment_power_match"])
    rule.pop("_path")
    rule["scope"]["context_concepts"] = {"operation": "equipment"}
    rule["inputs"].append({"id": "operation_power", "role": "operation", "predicate": "rated_power"})
    rule["checks"].append({"left": "available_power", "operator": "gte", "right": {"input": "operation_power"}})
    facts = {"subject": {"rated_power": {"literal": "10", "unit": "kW"}},
             "candidates": [{"id": "small", "rated_power": {"literal": "12000", "unit": "W"}},
                            {"id": "large", "rated_power": {"literal": "20", "unit": "kW"}}],
             "contexts": {"operation": {"rated_power": {"literal": "15", "unit": "kW"}}}}
    assert workbench.preview("business_rule", rule, facts)["result"]["eligible_candidate_ids"] == ["large"]
    facts["contexts"] = {}
    result = workbench.preview("business_rule", rule, facts)["result"]
    assert result["status"] == "incomplete" and not result["eligible_candidate_ids"]
    with pytest.raises(ValidationError, match="contexts require"):
        rule["scope"]["context_concepts"] = {"subject": "equipment"}
        workbench.prepare("business_rule", rule)


def test_standalone_table_governance_visibility_immutability_and_template_reuse(service, workspace):
    definition = table()
    proposal = publish(service, "decision_table", definition)
    assert (workspace / "knowledge/decisions/size_selection/v1.0.0.md").is_file()
    assert any(item["id"] == definition["id"] for item in service.template_catalog()["data"]["decisions"])
    kind, resolved, _ = LogicWorkbench(service).resolve(asset_ref("decision_table", definition))
    assert resolved == definition and kind == "decision_table"
    with pytest.raises(ConflictError, match="immutable"):
        LogicWorkbench(service).propose(actor="logic:author", kind=kind, definition=definition, reason="cannot overwrite", base_revision=service.registry.fingerprint)
    row = service.db.first("SELECT status,reviewed_by FROM changeset WHERE id=?", (proposal["id"],))
    assert row == {"status": "published", "reviewed_by": "logic:reviewer"}
    assert service.ledger.verify()["valid"]
    from test_templates import pcb_template
    template = pcb_template()
    template["groups"][0]["decision_binding"] = {"ref": asset_ref(kind, definition), "output": "equipment_class", "purpose": "enabled", "equals": "small"}
    template["operations"][0]["time_model_ref"] = "operation_cycle_time@1.0.0"
    for case in template["cases"]:
        case["facts"]["size"] = "100"
    proposal = service.template_propose(actor="template:author", template=template, reason="Reuse standalone decision")["data"]
    service.review_changeset(id=proposal["id"], reviewer="template:reviewer", decision="approved")
    revision = service.apply_changeset(id=proposal["id"], publisher="template:publisher")["data"]["source_revision"]
    service.publish_changeset(id=proposal["id"], publisher="template:publisher", source_revision=revision)
    assets = {item["ref"]: item for item in LogicWorkbench(service).catalog()["assets"]}
    assert any(use["id"] == "route:rigid_multilayer:1.0.0" for use in assets[asset_ref(kind, definition)]["used_by"])
    uses = assets["model:operation_cycle_time@1.0.0"]["used_by"]
    assert {use["kind"] for use in uses} == {"operation", "template"}


def test_decision_graph_snapshots_and_durable_replay_survive_source_changes(service, workspace):
    publish(service, "decision_table", table())
    workbench = LogicWorkbench(service)
    definition = graph(table())
    prepared = workbench.prepare("decision_model", definition)
    assert prepared["nodes"][0]["snapshot"] == table()
    publish(service, "decision_model", definition)
    ref = asset_ref("decision_model", definition)
    run = workbench.run(ref=ref, actor="logic:operator", facts={"facts": {"size": "100"}}, idempotency_key="run-1")
    assert run["record"]["result"]["output"] == {"selection": "small"}
    assert workbench.run(ref=ref, actor="logic:operator", facts={"facts": {"size": "100"}}, idempotency_key="run-1")["id"] == run["id"]
    with pytest.raises(ConflictError):
        workbench.run(ref=ref, actor="logic:operator", facts={"facts": {"size": "600"}}, idempotency_key="run-1")
    source = workspace / "knowledge/decisions/size_selection/v1.0.0.md"
    source.write_text(source.read_text(encoding="utf-8").replace('equipment_class: small', 'equipment_class: changed'), encoding="utf-8")
    service.registry.units["kW"]["factor_to_base"] = "999"
    assert workbench.replay(run["id"])["matches_recorded"]
    assert workbench.runs(ref)[0]["record"]["definition_hash"]
    assert workbench.runs(subject_id="missing") == []
    assert service.ledger.verify()["valid"]


def test_cycles_unknown_references_and_failed_cases_cannot_publish(service):
    workbench = LogicWorkbench(service)
    bad = table()
    bad["test_cases"][0]["expected"]["result"]["equipment_class"] = "wrong"
    assert not workbench.preview("decision_table", bad)["tests"]["passed"]
    with pytest.raises(ValidationError, match="passing regression"):
        workbench.propose(actor="author", kind="decision_table", definition=bad, reason="bad cases", base_revision=service.registry.fingerprint)
    publish(service, "decision_table", table())
    definition = workbench.prepare("decision_model", graph(table()))
    definition["nodes"][0]["bindings"] = {"facts": {"source": "node", "node": "select", "path": ["result"]}}
    with pytest.raises(ValidationError, match="cycle"):
        validate_decision_model(definition, service.registry)
    definition["nodes"][0]["ref"] = "decision:missing:1.0.0"
    with pytest.raises(NotFoundError):
        workbench.prepare("decision_model", definition)
    path = service.config.control / "decision-models/bad/v1.0.0.yaml"
    path.parent.mkdir(parents=True)
    import yaml
    path.write_text(yaml.safe_dump({**definition, "id": "bad"}))
    before = service.db.first("SELECT count(*) AS n FROM node")["n"]
    with pytest.raises(ValidationError):
        service.compile()
    assert service.db.first("SELECT count(*) AS n FROM node")["n"] == before
    from knowledge_os.governance import ChangePlan
    with pytest.raises(ValidationError, match="immutable logic asset"):
        ChangePlan(service).staged_documents("control/decision-models/unsafe/v1.0.0.yaml", [{"op": "add", "path": "", "value": definition}], [])


def test_rule_graph_stops_on_unknown_instead_of_producing_a_recommendation(service):
    rule = copy.deepcopy(service.registry.rules["equipment_power_match"])
    rule.pop("_path")
    rule["status"] = {"lifecycle": "active"}
    definition = {"format": "knowledgeos.decision-model.v1", "id": "bounded", "label": "Bounded", "version": "1.0.0", "status": {"lifecycle": "active"},
                  "nodes": [{"id": "filter", "kind": "business_rule", "ref": asset_ref("business_rule", rule), "snapshot": rule,
                             "bindings": {"subject": {"source": "facts", "path": ["subject"]}, "candidates": {"source": "facts", "path": ["candidates"]}}}],
                  "outputs": {"eligible": {"source": "node", "node": "filter", "path": ["eligible_candidate_ids"]}}}
    result = LogicWorkbench(service).evaluate("decision_model", definition, {"subject": {}, "candidates": [{"id": "unknown"}]})
    assert result["status"] == "incomplete" and result["output"] == {} and result["stopped_at"] == "filter"
    result = LogicWorkbench(service).evaluate("decision_model", definition, {"subject": {"rated_power": {"literal": "10", "unit": "kW"}}, "candidates": []})
    assert result["status"] == "blocked" and result["output"] == {}


def test_explicit_instance_exceptions_are_scoped_and_never_silently_apply(service):
    workbench = LogicWorkbench(service)
    rule = copy.deepcopy(service.registry.rules["equipment_power_match"])
    rule["scope"].update({"subject_ids": ["part:large"], "candidate_ids": ["equipment:large"]})
    facts = {"subject": {"id": "part:small", "rated_power": {"literal": "10", "unit": "kW"}},
             "candidates": [{"id": "equipment:large", "rated_power": {"literal": "20", "unit": "kW"}}]}
    assert workbench.preview("business_rule", rule, facts)["result"]["results"][0]["verdict"] == "not_matched"
    facts["subject"]["id"] = "part:large"
    assert workbench.preview("business_rule", rule, facts)["result"]["eligible_candidate_ids"] == ["equipment:large"]
    del facts["subject"]["id"]
    assert workbench.preview("business_rule", rule, facts)["result"]["status"] == "incomplete"


def test_existing_evaluation_and_new_workbench_share_context_bindings_and_provenance(service, workspace):
    import yaml
    rule = {"format": "knowledgeos.business-rule.v1", "id": "context_match", "label": "Context match", "description": "Explicit multiple participant comparison",
            "version": "1.0.0", "status": {"lifecycle": "active"},
            "scope": {"subject_concept": "organization", "candidate_concept": "organization", "context_concepts": {"operation": "organization"}},
            "inputs": [{"id": "subject_country", "role": "subject", "predicate": "country"}, {"id": "candidate_country", "role": "candidate", "predicate": "country"}, {"id": "context_country", "role": "operation", "predicate": "country"}],
            "checks": [{"left": "subject_country", "operator": "eq", "right": {"input": "candidate_country"}}, {"left": "subject_country", "operator": "eq", "right": {"input": "context_country"}}]}
    (workspace / "control/rules/business/context_match.yaml").write_text(yaml.safe_dump(rule))
    service.compile()
    contexts = {"operation": "org:acme"}
    direct = service.evaluate_business_definition("business_rule", "context_match", "org:acme", ["org:acme"], context_ids=contexts)["data"]
    impact = service.impact_business(rule, "business_rule", "org:acme", ["org:acme"], context_ids=contexts)["data"]
    preview = LogicWorkbench(service).preview("business_rule", rule, entity_bindings={"subject_id": "org:acme", "candidate_ids": ["org:acme"], "context_ids": contexts})
    assert direct["eligible_candidate_ids"] == impact["eligible_candidate_ids"] == preview["result"]["eligible_candidate_ids"] == ["org:acme"]
    assert direct["source_refs"]["contexts"] == impact["source_refs"]["contexts"] == preview["source_refs"]["contexts"]
    assert direct["source_refs"]["contexts"]["operation"]["country"]["source_path"] == "knowledge/entities/org-acme.md"
    run = LogicWorkbench(service).run(ref="business_rule:context_match@1.0.0", actor="operator", entity_bindings={"subject_id": "org:acme", "candidate_ids": ["org:acme"], "context_ids": contexts}, idempotency_key="bound")
    assert run["record"]["mode"] == "instance_facts"
    assert LogicWorkbench(service).runs(subject_id="org:acme")[0]["id"] == run["id"]
    assert LogicWorkbench(service).replay(run["id"])["matches_recorded"]
    with pytest.raises(ValidationError, match="context roles"):
        service.impact_business(rule, "business_rule", "org:acme", [], context_ids=[])


def test_decision_records_are_in_paired_backup_and_replay_after_index_rebuild(workspace, tmp_path):
    from knowledge_os.core import Config
    from knowledge_os.service import Service
    from knowledge_os.recovery import backup, restore
    config = Config(workspace)
    service = Service(config)
    service.compile(rebuild=True)
    run = LogicWorkbench(service).run(ref="model:cost_rollup@1.0.0", actor="test:operator", facts={"inputs": {
        "material_cost": "10", "labor_hours": "2", "labor_rate": "3", "overhead": "4"}}, idempotency_key="backup-run")
    service.close()
    destination = tmp_path / "paired-backup"
    manifest = backup(config, destination)
    assert manifest["durable_schema_version"] == 5
    for path in config.runtime.glob("knowledge.*.db*"):
        path.unlink()
    restore(config, destination)
    restored = Service(config)
    try:
        restored.compile(rebuild=True)
        workbench = LogicWorkbench(restored)
        assert workbench.runs()[0]["id"] == run["id"]
        assert workbench.replay(run["id"])["matches_recorded"]
        assert restored.ledger.verify()["valid"]
    finally:
        restored.close()


def test_api_preserves_template_permissions_and_readonly_run_history(service):
    auth = AccessControl.from_env({"KNOWLEDGEOS_AUTH_TOKENS": json.dumps({
        "reader": {"principal": "reader", "roles": ["reader"]},
        "agent": {"principal": "author", "roles": ["agent"]},
        "template": {"principal": "expert", "roles": ["template_author"]},
    })})
    server = KnowledgeServer(("127.0.0.1", 0), service, auth)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    def call(method, path, token, body=None):
        connection = http.client.HTTPConnection("127.0.0.1", server.server_port)
        connection.request(method, path, json.dumps(body) if body is not None else None, {"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
        response = connection.getresponse()
        result = json.loads(response.read())
        connection.close()
        return response.status, result
    try:
        assert call("GET", "/v1/logic", "reader")[0] == 200
        assert call("GET", "/v1/logic/runs", "reader")[1]["data"] == []
        payload = {"kind": "decision_table", "definition": table(), "reason": "review", "base_revision": service.registry.fingerprint}
        assert call("POST", "/v1/logic/propose", "agent", payload)[0] == 403
        assert call("POST", "/v1/logic/preview", "reader", payload)[0] == 403
        assert call("POST", "/v1/logic/propose", "template", payload)[0] == 200
        assert call("GET", "/v1/logic/propose", "reader")[0] == 405
        assert call("POST", "/v1/logic/run", "reader", {})[0] == 403
        payload["base_revision"] = "stale"
        assert call("POST", "/v1/logic/propose", "template", payload)[0] == 409
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
