import hashlib
import http.client
import json
import threading
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

from knowledge_os.api import AccessControl, KnowledgeServer
from knowledge_os.cli import local_dev_access_control, parser
from knowledge_os.connector import Connector
from knowledge_os.core import AuthenticationError, ConflictError, IntegrityError, ValidationError
from knowledge_os.core import digest
from knowledge_os.core import check_schema
from knowledge_os.governance import ChangePlan
from knowledge_os.recovery import backup, restore


def test_compiler_incremental_and_atomic(service, workspace):
    before = service.db.first("SELECT updated_at FROM entity_card WHERE node_id='org:acme'")["updated_at"]
    result = service.compile()
    assert result["changed"] == []
    assert len(result["skipped"]) == 5
    assert service.db.first("SELECT updated_at FROM entity_card WHERE node_id='org:acme'")["updated_at"] == before
    assert service.get("org:acme")["data"]["label"] == "Acme Industrial Systems"
    assert service.neighbors("org:acme")["data"]["edges"]
    path = workspace / "knowledge/entities/org-acme.md"
    original = path.read_text()
    path.write_text(original.replace("predicate: finding", "predicate: nonexistent_predicate"))
    with pytest.raises(ValidationError, match="unregistered predicate"):
        service.compile()
    assert service.get("org:acme")["data"]["label"] == "Acme Industrial Systems"


def test_ledger_tamper_and_model_policy(service):
    result = service.calculate("cost_rollup", {"material_cost": 10, "labor_hours": 2, "labor_rate": 3, "overhead": 4})["data"]
    assert result["output"]["value"] == "20.0"
    assert service.calculate("cost_rollup", {"material_cost": 10, "labor_hours": 2, "labor_rate": 3, "overhead": 4})["data"]["run_id"] == result["run_id"]
    assert service.ledger.verify()["valid"]
    with pytest.raises(ValidationError, match="mixed currencies"):
        service.calculate("cost_rollup", {"material_cost": {"unit": "USD_per_unit", "literal": 10},
                                          "labor_hours": 2, "labor_rate": {"unit": "EUR_per_hour", "literal": 3}, "overhead": 4})
    service.ledger.connection.execute("DROP TRIGGER event_no_update")
    service.ledger.connection.execute("UPDATE event SET reason='tampered' WHERE sequence=1")
    with pytest.raises(IntegrityError):
        service.ledger.verify()


def test_temperature_and_review_policy(service):
    registry = service.registry
    now = datetime(2026, 9, 28, tzinfo=timezone.utc)
    assert registry.temperature({"predicate": "lead_time_days", "status": "confirmed",
                                 "observed_at": "2026-09-25T00:00:00Z"}, now) == "hot"
    assert registry.temperature({"predicate": "lead_time_days", "status": "confirmed",
                                 "observed_at": "2026-01-01T00:00:00Z"}, now) == "warm"
    assert registry.temperature({"predicate": "lead_time_days", "status": "proposed",
                                 "observed_at": "2026-09-25T00:00:00Z"}, now) == "warm"
    assert len(service.review(limit=100)["data"]) <= 20
    historical = service.context("product:servo-module", domain="cost", as_of="2026-10-01T00:00:00Z")["data"]
    assert all(item["predicate"] != "unit_cost" for item in historical["assertions"])
    earlier = service.context("project:atlas", domain="pm", as_of="2026-09-20T00:00:00Z")
    assert earlier["data"]["entity_card"]["attrs"] == {}
    assert "historical_attributes_unavailable" in earlier["knowledge_gaps"]


def test_schema_validator_rejects_unknown_keywords():
    with pytest.raises(ValidationError, match="unsupported schema keywords"):
        check_schema({"type": "object", "unrecognized": True})
    check_schema({"type": "object", "properties": {"value": {"oneOf": [{"type": "string"}, {"type": "number"}]}},
                  "additionalProperties": False})


def test_dev_server_uses_explicit_loopback_admin_token():
    options = parser().parse_args(["dev-serve"])
    assert options.port == 8787
    with pytest.raises(SystemExit):
        parser().parse_args(["dev-serve", "--bind", "0.0.0.0"])
    auth = local_dev_access_control()
    principal = auth.authenticate("Bearer local-admin-token")
    assert principal == {"id": "local:admin", "roles": ["admin"]}
    auth.authorize(principal, "publish")
    assert auth.authenticate("Bearer local-reviewer-token") == {"id": "local:reviewer", "roles": ["admin"]}
    with pytest.raises(AuthenticationError, match="invalid Bearer token"):
        auth.authenticate("Bearer another-token")


def test_studio_schema_changeset_preserves_canonical_envelope(service):
    schema = next(item for item in service.studio()["data"]["definitions"] if item["id"] == "schema:canonical_node")
    assert schema["read_only"] is False
    before = {**schema, "sourcePath": schema["source_path"]}
    after = {**before, "label": "Canonical schema review", "config": {**before["config"]}}
    operation = {"type": "update", "targetKind": "schema", "targetId": before["id"], "before": before, "after": after}
    plan = ChangePlan(service)
    staged = plan.staged_documents(before["sourcePath"], {"op": "studio_batch"}, [operation])
    assert staged[before["sourcePath"]]["data"]["title"] == "Canonical schema review"
    proposal = service.propose(actor="schema:editor", target_source=before["sourcePath"],
                               patch={"op": "studio_batch"}, operations=[operation],
                               reason="Clarify schema contract")["data"]
    assert proposal["status"] == "review_required"
    assert service.db.first("SELECT risk FROM changeset WHERE id=?", (proposal["id"],))["risk"] == "high"
    after["config"]["required_knowledge"] = ["attrs", "assertions", "relations"]
    with pytest.raises(ValidationError, match="cannot remove canonical envelope fields"):
        plan.staged_documents(before["sourcePath"], {"op": "studio_batch"}, [operation])


def test_agent_grounding_and_tool_policy(service):
    request = service.agent_request(question="What does Acme do?", domain="industry", target="org:acme")["data"]
    assert any(item["ref"] == "node:org:acme" for item in request["evidence"])
    output = {"protocol_version": request["protocol_version"], "request_id": request["request_id"],
              "answer": "Acme is an organization.", "claims": [{"statement": "Acme is an organization.",
              "evidence_refs": ["node:org:acme"], "confidence": 0.9}]}
    assert service.agent_respond(request=request, model_output=output)["data"]["grounding"]["valid"]
    bad = {**output, "claims": [{**output["claims"][0], "evidence_refs": ["invented"]}]}
    with pytest.raises(ValidationError, match="unknown evidence"):
        service.agent_respond(request=request, model_output=bad)
    with pytest.raises(ValidationError, match="not allowed"):
        service.agent_invoke(domain="cost", operation="propose", arguments={})
    with pytest.raises(ValidationError, match="not allowed"):
        service.agent_invoke(domain="pm", operation="calculate", arguments={"model_id": "cost_rollup", "inputs": {}})


def test_connector_replay_survives_rebuild(service, workspace):
    connector = Connector(service)
    mapping = workspace / "connectors/examples/erp-suppliers.mapping.yaml"
    records = workspace / "connectors/examples/erp-suppliers.ndjson"
    assert connector.ingest_ndjson(records, mapping)["ingested"] == 1
    assert connector.ingest_ndjson(records, mapping)["skipped"] == 1
    assert service.get("org:acme")["data"]["attrs"]["country"] == "Singapore"
    service.compile(rebuild=True)
    assert service.get("org:acme")["data"]["attrs"]["country"] == "Singapore"
    assert service.db.first("SELECT count(*) AS n FROM connector_record")["n"] == 1


def test_connector_embedding_policy_and_deletion_replay(service, workspace):
    connector = Connector(service)
    mapping = workspace / "connectors/examples/erp-suppliers.mapping.yaml"
    mapping.write_text(mapping.read_text() + "\ndeleted_field: deleted\n")
    records = workspace / "connectors/examples/erp-suppliers.ndjson"
    original = json.loads(records.read_text())
    connector.ingest_ndjson(records, mapping)
    before = service.db.first("SELECT vector_json FROM node_embedding WHERE node_id='org:acme'")["vector_json"]
    changed = {**original, "lead_time_days": 987654, "version": "18", "updated_at": "2026-09-26T08:00:00Z"}
    records.write_text(json.dumps(changed) + "\n")
    connector.ingest_ndjson(records, mapping)
    assert service.db.first("SELECT vector_json FROM node_embedding WHERE node_id='org:acme'")["vector_json"] == before
    deleted = {**changed, "deleted": True, "version": "19", "updated_at": "2026-09-27T08:00:00Z"}
    records.write_text(json.dumps(deleted) + "\n")
    connector.ingest_ndjson(records, mapping)
    assert service.get("org:acme")["data"]["attrs"]["country"] == "China"
    service.compile(rebuild=True)
    assert service.get("org:acme")["data"]["attrs"]["country"] == "China"
    assert len(service.ledger.events(event_type="connector_delete")) == 1


def test_upstream_changeset_requires_matching_connector_result(service, workspace):
    connector = Connector(service)
    mapping = workspace / "connectors/examples/erp-suppliers.mapping.yaml"
    records = workspace / "connectors/examples/erp-suppliers.ndjson"
    connector.ingest_ndjson(records, mapping)
    future = json.loads(records.read_text().strip())
    future["lead_time_days"] = 35
    future["version"] = "18"
    future["updated_at"] = "2026-09-26T08:00:00Z"
    proposal = service.propose(actor="agent:test", target_source="erp:supplier_master:SUP-001",
                               patch={"expected_source_hash": digest(future)}, reason="ERP update expected")["data"]
    service.review_changeset(id=proposal["id"], reviewer="reviewer:test", decision="approved")
    with pytest.raises(ValidationError, match="source_revision"):
        service.publish_changeset(id=proposal["id"], publisher="publisher:test", source_revision="18")
    next_record = workspace / "connectors/examples/next.ndjson"
    next_record.write_text(json.dumps(future) + "\n")
    connector.ingest_ndjson(next_record, mapping)
    published = service.publish_changeset(id=proposal["id"], publisher="publisher:test", source_revision="18")["data"]
    assert published["verification"]["kind"] == "upstream_source_system"


def test_extraction_evidence_and_no_write(service):
    request = service.extraction_request(source={"kind": "text", "content": "Acme builds servo modules."})["data"]
    entity = {"type": "organization", "label": "Newco", "attrs": {"legal_name": "Newco Ltd."}, "assertions": [], "relations": [],
              "confidence": 0.8, "evidence": [{"segment_id": "segment:0001", "quote": "Acme builds servo modules."}]}
    model = {"protocol_version": request["protocol_version"], "registry_fingerprint": request["registry_fingerprint"],
             "entities": [entity], "unmapped_facts": []}
    result = service.extraction_candidates(request=request, model_output=model)["data"]
    assert result["write_performed"] is False
    assert result["candidates"][0]["action"] == "create_instance"
    assert service.db.first("SELECT id FROM node WHERE id='org:newco'") is None
    model["entities"][0]["evidence"][0]["quote"] = "not in source"
    assert service.extraction_candidates(request=request, model_output=model)["data"]["rejected"]


def test_extraction_existing_relation_candidate(service):
    request = service.extraction_request(source={"kind": "text", "content": "Acme supplies a servo module."})["data"]
    evidence = [{"segment_id": "segment:0001", "quote": "Acme supplies a servo module."}]
    entity = {"type": "organization", "label": "Acme Industrial Systems", "existing_id": "org:acme",
              "attrs": {}, "assertions": [], "relations": [{"predicate": "supplies", "target": "product:servo-module"}],
              "confidence": 0.9, "evidence": evidence}
    result = service.extraction_candidates(request=request, model_output={"protocol_version": request["protocol_version"],
        "registry_fingerprint": request["registry_fingerprint"], "entities": [entity]})["data"]
    assert result["rejected"] == []
    assert result["candidates"][0]["relation_candidates"]
    assert result["candidates"][0]["action"] == "no_change"


def test_changeset_review_and_publication(service, workspace):
    path = workspace / "knowledge/entities/org-acme.md"
    target = "knowledge/entities/org-acme.md"
    proposal = service.propose(actor="agent:test", target_source=target,
                               patch={"op": "replace", "path": "/base/node/label", "value": "Acme Updated"},
                               reason="verified correction", idempotency_key="test-key")["data"]
    repeated = service.propose(actor="agent:test", target_source=target,
                               patch={"op": "replace", "path": "/base/node/label", "value": "Acme Updated"},
                               reason="verified correction", idempotency_key="test-key")["data"]
    assert repeated["id"] == proposal["id"]
    with pytest.raises(ConflictError):
        service.propose(actor="agent:test", target_source=target, patch={"op": "delete"},
                        reason="verified correction", idempotency_key="test-key")
    service.review_changeset(id=proposal["id"], reviewer="reviewer:test", decision="approved")
    with pytest.raises(ValidationError, match="not changed"):
        service.publish_changeset(id=proposal["id"], publisher="publisher:test", source_revision=proposal["base_revision"])
    path.write_text(path.read_text().replace("label: Acme Industrial Systems", "label: Acme Updated"))
    revision = ChangePlan(service).revision(target)
    published = service.publish_changeset(id=proposal["id"], publisher="publisher:test", source_revision=revision)["data"]
    assert published["status"] == "published"
    assert service.get("org:acme")["data"]["label"] == "Acme Updated"


def test_studio_patch_binds_existing_definition(service):
    definition = next(item for item in service.studio()["data"]["definitions"] if item["id"] == "finding")
    after = {**definition, "label": "Finding revised"}
    operation = {"type": "update", "targetKind": "predicate", "targetId": "finding", "before": definition, "after": after}
    proposal = service.propose(actor="agent:studio", target_source=definition["source_path"],
                               patch={"op": "studio_batch", "operation_count": 1}, operations=[operation],
                               reason="ontology correction")
    assert proposal["data"]["status"] == "review_required"
    bad = {**operation, "targetId": "summary"}
    with pytest.raises(ConflictError, match="source changed"):
        service.propose(actor="agent:studio", target_source=definition["source_path"],
                        patch={"op": "studio_batch", "operation_count": 1}, operations=[bad], reason="bad binding")


def test_http_auth_permissions_and_agent_routes(service):
    auth = AccessControl.from_env({"KNOWLEDGEOS_AUTH_TOKENS": json.dumps({"reader-token": {"principal": "user:reader", "roles": ["reader"]},
                                                                                "agent-token": {"principal": "user:agent", "roles": ["agent"]},
                                                                                "expert-token": {"principal": "user:expert", "roles": ["agent", "reviewer"]}})})
    server = KnowledgeServer(("127.0.0.1", 0), service, auth)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    def request(method, path, token=None, body=None):
        conn = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=5)
        headers = {"Authorization": "Bearer " + token} if token else {}
        if body is not None:
            headers["Content-Type"] = "application/json"
        conn.request(method, path, body=json.dumps(body) if body is not None else None, headers=headers)
        response = conn.getresponse()
        result = response.status, json.loads(response.read())
        conn.close()
        return result
    try:
        assert request("GET", "/health")[0] == 200
        assert request("GET", "/v1/get?id=org:acme")[0] == 401
        assert request("GET", "/v1/get?id=org:acme", "reader-token")[0] == 200
        assert request("POST", "/v1/agent/request", "reader-token", {"question": "Acme?", "domain": "industry"})[0] == 403
        for path in ("/v1/session", "/v1/control", "/v1/studio", "/v1/changesets", "/v1/get?id=org:acme",
                     "/v1/models/history?id=cost_rollup",
                     "/v1/resolve?query=Acme", "/v1/search?query=Acme", "/v1/neighbors?id=org:acme",
                     "/v1/history?id=org:acme", "/v1/explain?target=org:acme",
                     "/v1/context?id=org:acme&domain=industry", "/v1/agent/capabilities?domain=industry",
                     "/v1/agent/skill?id=industry-evidence-brief"):
            assert request("GET", path, "reader-token")[0] == 200, path
        studio_definitions = request("GET", "/v1/studio", "reader-token")[1]["data"]["definitions"]
        assert next(item for item in studio_definitions if item["id"] == "schema:canonical_node")["read_only"] is False
        assert request("POST", "/v1/query", "reader-token", {"template": "nodes_by_type", "params": {"type": "organization"}})[0] == 200
        agent_status, agent_packet = request("POST", "/v1/agent/request", "agent-token", {"question": "Acme?", "domain": "industry", "target": "org:acme"})
        assert agent_status == 200
        packet = agent_packet["data"]
        assert request("POST", "/v1/agent/respond", "agent-token", {"request": packet, "model_output": {
            "protocol_version": packet["protocol_version"], "request_id": packet["request_id"], "answer": "Acme exists.",
            "claims": [{"statement": "Acme exists.", "evidence_refs": ["node:org:acme"], "confidence": 0.8}]}})[0] == 200
        assert request("POST", "/v1/agent/invoke", "agent-token", {"domain": "industry", "operation": "get",
                                                                    "arguments": {"id": "org:acme"}})[0] == 200
        assert request("POST", "/v1/calculate", "agent-token", {"model_id": "cost_rollup", "inputs": {
            "material_cost": 10, "labor_hours": 2, "labor_rate": 3, "overhead": 4}})[0] == 200
        assert request("POST", "/v1/models/preview", "reader-token", {"model": {}, "inputs": {}})[0] == 403
        assert request("POST", "/v1/models/preview", "agent-token", {"model": {}, "inputs": {}})[0] == 422
        business_rule = service.registry.rules["equipment_power_match"]
        business_body = {"kind": "business_rule", "definition": business_rule,
                         "facts": {"subject": {"rated_power": {"value": 10, "unit": "kW"}},
                                   "candidates": [{"id": "candidate", "rated_power": {"value": 12, "unit": "kW"}}]}}
        assert request("POST", "/v1/business/preview", "reader-token", business_body)[0] == 403
        status, response = request("POST", "/v1/business/preview", "agent-token", business_body)
        assert status == 200 and response["data"]["eligible_candidate_ids"] == ["candidate"]
        business_body["facts"]["candidates"] = [{"id": "undersized", "rated_power": {"value": 8, "unit": "kW"}}]
        status, response = request("POST", "/v1/business/preview", "agent-token", business_body)
        assert status == 200 and response["data"]["results"][0]["verdict"] == "condition_failed"
        assert request("POST", "/v1/business/impact", "reader-token", {"definition": business_rule, "kind": "business_rule", "subject_id": "org:acme"})[0] == 403
        assert request("POST", "/v1/business/impact", "agent-token", {"definition": service.registry.constraints["equipment_power_positive"], "kind": "business_constraint"})[0] == 200
        assert request("POST", "/v1/business/evaluate", "reader-token", {"kind": "business_rule", "id": "equipment_power_match", "subject_id": "org:acme"})[0] == 422
        assert request("POST", "/v1/calculate/entity", "reader-token", {"model_id": "equipment_hourly_depreciation", "node_id": "equipment:test"})[0] == 403
        assert request("POST", "/v1/fx/convert", "reader-token", {"amount": {"literal": "1", "unit": "USD"}, "quote_node_id": "fx:test", "as_of": "2026-09-29T00:00:00Z"})[0] == 403
        assert request("POST", "/v1/changesets/apply", "agent-token", {"id": "missing"})[0] == 403
        extraction = request("POST", "/v1/extraction/request", "agent-token", {"source": {"kind": "text", "content": "Acme exists."}})
        assert extraction[0] == 200
        extract_packet = extraction[1]["data"]
        assert request("POST", "/v1/extraction/candidates", "agent-token", {"request": extract_packet, "model_output": {
            "protocol_version": extract_packet["protocol_version"], "registry_fingerprint": extract_packet["registry_fingerprint"],
            "entities": []}})[0] == 200
        proposed = request("POST", "/v1/propose", "agent-token", {"actor": "forged:admin",
            "target_source": "knowledge/entities/org-acme.md", "patch": {"op": "replace", "path": "/base/node/label", "value": "Acme Updated"},
            "reason": "verified correction"})
        assert proposed[0] == 201
        assert service.db.first("SELECT actor FROM changeset WHERE id=?", (proposed[1]["data"]["id"],))["actor"] == "user:agent"
        assert request("GET", "/v1/agent/request", "agent-token")[0] == 405
        assert request("GET", "/v1/studio", "reader-token")[1]["data"]["definitions"]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_auth_configuration_fails_closed():
    with pytest.raises(ValidationError, match="TOKENS is required"):
        AccessControl.from_env({"KNOWLEDGEOS_AUTH_MODE": "required"})
    with pytest.raises(ValidationError, match="unknown authentication roles"):
        AccessControl.from_env({"KNOWLEDGEOS_AUTH_TOKENS": json.dumps({"token": {"principal": "x", "roles": ["superuser"]}})})


def test_paired_backup_requires_stopped_service(workspace, tmp_path):
    from knowledge_os.core import Config
    from knowledge_os.service import Service
    service = Service(Config(workspace))
    service.compile(rebuild=True)
    destination = tmp_path.parent / (tmp_path.name + "-backup")
    with pytest.raises(ConflictError, match="runtime is in use"):
        backup(service.config, destination)
    service.close()
    manifest = backup(service.config, destination)
    assert set(manifest["files"]) == {"knowledge.state.db", "knowledge.ledger.db"}
    with pytest.raises(ConflictError, match="empty runtime"):
        restore(service.config, destination)


def test_legacy_index_migrates_governance_state(workspace):
    from knowledge_os.core import Config
    from knowledge_os.service import Service
    runtime = workspace / "runtime"
    runtime.mkdir()
    with sqlite3.connect(runtime / "knowledge.index.db") as connection:
        connection.executescript("""
          CREATE TABLE node(id TEXT PRIMARY KEY,kind TEXT,type TEXT,natural_key TEXT,label TEXT,aliases_json TEXT,tags_json TEXT,
            attrs_json TEXT,lifecycle TEXT,revision INTEGER,source_class TEXT,source_path TEXT,source_hash TEXT,narrative TEXT,compiled_at TEXT);
          CREATE TABLE changeset(id TEXT PRIMARY KEY,actor TEXT,target_source TEXT,risk TEXT,status TEXT,patch_json TEXT,
            reason TEXT,created_at TEXT,reviewed_by TEXT,reviewed_at TEXT);
          INSERT INTO changeset(id,actor,target_source,risk,status,patch_json,reason,created_at)
            VALUES('legacy-id','agent:old','knowledge/entities/org-acme.md','normal','review_required','{}','legacy','2026-01-01T00:00:00Z');
        """)
    service = Service(Config(workspace))
    try:
        assert service.db.first("SELECT id FROM changeset WHERE id='legacy-id'")["id"] == "legacy-id"
        assert service.db.first("SELECT name FROM main.sqlite_master WHERE name='changeset'") is None
        assert "key_namespace" in {row["name"] for row in service.db.execute("PRAGMA main.table_info(node)")}
    finally:
        service.close()


def test_durable_state_survives_disposable_index_recreation(workspace):
    from knowledge_os.core import Config
    from knowledge_os.service import Service
    config = Config(workspace)
    service = Service(config)
    service.compile(rebuild=True)
    mapping = workspace / "connectors/examples/erp-suppliers.mapping.yaml"
    records = workspace / "connectors/examples/erp-suppliers.ndjson"
    Connector(service).ingest_ndjson(records, mapping)
    proposal = service.propose(actor="agent:test", target_source="knowledge/entities/org-acme.md",
                               patch={"op": "replace", "path": "/base/node/label", "value": "Acme Updated"},
                               reason="persist governance")["data"]
    ledger_count = service.ledger.verify()["events"]
    service.close()
    (config.runtime / "knowledge.index.db").unlink()
    reopened = Service(config)
    try:
        reopened.compile(rebuild=True)
        assert reopened.get("org:acme")["data"]["attrs"]["country"] == "Singapore"
        assert reopened.changesets()["data"][0]["id"] == proposal["id"]
        assert reopened.ledger.verify()["events"] >= ledger_count
    finally:
        reopened.close()


def test_recorded_time_keeps_earlier_snapshot(service, workspace):
    first = service.db.first("SELECT recorded_at FROM entity_snapshot WHERE node_id='project:atlas' ORDER BY recorded_at DESC LIMIT 1")["recorded_at"]
    path = workspace / "knowledge/entities/project-atlas.md"
    path.write_text(path.read_text().replace("label: Project Atlas", "label: Project Atlas Revised"))
    service.compile()
    assert service.get("project:atlas")["data"]["label"] == "Project Atlas Revised"
    old = service.context("project:atlas", domain="pm", as_of=first, recorded_as_of=first)["data"]
    assert old["entity_card"]["label"] == "Project Atlas"
    assert any(edge["dst"] == "task:atlas-m1" for edge in old["relations"]["edges"])


def test_control_studio_and_portable_agent_skill(service, workspace):
    control = service.control_plane()["data"]
    assert control["contract_version"] == "3.6"
    assert len(control["ontology"]["concept_types"]) == 9
    assert len(control["ontology"]["relation_types"]) == 6
    assert len(control["predicates"]) == 16
    assert set(control["models"]) == {"cost_rollup", "schedule_variance", "project_risk_score", "equipment_hourly_depreciation", "fx_conversion"}
    assert len(control["units"]) == 8
    assert len(control["currencies"]) == 4
    assert set(control["domains"]) == {"cost", "industry", "pm"}
    studio = service.studio()["data"]
    assert len(studio["definitions"]) == 61
    assert studio["coverage"]["concept"] == 9
    assert studio["coverage"]["relation"] == 6
    assert studio["coverage"]["predicate"] == 16
    assert studio["coverage"]["unit"] == 8
    assert studio["coverage"]["currency"] == 4
    assert next(item for item in studio["definitions"] if item["id"] == "risk_level")["source_path"] == "control/predicates/risk_level.yaml"
    bundle = service.agent_skill("industry-evidence-brief")
    assert set(bundle["data"]["files"]) == {"SKILL.md", "contract.yaml"}
    assert bundle["quality_status"]["portable"] is True
    extension = workspace / "control/ontology/extension.yaml"
    extension.write_text("concept_types:\n  - id: lab_sample\n    label: Laboratory sample\n    properties: []\n")
    extended = service.studio()["data"]
    assert next(item for item in extended["definitions"] if item["id"] == "lab_sample")["source_path"] == "control/ontology/extension.yaml"


def test_agent_deterministic_tool_evidence_and_pm_models(service):
    request = service.agent_request(question="Calculate the unit cost", domain="cost", target="product:servo-module")["data"]
    assert request["skill"]["id"] == "cost-rollup-analysis"
    tool = service.agent_invoke(domain="cost", operation="calculate", arguments={"model_id": "cost_rollup", "inputs": {
        "material_cost": 100, "labor_hours": 1.5, "labor_rate": 20, "overhead": 8}})
    assert tool["data"]["output"]["value"] == "138.0"
    evidence_ref = tool["quality_status"]["agent_evidence_ref"]
    output = {"protocol_version": request["protocol_version"], "request_id": request["request_id"],
              "answer": "The unit cost is 138.", "claims": [{"statement": "Unit cost is 138.",
              "evidence_refs": [evidence_ref], "confidence": 1.0}]}
    response = service.agent_respond(request=request, model_output=output, tool_results=[tool])["data"]
    assert evidence_ref in response["grounding"]["cited_evidence_refs"]
    with pytest.raises(ValidationError, match="unknown evidence"):
        service.agent_respond(request=request, model_output=output)
    schedule = service.agent_invoke(domain="pm", operation="calculate", arguments={"model_id": "schedule_variance", "inputs": {
        "baseline_finish": "2026-10-01T00:00:00Z", "forecast_finish": "2026-10-06T00:00:00Z"}})["data"]
    risk = service.agent_invoke(domain="pm", operation="calculate", arguments={"model_id": "project_risk_score", "inputs": {
        "schedule_delay_days": 5, "blocked_dependencies": 1, "high_risk_items": 1}})["data"]
    assert schedule["output"]["value"] == "5.0" and schedule["output"]["unit"] == "calendar_days"
    assert risk["output"]["value"] == "6.0" and risk["output"]["classification"] == "high"


def test_extraction_registry_change_rejects_stale_request(service, workspace):
    source = {"kind": "text", "content": "Acme website is https://acme.test."}
    previous = service.extraction_request(source=source)["data"]
    predicate = workspace / "control/predicates/website.yaml"
    predicate.write_text("""id: website
label: Website
value: { type: string, cardinality: one }
storage: { mode: attr }
semantics: { description: Official website. }
policy:
  temporal: none
  evidence: optional
  history: git_only
  embedding: false
  write: reviewed
  freshness: stable
  authority: git_authored
  provenance_tier: C
status: { lifecycle: active, equivalent_to: }
""")
    ontology = workspace / "control/ontology/core.yaml"
    ontology.write_text(ontology.read_text().replace(
        "      - { predicate: country, required: false, cardinality: inherit, group: profile }",
        "      - { predicate: country, required: false, cardinality: inherit, group: profile }\n"
        "      - { predicate: website, required: false, cardinality: inherit, group: profile }"))
    stale_output = {"protocol_version": previous["protocol_version"], "registry_fingerprint": previous["registry_fingerprint"], "entities": []}
    with pytest.raises(ValidationError, match="stale"):
        service.extraction_candidates(request=previous, model_output=stale_output)
    current = service.extraction_request(source=source)["data"]
    organization = next(item for item in current["ontology"]["concepts"] if item["id"] == "organization")
    assert "website" in {item["predicate"] for item in organization["properties"]}
    assert current["registry_fingerprint"] != previous["registry_fingerprint"]


def test_changeset_state_machine_and_outbox_recovery(workspace):
    from knowledge_os.core import Config
    from knowledge_os.service import Service
    from knowledge_os.storage import Audit
    config = Config(workspace)
    service = Service(config)
    service.compile(rebuild=True)
    proposal = service.propose(actor="agent:test", target_source="knowledge/entities/org-acme.md",
        patch={"op": "replace", "path": "/base/node/label", "value": "Rejected"}, reason="state test")["data"]
    service.review_changeset(id=proposal["id"], reviewer="reviewer:test", decision="rejected")
    with pytest.raises(ValidationError, match="cannot be reviewed"):
        service.review_changeset(id=proposal["id"], reviewer="reviewer:test", decision="approved")
    with service.db.transaction():
        Audit(service.db, service.ledger).stage(event_type="recovery_test", actor="test", target_id="target:1", event_id="audit-recovery-test")
    service.close()
    recovered = Service(config)
    try:
        assert recovered.db.first("SELECT status FROM audit_outbox WHERE event_id='audit-recovery-test'")["status"] == "delivered"
        assert len(recovered.ledger.events(event_type="recovery_test")) == 1
        assert recovered.ledger.verify()["valid"]
    finally:
        recovered.close()


def test_python_cli_command_surface(workspace):
    repository = Path(__file__).resolve().parents[1]
    entry = repository / "bin/knowledgeos"
    def run(*arguments):
        completed = subprocess.run([sys.executable, str(entry), "--root", str(workspace), *arguments],
                                   cwd=repository, capture_output=True, text=True, timeout=20)
        assert completed.returncode == 0, completed.stderr
        return json.loads(completed.stdout)
    version = subprocess.run([sys.executable, str(entry), "--version"], cwd=repository,
                             capture_output=True, text=True, timeout=20)
    assert version.returncode == 0 and version.stdout.strip() == "0.3.0"
    assert run("doctor")["healthy"]
    assert run("rebuild")["files"] == 5
    assert run("control")["data"]["contract_version"] == "3.6"
    assert run("get", "org:acme")["data"]["id"] == "org:acme"
    assert run("agent-request", "What does Acme make?", "--domain", "industry", "--target", "org:acme")["data"]["evidence"]
    assert run("calculate", "cost_rollup", "--inputs", '{"material_cost":100,"labor_hours":1.5,"labor_rate":20,"overhead":8}')["data"]["output"]["value"] == "138.0"
    source = workspace / "source.txt"
    source.write_text("Acme builds servo modules.")
    assert run("extract-contract", "--source-type", "text", "--content-file", str(source))["data"]["output_schema"]
    assert run("verify-ledger")["valid"]
