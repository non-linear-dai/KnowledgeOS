"""Extraction protocol v2 server side: contract, source registration, acceptance, proposals and queues."""
import base64
import copy
import hashlib
import http.client
import json
import threading
from pathlib import Path

import jsonschema
import pytest

from knowledge_os.api import AccessControl, KnowledgeServer
from knowledge_os.core import ConflictError, ValidationError, frontmatter
from knowledge_os.extraction_v2 import PROTOCOL

REPO = Path(__file__).resolve().parents[1]
EXAMPLES = REPO / "docs/examples/extraction-v2"


def example(name):
    return json.loads((EXAMPLES / name).read_text(encoding="utf-8"))


def assert_protocol(service, message):
    schema = service.registry.schemas["extraction-v2"]
    errors = ["/".join(map(str, error.absolute_path)) + ": " + error.message
              for error in jsonschema.Draft202012Validator(schema).iter_errors(message)]
    assert not errors, errors[:5]


def contract_for(service, **scope):
    request = {"protocol_version": PROTOCOL, "message_type": "contract_request",
               "scope": scope or {"domains": ["cost", "industry"]},
               "include": ["concepts", "relations", "recipes", "lexicon", "rule_slots"]}
    contract = service.extraction_v2_contract(request)["data"]
    assert_protocol(service, contract)
    return contract


def source_document(document_id, version, blocks, **document):
    raw = "\n".join(block["text"] for block in blocks).encode("utf-8")
    sha = hashlib.sha256(raw).hexdigest()
    return {"protocol_version": PROTOCOL, "message_type": "source_document",
            "document": {"id": document_id, "version": version, "locator": f"dms://test/{document_id}", "kind": "meeting_minutes",
                         "content_hash": sha, "captured_at": "2026-10-26T13:31:00Z", "origin": {"system": "dms"},
                         "document_dates": {"issued_at": "2026-10-15"}, **document},
            "original": {"sha256": sha, "media_type": "text/plain", "bytes": len(raw),
                         "inline_base64": base64.b64encode(raw).decode()},
            "parser": {"name": "test-parser", "version": "1.0.0"},
            "blocks": [{"id": block["id"], "type": block.get("type", "paragraph"), "text": block["text"], "order": index,
                        **{key: value for key, value in block.items() if key not in ("id", "type", "text")}}
                       for index, block in enumerate(blocks)]}


def anchor(message, block_id, quote, role=None):
    text = next(block["text"] for block in message["blocks"] if block["id"] == block_id)
    start = text.index(quote)
    result = {"block_id": block_id, "start": start, "end": start + len(quote), "quote": quote}
    if role:
        result["role"] = role
    return result


def submission(contract, source, instances, mode="precheck", **extra):
    document = source["document"]
    message = {"protocol_version": PROTOCOL, "message_type": "extraction_submission",
               "submission_id": extra.pop("submission_id", "sub-test-1"), "mode": mode,
               "contract": {"contract_id": contract["contract_id"], "registry_fingerprint": contract["registry_fingerprint"]},
               "source": {"document_id": document["id"], "document_version": document["version"], "content_hash": document["content_hash"]},
               "run": {"run_id": "run-test", "agent": {"platform": "pytest", "name": "kos-extractor", "version": "0.1.0"},
                       "skill": {"id": "document-instance-extraction", "version": "1.0.0"}, "stages": [{"id": "fill_fields", "kind": "llm"}]},
               "instances": instances, **extra}
    if mode == "propose":
        message.setdefault("reason", "测试：按评审纪要更新实例")
    return message


def worked_example(service):
    contract = contract_for(service)
    receipt = service.extraction_v2_register(example("03-source-document.json"), actor="agent:extractor")["data"]
    assert_protocol(service, receipt)
    message = example("05-extraction-submission.json")
    message["contract"] = {"contract_id": contract["contract_id"], "registry_fingerprint": contract["registry_fingerprint"]}
    return contract, receipt, message


def test_contract_is_generated_from_the_registry(service):
    contract = contract_for(service, domains=["cost", "industry"], document_type="supplier_review_minutes")
    concepts = {item["id"]: item for item in contract["concepts"]}
    assert set(concepts) == {"organization", "market", "product", "equipment"}
    product = concepts["product"]
    lead_time = next(item for item in product["properties"] if item["predicate"] == "lead_time_days")
    assert lead_time["publication_route"] == "upstream_source_system" and lead_time["temporal_required"]
    assert lead_time["value"]["unit_aliases"]["天"] == "calendar_days"
    assert next(item for item in product["properties"] if item["predicate"] == "summary")["publication_route"] == "governed_changeset"
    assert "物料" in product["terms"]["alt"] and product["identity"]["keys"][0]["source"] == "natural_key"
    assert {item["logic_ref"] for item in contract["rule_slots"]} == {"constraint:equipment_power_positive@1.0.0",
                                                                     "rule:equipment_power_match@1.0.0"}
    assert [item["id"] for item in contract["recipes"]] == ["supplier_review_minutes"]
    assert {item["node_id"] for item in contract["lexicon"]["entries"]} >= {"org:acme", "product:servo-module"}
    assert any(item["id"] == "supplies" for item in contract["relations"])
    assert contract["registry_fingerprint"] == service.registry.fingerprint
    assert service.db.first("SELECT 1 FROM durable.extraction_contract WHERE id=?", (contract["contract_id"],))
    assert contract_for(service, domains=["cost", "industry"], document_type="supplier_review_minutes")["contract_id"] == contract["contract_id"]
    page = service.extraction_v2_contract({"protocol_version": PROTOCOL, "message_type": "contract_request",
                                           "scope": {"concepts": ["organization", "product"]}, "include": ["lexicon"],
                                           "lexicon": {"limit": 1}})["data"]["lexicon"]
    assert len(page["entries"]) == 1 and page["next_cursor"] == "o:1"
    with pytest.raises(ValidationError, match="not open to document extraction"):
        contract_for(service, concepts=["route_template"])
    with pytest.raises(ValidationError, match="PROTOCOL_UNSUPPORTED"):
        service.extraction_v2_contract({"protocol_version": "knowledgeos.extraction.v1", "message_type": "contract_request"})


def test_worked_example_precheck_matches_documented_outcomes_without_writes(service):
    contract, receipt, message = worked_example(service)
    documented_contract = example("02-extraction-contract.json")
    volatile = {"issued_at", "registry_fingerprint", "contract_id"}
    assert {k: v for k, v in contract.items() if k not in volatile} == {k: v for k, v in documented_contract.items() if k not in volatile}
    assert receipt["stored"] and receipt["block_count"] == 13
    before = service.db.first("SELECT count(*) AS count FROM changeset")["count"]
    report = service.extraction_v2_precheck(message)["data"]
    assert_protocol(service, report)
    assert report["status"] == "accepted" and report["changesets"] == []
    org, product = report["instances"]
    assert (org["outcome"], org["node_id"]) == ("matched", "org:acme")
    assert [field["outcome"] for field in org["fields"]] == ["already_present", "accepted"]
    assert org["fields"][1]["temporal"]["observed_at"] == "2026-10-15T00:00:00+08:00"
    assert (product["outcome"], product["node_id"]) == ("matched", "product:servo-module")
    summary, lead_time = product["fields"]
    assert summary["outcome"] == "conflict" and summary["conflict"]["authority_strategy"] == "review_equal_authority"
    assert lead_time["outcome"] == "upstream_required"
    assert lead_time["normalized"] == {"type": "quantity", "literal": "30", "unit": "calendar_days",
                                       "qualifiers": {"approximation": "approximate"}}
    assert report["relations"] == [{"local_id": "tmp:rel-1", "outcome": "already_present",
                                    "source_node_id": "org:acme", "target_node_id": "product:servo-module"}]
    rule = report["rule_signals"][0]
    assert (rule["outcome"], rule["next_action"], rule["logic_ref"]) == ("differs_from_existing", "clarify_scope",
                                                                         "constraint:equipment_power_positive@1.0.0")
    assert rule["comparison"]["existing"]["operator"] == "gt" and rule["comparison"]["extracted"]["operator"] == "gte"
    assert report["unmapped_recorded"] == 1
    assert report["coverage"]["blocks_total"] == 13 and report["coverage"]["blocks_with_evidence"] == 8
    b7 = next(item for item in report["coverage"]["uncovered_cue_blocks"] if item["block_id"] == "b7")
    assert {cue["match"] for cue in b7["cues"]} == {"测试台", "额定功率"}
    assert service.db.first("SELECT count(*) AS count FROM changeset")["count"] == before
    assert service.extraction_v2_queue()["data"] == []
    volatile = {"evaluated_at", "registry_fingerprint", "contract_id"}
    documented = example("06-validation-report.json")
    assert {k: v for k, v in report.items() if k not in volatile} == {k: v for k, v in documented.items() if k not in volatile}
    assert receipt == example("04-source-receipt.json")


def test_stale_contract_unknown_source_and_mode_guards(service):
    contract, _, message = worked_example(service)
    stale = copy.deepcopy(message)
    stale["contract"]["registry_fingerprint"] = "0" * 64
    report = service.extraction_v2_precheck(stale)["data"]
    assert report["status"] == "rejected" and report["issues"][0]["code"] == "CONTRACT_STALE"
    unknown = copy.deepcopy(message)
    unknown["source"]["document_version"] = "v9"
    assert service.extraction_v2_precheck(unknown)["data"]["issues"][0]["code"] == "SOURCE_UNKNOWN"
    tampered = copy.deepcopy(message)
    tampered["source"]["content_hash"] = "f" * 64
    assert service.extraction_v2_precheck(tampered)["data"]["issues"][0]["code"] == "SOURCE_HASH_MISMATCH"
    with pytest.raises(ValidationError, match="mode must be"):
        service.extraction_v2_submit(message, actor="agent:extractor")
    proposal = copy.deepcopy(message)
    proposal["mode"] = "propose"
    with pytest.raises(ValidationError, match="reason"):
        service.extraction_v2_submit(proposal, actor="agent:extractor")
    broken = copy.deepcopy(message)
    del broken["instances"][0]["fields"][0]["evidence"]
    with pytest.raises(ValidationError, match="schema"):
        service.extraction_v2_precheck(broken)


def test_anchor_value_and_normalized_quote_rules(service):
    contract = contract_for(service)
    source = source_document("source:test:anchors", "v1", [
        {"id": "p1", "text": "Acme 表示：华东工厂已投产。"},
        {"id": "p2", "text": "Acme  Industrial   Systems Ltd. 为法定名称。"},
    ])
    service.extraction_v2_register(source, actor="agent:extractor")
    label = {"raw": "Acme", "evidence": [anchor(source, "p1", "Acme", "subject")]}
    fields = [
        {"predicate": "finding", "value": {"raw": "华东工厂已投产"}, "evidence": [anchor(source, "p1", "华东工厂已投产", "value")]},
        {"predicate": "finding", "value": {"raw": "华东工厂已投产"},
         "evidence": [{"block_id": "p1", "start": 0, "end": 4, "quote": "华东工厂", "role": "value"}]},
        {"predicate": "finding", "value": {"raw": "x"}, "evidence": [{"block_id": "p1", "start": 90, "end": 95, "quote": "abcde"}]},
        {"predicate": "finding", "value": {"raw": "x"}, "evidence": [{"block_id": "missing", "start": 0, "end": 1, "quote": "x"}]},
        {"predicate": "legal_name", "value": {"raw": "Acme Industrial Systems Ltd."},
         "evidence": [{"block_id": "p2", "start": 0, "end": 31, "quote": "Acme Industrial Systems Ltd.", "role": "value"}]},
        {"predicate": "finding", "value": {"raw": "未出现的内容"}, "evidence": [anchor(source, "p1", "华东工厂已投产", "value")]},
        {"predicate": "rated_power", "value": {"raw": "5kW"}, "evidence": [anchor(source, "p1", "Acme")]},
    ]
    report = service.extraction_v2_precheck(submission(contract, source, [
        {"local_id": "tmp:org", "concept": "organization", "label": label, "fields": fields}]))["data"]
    assert_protocol(service, report)
    results = report["instances"][0]["fields"]
    assert results[0]["outcome"] == "accepted"
    codes = [{issue["code"] for issue in item.get("issues", [])} for item in results]
    assert "QUOTE_MISMATCH" in codes[1] and "ANCHOR_OUT_OF_RANGE" in codes[2] and "ANCHOR_BLOCK_UNKNOWN" in codes[3]
    assert results[4]["outcome"] == "already_present" and "QUOTE_NORMALIZED_MATCH" in codes[4]
    assert "VALUE_NOT_IN_EVIDENCE" in codes[5] and "PREDICATE_NOT_BOUND" in codes[6]
    assert report["status"] == "partially_accepted"


def test_propose_creates_governed_changesets_queues_and_is_idempotent(service, workspace):
    _, _, message = worked_example(service)
    message.update({"mode": "propose", "reason": "2026Q3 供应商评审纪要"})
    report = service.extraction_v2_submit(message, actor="agent:extractor")["data"]
    assert_protocol(service, report)
    targets = {item["target_source"]: item for item in report["changesets"]}
    assert set(targets) == {"knowledge/entities/org-acme.md", "knowledge/entities/product-servo-module.md"}
    product = service.db.first("SELECT risk,status FROM changeset WHERE id=?", (targets["knowledge/entities/product-servo-module.md"]["id"],))
    assert product["risk"] == "high"
    assert service.extraction_v2_submit(message, actor="agent:extractor")["data"]["changesets"] == report["changesets"]
    changed = copy.deepcopy(message)
    changed["reason"] = "另一个原因"
    with pytest.raises(ConflictError):
        service.extraction_v2_submit(changed, actor="agent:extractor")
    kinds = sorted(item["kind"] for item in service.extraction_v2_queue()["data"])
    assert kinds == ["rule_signal", "unmapped_fact", "upstream_required"]
    upstream = service.extraction_v2_queue(kind="upstream_required")["data"][0]
    assert upstream["target_id"] == "product:servo-module" and upstream["predicate"] == "lead_time_days"
    assert {row["kind"] for row in service.review()["data"]} >= {"extraction_upstream_required", "extraction_rule_signal"}
    org_changeset = targets["knowledge/entities/org-acme.md"]["id"]
    service.review_changeset(id=org_changeset, reviewer="reviewer:test", decision="approved")
    applied = service.apply_changeset(id=org_changeset, publisher="publisher:test")["data"]
    service.publish_changeset(id=org_changeset, publisher="publisher:test", source_revision=applied["source_revision"])
    published, _ = frontmatter(workspace / "knowledge/entities/org-acme.md")
    finding = published["knowledge"]["assertions"][-1]
    assert finding["predicate"] == "finding" and finding["epistemic"]["status"] == "proposed"
    assert finding["temporal"]["observed_at"] == "2026-10-15T00:00:00+08:00"
    assert finding["provenance"]["evidence_refs"][0].startswith("source:dms:supplier-review:acme-2026q3@v1#b4:")
    assert published["base"]["version"]["entity_revision"] == 2
    binding = service.db.first("SELECT node_id FROM durable.source_entity_binding WHERE source_id=?",
                               ("source:dms:supplier-review:acme-2026q3",))
    assert binding == {"node_id": "org:acme"}
    assert service.ledger.verify()["valid"]
    events = {event["event_type"] for event in service.ledger.events(limit=50)}
    assert {"extraction_source_registered", "extraction_v2_submission"} <= events


def test_new_instances_relations_between_them_and_draft_routing(service, workspace):
    contract = contract_for(service)
    source = source_document("source:test:new-supplier", "v1", [
        {"id": "h1", "type": "heading", "text": "新供应商"},
        {"id": "p1", "text": "Beta Robotics（Beta Robotics Co., Ltd.）供应 Gripper G2 夹爪模块，定位为轻载协作夹爪。"},
        {"id": "p2", "text": "Gamma Motion 是潜在供应商，尚未提供资料。"},
    ])
    receipt = service.extraction_v2_register(source, actor="agent:extractor")["data"]
    assert receipt["stored"] and service.extraction_v2_source("source:test:new-supplier")["data"]["original_archived"]
    instances = [
        {"local_id": "tmp:beta", "concept": "organization", "label": {"raw": "Beta Robotics", "evidence": [anchor(source, "p1", "Beta Robotics")]},
         "fields": [{"predicate": "legal_name", "value": {"raw": "Beta Robotics Co., Ltd."},
                     "evidence": [anchor(source, "p1", "Beta Robotics Co., Ltd.", "value")]}]},
        {"local_id": "tmp:g2", "concept": "product", "label": {"raw": "Gripper G2", "evidence": [anchor(source, "p1", "Gripper G2")]},
         "fields": [{"predicate": "summary", "value": {"raw": "定位为轻载协作夹爪"}, "evidence": [anchor(source, "p1", "定位为轻载协作夹爪", "value")]}]},
        {"local_id": "tmp:gamma", "concept": "organization", "label": {"raw": "Gamma Motion", "evidence": [anchor(source, "p2", "Gamma Motion")]},
         "fields": []},
    ]
    relations = [{"local_id": "tmp:supplies", "predicate": "supplies", "source": {"local_id": "tmp:beta"}, "target": {"local_id": "tmp:g2"},
                  "evidence": [anchor(source, "p1", "供应", "context")]},
                 {"local_id": "tmp:gamma-supplies", "predicate": "supplies", "source": {"local_id": "tmp:gamma"}, "target": {"local_id": "tmp:g2"},
                  "evidence": [anchor(source, "p2", "潜在供应商", "context")]},
                 {"local_id": "tmp:bad", "predicate": "supplies", "source": {"local_id": "tmp:g2"}, "target": {"local_id": "tmp:beta"},
                  "evidence": [anchor(source, "p1", "供应", "context")]}]
    message = submission(contract, source, instances, mode="propose", relations=relations, submission_id="sub-new-1")
    report = service.extraction_v2_submit(message, actor="agent:extractor")["data"]
    assert_protocol(service, report)
    outcomes = {item["local_id"]: item for item in report["instances"]}
    assert outcomes["tmp:beta"]["outcome"] == "new" and outcomes["tmp:beta"]["node_id"] == "org:beta-robotics"
    assert outcomes["tmp:g2"]["outcome"] == "new" and outcomes["tmp:g2"]["node_id"] == "product:gripper-g2"
    assert outcomes["tmp:gamma"]["outcome"] == "draft"
    assert report["coverage"]["missing_required"] == [{"local_id": "tmp:gamma", "predicates": ["legal_name"]}]
    relation_outcomes = {item["local_id"]: item["outcome"] for item in report["relations"]}
    assert relation_outcomes == {"tmp:supplies": "accepted", "tmp:gamma-supplies": "deferred", "tmp:bad": "rejected"}
    assert {item["target_source"] for item in report["changesets"]} == {"knowledge/entities/org-beta-robotics.md",
                                                                         "knowledge/entities/product-gripper-g2.md"}
    assert [item["kind"] for item in service.extraction_v2_queue()["data"]] == ["draft_instance"]
    by_target = {item["target_source"]: item["id"] for item in report["changesets"]}
    for target in ("knowledge/entities/product-gripper-g2.md", "knowledge/entities/org-beta-robotics.md"):
        service.review_changeset(id=by_target[target], reviewer="reviewer:test", decision="approved")
        applied = service.apply_changeset(id=by_target[target], publisher="publisher:test")["data"]
        service.publish_changeset(id=by_target[target], publisher="publisher:test", source_revision=applied["source_revision"])
    beta, _ = frontmatter(workspace / "knowledge/entities/org-beta-robotics.md")
    assert beta["base"]["node"]["label"] == "Beta Robotics" and beta["knowledge"]["attrs"]["legal_name"] == "Beta Robotics Co., Ltd."
    assert beta["knowledge"]["relations"] == [{"predicate": "supplies", "target": "product:gripper-g2"}]
    assert service.db.first("SELECT label FROM node WHERE id=?", ("product:gripper-g2",))["label"] == "Gripper G2"


def test_active_business_constraint_rejects_extracted_values(service, workspace):
    (workspace / "control/constraints/business/equipment_utilization_cap.yaml").write_text("""format: knowledgeos.business-constraint.v1
id: equipment_utilization_cap
label: 设备利用率不超过 90%
description: 测试用约束。
version: 1.0.0
status: {lifecycle: active}
scope: {subject_concept: equipment}
inputs:
  - {id: utilization, role: subject, predicate: utilization_factor}
checks:
  - left: utilization
    operator: lte
    right: {value: {value: "0.9", unit: one}}
    message: 利用率不得超过 90%
""", encoding="utf-8")
    service.refresh_registry()
    service.compile()
    contract = contract_for(service, concepts=["equipment"])
    source = source_document("source:test:equipment", "v1", [{"id": "p1", "text": "3号测试台（设备编号 EQ-TB-03）利用率约95%，另一台利用率 80%。"}])
    service.extraction_v2_register(source, actor="agent:extractor")
    label = {"raw": "3号测试台", "evidence": [anchor(source, "p1", "3号测试台")]}
    key = {"raw": "EQ-TB-03", "evidence": [anchor(source, "p1", "EQ-TB-03", "key")]}
    report = service.extraction_v2_precheck(submission(contract, source, [
        {"local_id": "tmp:bench", "concept": "equipment", "label": label, "identity": {"key": key},
         "fields": [{"predicate": "utilization_factor", "value": {"raw": "约95%"}, "evidence": [anchor(source, "p1", "约95%", "value")]}]}]))["data"]
    assert_protocol(service, report)
    instance = report["instances"][0]
    field = instance["fields"][0]
    assert field["outcome"] == "rejected" and field["normalized"]["literal"] == "0.95"
    assert any(issue["code"] == "CONSTRAINT_VIOLATION" for issue in field["issues"])
    # Active constraints also gate compilation: without a valid fact the new equipment cannot be created.
    assert instance["outcome"] == "rejected" and "node_id" not in instance
    assert any(issue["code"] == "CONSTRAINT_VIOLATION" for issue in instance["issues"])
    assert report["status"] == "rejected"


def test_new_source_version_reports_block_diff_and_facts_at_risk(service):
    _, _, message = worked_example(service)
    message.update({"mode": "propose", "reason": "评审纪要首版"})
    service.extraction_v2_submit(message, actor="agent:extractor")
    original = example("03-source-document.json")
    assert service.extraction_v2_register(original, actor="agent:extractor")["data"]["stored"]
    updated = copy.deepcopy(original)
    updated["document"].update({"version": "v2", "previous_version": "v1"})
    blocks = updated["blocks"]
    blocks[3]["text"] = "Acme（Acme Industrial Systems Ltd.）表示，2026年三季度已在华东新增三条伺服模块装配线。"
    updated["blocks"] = [block for block in blocks if block["id"] != "b8"]
    receipt = service.extraction_v2_register(updated, actor="agent:extractor")["data"]
    assert_protocol(service, receipt)
    assert receipt["diff"]["previous_version"] == "v1" and receipt["diff"]["changed"] == ["b4"] and receipt["diff"]["removed"] == ["b8"]
    at_risk = {(item["block_id"], item["change"]) for item in receipt["facts_at_risk"]}
    assert ("b4", "changed") in at_risk
    assert any(row["kind"] == "extraction_fact_at_risk" for row in service.review()["data"])
    same = copy.deepcopy(updated)
    assert service.extraction_v2_register(same, actor="agent:extractor")["data"] == receipt
    conflicting = copy.deepcopy(updated)
    conflicting["blocks"][0]["text"] = "改动后的标题"
    with pytest.raises(ConflictError):
        service.extraction_v2_register(conflicting, actor="agent:extractor")
    invalid = copy.deepcopy(updated)
    invalid["document"]["version"] = "v3"
    invalid["blocks"].append({"id": "b9", "type": "table_cell", "text": "孤立单元格", "order": 99})
    rejected = service.extraction_v2_register(invalid, actor="agent:extractor")["data"]
    assert not rejected["stored"] and {item["code"] for item in rejected["issues"]} >= {"BLOCK_INVALID"}


def test_http_routes_and_permissions(service):
    auth = AccessControl.from_env({"KNOWLEDGEOS_AUTH_TOKENS": json.dumps({
        "reader-token": {"principal": "user:reader", "roles": ["reader"]},
        "agent-token": {"principal": "agent:extractor", "roles": ["agent"]}})})
    server = KnowledgeServer(("127.0.0.1", 0), service, auth)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    def request(method, path, token, body=None):
        connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=10)
        headers = {"Authorization": "Bearer " + token, "Content-Type": "application/json"}
        connection.request(method, path, body=json.dumps(body) if body is not None else None, headers=headers)
        response = connection.getresponse()
        result = response.status, json.loads(response.read())
        connection.close()
        return result

    try:
        contract_request = example("01-contract-request.json")
        status, contract = request("POST", "/v2/extraction/contract", "reader-token", contract_request)
        assert status == 200 and contract["data"]["message_type"] == "extraction_contract"
        assert request("GET", "/v2/extraction/contract", "reader-token")[0] == 405
        source = example("03-source-document.json")
        assert request("POST", "/v2/extraction/sources", "reader-token", source)[0] == 403
        status, receipt = request("POST", "/v2/extraction/sources", "agent-token", source)
        assert status == 200 and receipt["data"]["stored"]
        message = example("05-extraction-submission.json")
        message["contract"] = {"contract_id": contract["data"]["contract_id"], "registry_fingerprint": contract["data"]["registry_fingerprint"]}
        status, report = request("POST", "/v2/extraction/precheck", "agent-token", message)
        assert status == 200 and report["data"]["status"] == "accepted"
        message.update({"mode": "propose", "reason": "HTTP 提案"})
        assert request("POST", "/v2/extraction/precheck", "agent-token", message)[0] == 422
        status, proposal = request("POST", "/v2/extraction/submissions", "agent-token", message)
        assert status == 201 and len(proposal["data"]["changesets"]) == 2
        assert service.db.first("SELECT actor FROM changeset WHERE id=?", (proposal["data"]["changesets"][0]["id"],))["actor"] == "agent:extractor"
        status, queue = request("GET", "/v2/extraction/queue?kind=upstream_required", "reader-token")
        assert status == 200 and len(queue["data"]) == 1
        status, stored = request("GET", "/v2/extraction/source?id=source:dms:supplier-review:acme-2026q3", "reader-token")
        assert status == 200 and stored["data"]["versions"] == ["v1"]
        status, assets = request("GET", "/v2/extraction/assets", "reader-token")
        assert status == 200 and assets["data"]["profile"]["exists"]
        card = next(item for item in assets["data"]["concepts"] if item["id"] == "product")["card"]["document"]
        assert request("POST", "/v2/extraction/assets/preview", "reader-token", {"kind": "card", "document": card})[0] == 403
        status, preview = request("POST", "/v2/extraction/assets/preview", "agent-token", {"kind": "card", "document": {**card, "card_version": "1.0.1"}})
        assert status == 200 and preview["data"]["valid"]
        status, created = request("POST", "/v2/extraction/assets/propose", "agent-token",
                                  {"kind": "card", "document": {**card, "card_version": "1.0.1"}, "reason": "HTTP 说明卡提案"})
        assert status == 201 and created["data"]["target_source"] == "control/extraction/v2/cards/product.yaml"
    finally:
        server.shutdown()
        server.server_close()


def test_ontology_drift_drops_stale_card_entries_without_blocking_the_registry(service, workspace):
    path = workspace / "control/ontology/core.yaml"
    text = path.read_text(encoding="utf-8")
    decision = text.index("  - id: decision\n")
    finding = text.index("      - { predicate: finding", decision)
    path.write_text(text[:finding] + text[text.index("\n", finding) + 1:], encoding="utf-8")
    service.refresh_registry()
    warnings = service.control_plane()["data"]["extraction_v2"]["warnings"]
    assert any("finding is not bound to decision" in item for item in warnings)
    assert "finding" not in service.registry.extraction_v2["cards"]["decision"]["properties"]
    contract = contract_for(service, concepts=["decision"])
    assert "finding" not in {item["predicate"] for item in contract["concepts"][0]["properties"]}


def test_extraction_assets_are_edited_through_governed_changesets(service, workspace):
    assets = service.extraction_v2_assets()["data"]
    equipment = next(item for item in assets["concepts"] if item["id"] == "equipment")
    assert equipment["card"]["exists"] and equipment["card"]["source_path"] == "control/extraction/v2/cards/equipment.yaml"
    assert {item["predicate"] for item in equipment["properties"]} >= {"rated_power", "utilization_factor"}
    assert next(item for item in equipment["properties"] if item["predicate"] == "rated_power")["publication_route"] == "upstream_source_system"
    assert [item["id"] for item in assets["recipes"]] == ["supplier_review_minutes"] and assets["profile"]["exists"]
    card = copy.deepcopy(equipment["card"]["document"])
    card["properties"]["rated_power"]["terms"]["alt"].append("电机功率")
    card["properties"]["rated_power"]["unit_aliases"] = {"千瓦特": "kW"}
    preview = service.extraction_v2_asset_preview(kind="card", document=card, samples=[{"predicate": "rated_power", "raw": "7.5千瓦特"}],
                                                  sample_text="3号测试台电机功率 7.5千瓦特")["data"]
    assert not preview["valid"] and "必须高于当前版本" in preview["errors"][0]
    assert preview["trials"][0]["normalized"] == {"type": "quantity", "literal": "7.5", "unit": "kW"}
    cues = {cue["match"] for cue in preview["cue_preview"][0]["cues"]}
    assert {"测试台", "电机功率", "7.5千瓦特"} <= cues
    assert "电机功率" in next(item for item in preview["contract"]["properties"] if item["predicate"] == "rated_power")["terms"]["alt"]
    card["card_version"] = "1.1.0"
    preview = service.extraction_v2_asset_preview(kind="card", document=card)["data"]
    assert preview["valid"], preview["errors"]
    unbound = copy.deepcopy(card)
    unbound["properties"]["finding"] = {"terms": {"alt": ["结论"]}}
    errors = service.extraction_v2_asset_preview(kind="card", document=unbound)["data"]["errors"]
    assert errors == ["control/extraction/v2/cards/equipment.yaml: finding is not bound to equipment"]
    with pytest.raises(ValidationError):
        service.extraction_v2_asset_propose(kind="card", document=unbound, reason="错误提案", actor="author:test")
    proposal = service.extraction_v2_asset_propose(kind="card", document=card, reason="补充功率写法", actor="author:test",
                                                   base_revision=preview["revision"])["data"]
    row = service.db.first("SELECT risk,title FROM changeset WHERE id=?", (proposal["id"],))
    assert row["risk"] == "high" and row["title"].startswith("抽取说明卡：")
    assert service.extraction_v2_assets()["data"]["pending"][0]["id"] == proposal["id"]
    with pytest.raises(ValidationError, match="independent reviewer"):
        service.review_changeset(id=proposal["id"], reviewer="author:test", decision="approved")
    service.review_changeset(id=proposal["id"], reviewer="reviewer:test", decision="approved")
    revision = service.apply_changeset(id=proposal["id"], publisher="publisher:test")["data"]["source_revision"]
    service.publish_changeset(id=proposal["id"], publisher="publisher:test", source_revision=revision)
    service.refresh_registry()
    published = service.registry.extraction_v2["cards"]["equipment"]
    assert published["card_version"] == "1.1.0" and published["properties"]["rated_power"]["unit_aliases"] == {"千瓦特": "kW"}
    recipe = {"format": "knowledgeos.extraction-recipe.v1", "id": "equipment_ledger", "version": "1.0.0", "document_types": ["equipment_ledger"],
              "mappings": [{"selector": {"kind": "table_header", "pattern": "^额定功率$"}, "concept": "equipment", "role": "field", "predicate": "rated_power"}]}
    created = service.extraction_v2_asset_propose(kind="recipe", document=recipe, reason="新增设备台账方案", actor="author:test")["data"]
    assert created["target_source"] == "control/extraction/v2/recipes/equipment_ledger.yaml"
    service.review_changeset(id=created["id"], reviewer="reviewer:test", decision="approved")
    revision = service.apply_changeset(id=created["id"], publisher="publisher:test")["data"]["source_revision"]
    service.publish_changeset(id=created["id"], publisher="publisher:test", source_revision=revision)
    service.refresh_registry()
    assert "equipment_ledger" in service.registry.extraction_v2["recipes"]
    assert (workspace / "control/extraction/v2/recipes/equipment_ledger.yaml").exists()
    profile = copy.deepcopy(service.extraction_v2_assets()["data"]["profile"]["document"])
    profile["acceptance"]["min_confidence"] = 1.5
    assert service.extraction_v2_asset_preview(kind="profile", document=profile)["data"]["errors"]


def test_contract_renders_standard_instructions_by_skill_stage(service):
    request = {"protocol_version": PROTOCOL, "message_type": "contract_request", "scope": {"domains": ["cost", "industry"], "document_type": "supplier_review_minutes"},
               "include": ["concepts", "relations", "recipes", "lexicon", "rule_slots", "instructions"]}
    contract = service.extraction_v2_contract(request)["data"]
    assert_protocol(service, contract)
    instructions = contract["instructions"]
    assert instructions["format"] == "knowledgeos.extraction-instructions.v1" and instructions["language"] == "zh"
    stages = [section["stage"] for section in instructions["sections"]]
    assert stages == ["all", "route", "mentions", "consolidate", "fill_fields", "relations", "rule_signals", "precheck_and_repair"]
    markdown = instructions["markdown"]
    assert hashlib.sha256(markdown.encode("utf-8")).hexdigest() == instructions["content_hash"]
    assert contract["contract_id"] in markdown
    equipment = instructions["concepts"]["equipment"]
    assert "用于 X1 出厂检验的测试台额定功率不得低于5kW" in equipment["mentions"] and "不应抽取" in equipment["mentions"]
    assert "千瓦 → kW" in equipment["fill_fields"] and "由业务系统维护" in equipment["fill_fields"]
    assert "`^EQ-[A-Z0-9-]{2,60}$`" in equipment["consolidate"]
    assert "rated_power > 0 W" in markdown and "表头或行标签 匹配 `^(标准)?交期$`" in markdown
    assert service.extraction_v2_contract(request)["data"]["instructions"]["content_hash"] == instructions["content_hash"]
    assert "instructions" not in contract_for(service)
    card = copy.deepcopy(next(item for item in service.extraction_v2_assets()["data"]["concepts"] if item["id"] == "equipment")["card"]["document"])
    card.update({"card_version": "1.0.1"})
    card["card"]["boundary_notes"].append("备用设备不单独建实例。")
    preview = service.extraction_v2_asset_preview(kind="card", document=card)["data"]
    assert "备用设备不单独建实例。" in preview["instructions"]["concepts"]["equipment"]["mentions"]


def test_feedback_aggregates_signals_and_resolves_after_card_publication(service, workspace):
    contract = contract_for(service, concepts=["organization", "project", "equipment"])
    source = source_document("source:test:feedback", "v1", [
        {"id": "p1", "text": "3号测试台（设备编号 EQ-TB-03）额定功率 7.5千瓦特。"},
        {"id": "p2", "text": "Project Atlas 风险评估：爆表。"},
        {"id": "p3", "text": "Acme 的供应商评级为 A 级。"},
        {"id": "p4", "text": "现场还有一台测试台在维修，铭牌功率不清。"},
    ])
    service.extraction_v2_register(source, actor="agent:extractor")
    instances = [
        {"local_id": "tmp:bench", "concept": "equipment", "label": {"raw": "3号测试台", "evidence": [anchor(source, "p1", "3号测试台")]},
         "identity": {"key": {"raw": "EQ-TB-03", "evidence": [anchor(source, "p1", "EQ-TB-03", "key")]}},
         "fields": [{"predicate": "rated_power", "value": {"raw": "7.5千瓦特"}, "evidence": [anchor(source, "p1", "7.5千瓦特", "value")]}]},
        {"local_id": "tmp:atlas", "concept": "project", "label": {"raw": "Project Atlas", "evidence": [anchor(source, "p2", "Project Atlas")]},
         "fields": [{"predicate": "risk_level", "value": {"raw": "爆表"}, "evidence": [anchor(source, "p2", "爆表", "value")]}]},
    ]
    unmapped = [{"text": "Acme 的供应商评级为 A 级", "reason": "no_matching_predicate", "suggested_concept": "organization",
                 "suggested_property_label": "供应商评级", "evidence": [anchor(source, "p3", "Acme 的供应商评级为 A 级")]}]
    message = submission(contract, source, instances, mode="propose", submission_id="sub-feedback-1", unmapped_facts=unmapped)
    service.extraction_v2_submit(message, actor="agent:extractor")
    feedback = service.extraction_v2_feedback()["data"]
    assert feedback["totals"]["submissions"] == 1 and feedback["totals"]["open_suggestions"] >= 4
    by_kind = {}
    for item in feedback["suggestions"]:
        by_kind.setdefault(item["kind"], []).append(item)
    unit = by_kind["unit_alias"][0]
    assert (unit["concept"], unit["predicate"], unit["text"], unit["status"]) == ("equipment", "rated_power", "千瓦特", "open")
    assert unit["action"]["choices"] == ["W", "kW", "MW"] and unit["samples"][0]["text"].startswith("3号测试台")
    enum = by_kind["enum_alias"][0]
    assert (enum["predicate"], enum["text"]) == ("risk_level", "爆表") and enum["action"]["choices"] == ["low", "medium", "high", "critical"]
    term = by_kind["property_term"][0]
    assert (term["concept"], term["text"]) == ("organization", "供应商评级") and "legal_name" in term["action"]["choices"]
    assert any(item["text"] == "测试台" for item in by_kind["uncovered_cue"])
    stats = {item["concept"]: item for item in feedback["concepts"]}
    assert stats["equipment"]["fields"]["rejected"] == 1 and stats["equipment"]["issues"]["UNIT_NOT_ALLOWED"] == 1
    assert len(feedback["versions"]) == 1 and feedback["versions"][0]["current"] and feedback["versions"][0]["field_acceptance_rate"] == 0
    assert service.extraction_v2_feedback(concept="project")["data"]["suggestions"][0]["kind"] == "enum_alias"
    # Applying the suggestion is a governed card change; afterwards the suggestion is resolved and a new version is measured.
    card = copy.deepcopy(next(item for item in service.extraction_v2_assets()["data"]["concepts"] if item["id"] == "equipment")["card"]["document"])
    card["card_version"] = "1.0.1"
    card["properties"]["rated_power"]["unit_aliases"] = {"千瓦特": "kW"}
    proposal = service.extraction_v2_asset_propose(kind="card", document=card, reason="反馈：补充单位写法", actor="author:test")["data"]
    service.review_changeset(id=proposal["id"], reviewer="reviewer:test", decision="approved")
    revision = service.apply_changeset(id=proposal["id"], publisher="publisher:test")["data"]["source_revision"]
    service.publish_changeset(id=proposal["id"], publisher="publisher:test", source_revision=revision)
    service.refresh_registry()
    resolved = next(item for item in service.extraction_v2_feedback()["data"]["suggestions"] if item["kind"] == "unit_alias")
    assert resolved["status"] == "resolved"
    renewed = contract_for(service, concepts=["organization", "project", "equipment"])
    retry = submission(renewed, source, instances[:1], mode="propose", submission_id="sub-feedback-2")
    service.extraction_v2_submit(retry, actor="agent:extractor")
    versions = service.extraction_v2_feedback(concept="equipment")["data"]["versions"]
    assert [item["field_acceptance_rate"] for item in versions] == [0, 1] and versions[1]["current"]
    with pytest.raises(ValidationError):
        service.extraction_v2_feedback(concept="unknown")
