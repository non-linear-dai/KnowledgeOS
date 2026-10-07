import copy

import pytest
import yaml

from knowledge_os.core import ValidationError, frontmatter
from knowledge_os.entity_resolution import mentioned


def packet(service, source, entities):
    request = service.extraction_request(source=source)["data"]
    output = {"protocol_version": request["protocol_version"],
              "registry_fingerprint": request["registry_fingerprint"], "entities": entities}
    return request, output


def evidence(quote):
    return [{"segment_id": "segment:0001", "quote": quote}]


def test_identity_mentions_in_chinese_and_latin_text():
    assert mentioned("项目阿特拉斯", "本次项目阿特拉斯的风险升高")
    assert mentioned("ATLAS-M1", "ATLAS-M1 的状态待确认")
    assert not mentioned("Atlas", "AtlasWorks 的项目")


def test_incremental_attribute_and_assessment_publish_with_binding(service, workspace):
    quote = "Project Atlas has a revised delivery plan and high risk from supplier delay."
    source = {"kind": "meeting_minutes", "locator": "meeting://atlas/review-42", "content": quote}
    entity = {"type": "project", "label": "Project Atlas", "attrs": {"summary": "Revised delivery plan."},
              "assertions": [{"predicate": "risk_level", "value": "high",
                              "supersedes_id": "assertion:atlas-risk-2026-09"}],
              "relations": [], "confidence": 0.95, "evidence": evidence(quote)}
    request, output = packet(service, source, [entity])
    result = service.extraction_candidates(request=request, model_output=output)["data"]
    assert result["rejected"] == result["unresolved"] == []
    candidate = result["candidates"][0]
    assert candidate["action"] == "update_instance" and candidate["target_id"] == "project:atlas"
    assert candidate["publication_route"] == "governed_changeset"
    assert {item["predicate"] for item in candidate["attribute_candidates"]} == {"summary"}
    assert any(item["path"] == "/knowledge/assertions/-" and item["value"]["version"]["supersedes"] == "assertion:atlas-risk-2026-09"
               for item in candidate["operations"])
    assert frontmatter(workspace / "knowledge/entities/project-atlas.md")[0]["knowledge"]["attrs"]["summary"] != "Revised delivery plan."
    proposal = service.propose_extraction_candidate(request=request, model_output=output, input_index=0,
        actor="agent:test", reason="reviewed meeting evidence", idempotency_key="atlas-review-42")["data"]
    assert service.propose_extraction_candidate(request=request, model_output=output, input_index=0,
        actor="agent:test", reason="reviewed meeting evidence", idempotency_key="atlas-review-42")["data"]["id"] == proposal["id"]
    service.review_changeset(id=proposal["id"], reviewer="reviewer:test", decision="approved")
    applied = service.apply_changeset(id=proposal["id"], publisher="publisher:test")["data"]
    service.publish_changeset(id=proposal["id"], publisher="publisher:test", source_revision=applied["source_revision"])
    published = frontmatter(workspace / "knowledge/entities/project-atlas.md")[0]
    assert published["knowledge"]["attrs"]["summary"] == "Revised delivery plan."
    assert published["base"]["version"]["entity_revision"] == 2
    assert published["knowledge"]["assertions"][-1]["epistemic"]["status"] == "proposed"
    binding = service.db.first("SELECT node_id,source_hash FROM durable.source_entity_binding WHERE source_id=?", (request["source"]["id"],))
    assert binding == {"node_id": "project:atlas", "source_hash": request["source"]["content_hash"]}
    assert service.propose_extraction_candidate(request=request, model_output=output, input_index=0,
        actor="agent:test", reason="reviewed meeting evidence", idempotency_key="atlas-review-42")["data"]["id"] == proposal["id"]
    assert service.ledger.verify()["valid"]
    service.compile(rebuild=True)
    assert service.db.first("SELECT node_id FROM durable.source_entity_binding WHERE source_id=?", (request["source"]["id"],))["node_id"] == "project:atlas"
    updated_request = service.extraction_request(source={**source, "content": quote + " Follow-up pending."})["data"]
    assert updated_request["source"]["id"] == request["source"]["id"]
    assert updated_request["source"]["content_hash"] != request["source"]["content_hash"]


def test_ambiguous_or_unsupported_identity_never_creates_or_updates(service, workspace):
    original, body = frontmatter(workspace / "knowledge/entities/org-acme.md")
    duplicate = copy.deepcopy(original)
    duplicate["base"]["node"].update({"id": "org:acme-duplicate", "key": "ACME-OTHER", "key_namespace": "alternate"})
    duplicate["knowledge"]["assertions"] = []
    path = workspace / "knowledge/entities/org-acme-duplicate.md"
    path.write_text("---\n" + yaml.safe_dump(duplicate, sort_keys=False) + "---\n" + body)
    service.compile()
    quote = "Acme Industrial Systems has updated its profile."
    entity = {"type": "organization", "label": "Acme Industrial Systems", "attrs": {"legal_name": "Acme Industrial Systems"},
              "assertions": [], "relations": [], "confidence": 0.99, "evidence": evidence(quote)}
    request, output = packet(service, {"kind": "text", "content": quote}, [entity])
    result = service.extraction_candidates(request=request, model_output=output)["data"]
    assert not result["candidates"] and len(result["unresolved"][0]["candidates"]) == 2
    with pytest.raises(ValidationError, match="unresolved"):
        service.propose_extraction_candidate(request=request, model_output=output, input_index=0,
                                             actor="agent:test", reason="unsafe")
    proposal = service.propose_extraction_candidate(request=request, model_output=output, input_index=0,
        actor="reviewer:resolver", reason="reviewed same-name instance", resolved_id="org:acme",
        resolution_reason="verified against the source record")["data"]
    assert proposal["status"] == "review_required"
    with pytest.raises(ValidationError, match="independent reviewer"):
        service.review_changeset(id=proposal["id"], reviewer="reviewer:resolver", decision="approved")
    output["entities"][0]["existing_id"] = "org:acme"
    assert service.extraction_candidates(request=request, model_output=output)["data"]["unresolved"]
    unsupported = {**entity, "label": "Newco"}
    unsupported.pop("existing_id", None)
    request, output = packet(service, {"kind": "text", "content": quote}, [unsupported])
    assert service.extraction_candidates(request=request, model_output=output)["data"]["unresolved"][0]["reason"] == "new identity is absent from cited evidence"


def test_scoped_key_context_and_operational_fact_route(service, workspace):
    quote = "Project Atlas: Atlas Milestone 1 has a new summary and is blocked."
    entity = {"type": "task", "label": "Atlas Milestone 1", "key": "ATLAS-M1", "context_ids": ["project:atlas"],
              "attrs": {"summary": "Awaiting supplier material."},
              "assertions": [{"predicate": "task_status", "value": "blocked"}],
              "relations": [], "confidence": 0.9, "evidence": evidence(quote)}
    request, output = packet(service, {"kind": "meeting_minutes", "content": quote}, [entity])
    result = service.extraction_candidates(request=request, model_output=output)["data"]
    assert result["candidates"][0]["target_id"] == "task:atlas-m1"
    assert {item["path"] for item in result["candidates"][0]["operations"]} == {
        "/knowledge/attrs/summary", "/base/version/entity_revision"}
    assert result["unmapped_facts"][0]["text"] == "task_status"
    assert frontmatter(workspace / "knowledge/entities/task-atlas-m1.md")[0]["knowledge"]["attrs"]["summary"] != "Awaiting supplier material."
    with pytest.raises(ValidationError, match="authoritative connector"):
        service.propose(actor="agent:test", target_source="knowledge/entities/task-atlas-m1.md",
            patch={"op": "add", "path": "/knowledge/assertions/-",
                   "value": {"id": "assertion:wrong-route", "predicate": "task_status"}},
            reason="attempt to write operational status")


def test_linked_context_disambiguates_duplicate_instance_names(service, workspace):
    original, body = frontmatter(workspace / "knowledge/entities/task-atlas-m1.md")
    duplicate = copy.deepcopy(original)
    duplicate["base"]["node"].update({"id": "task:other-m1", "key": "OTHER-M1"})
    duplicate["knowledge"]["assertions"][0]["id"] = "assertion:other-m1-status"
    (workspace / "knowledge/entities/task-other-m1.md").write_text(
        "---\n" + yaml.safe_dump(duplicate, sort_keys=False) + "---\n" + body)
    service.compile()
    quote = "Project Atlas reviewed Atlas Milestone 1 today."
    entity = {"type": "task", "label": "Atlas Milestone 1", "attrs": {"summary": "Review held."},
              "assertions": [], "relations": [], "confidence": 0.9, "evidence": evidence(quote)}
    request, output = packet(service, {"kind": "meeting_minutes", "content": quote}, [entity])
    assert service.extraction_candidates(request=request, model_output=output)["data"]["unresolved"]
    output["entities"][0]["context_ids"] = ["project:atlas"]
    candidate = service.extraction_candidates(request=request, model_output=output)["data"]["candidates"][0]
    assert candidate["target_id"] == "task:atlas-m1"
    assert "linked_context" in candidate["resolution"]["reason"]


def test_resolver_searches_beyond_bounded_model_hints(service, workspace):
    profile = workspace / "control/extraction/default.yaml"
    profile.write_text(profile.read_text().replace("existing_entity_limit: 500", "existing_entity_limit: 1"))
    quote = "Acme discussed Project Atlas."
    entity = {"type": "project", "label": "Project Atlas", "attrs": {"summary": "Discussion recorded."},
              "assertions": [], "relations": [], "confidence": 0.9, "evidence": evidence(quote)}
    request, output = packet(service, {"kind": "meeting_minutes", "content": quote}, [entity])
    assert "project:atlas" not in {item["id"] for item in request["existing_entities"]}
    candidate = service.extraction_candidates(request=request, model_output=output)["data"]["candidates"][0]
    assert candidate["action"] == "update_instance" and candidate["target_id"] == "project:atlas"


def test_decision_instance_incremental_and_versioned_table_guard(service, workspace):
    quote = "Decision Gate A has a new finding: supplier evidence remains incomplete."
    entity = {"type": "decision", "label": "Decision Gate A", "key": "GATE-A",
              "attrs": {"summary": "Supplier evidence review."},
              "assertions": [{"predicate": "finding", "value": "Supplier evidence remains incomplete."}],
              "relations": [], "confidence": 0.9, "evidence": evidence(quote)}
    source = {"kind": "meeting_minutes", "locator": "meeting://decision/gate-a", "content": quote}
    request, output = packet(service, source, [entity])
    result = service.extraction_candidates(request=request, model_output=output)["data"]
    assert result["candidates"][0]["action"] == "create_instance"
    assert result["candidates"][0]["proposed_instance"]["knowledge"]["assertions"][0]["predicate"] == "finding"
    first = service.propose_extraction_candidate(request=request, model_output=output, input_index=0,
        actor="agent:test", reason="reviewed decision note")["data"]
    service.review_changeset(id=first["id"], reviewer="reviewer:test", decision="approved")
    applied = service.apply_changeset(id=first["id"], publisher="publisher:test")["data"]
    service.publish_changeset(id=first["id"], publisher="publisher:test", source_revision=applied["source_revision"])
    updated_quote = "Decision Gate A now has a new finding: supplier evidence passed review."
    updated = {**entity, "attrs": {"summary": "Supplier evidence approved."},
               "assertions": [{"predicate": "finding", "value": "Supplier evidence passed review."}],
               "evidence": evidence(updated_quote)}
    next_request, next_output = packet(service, {**source, "content": updated_quote}, [updated])
    next_candidate = service.extraction_candidates(request=next_request, model_output=next_output)["data"]["candidates"][0]
    assert next_candidate["action"] == "update_instance"
    assert next_candidate["resolution"]["reason"] == "confirmed_source_binding"
    second = service.propose_extraction_candidate(request=next_request, model_output=next_output, input_index=0,
        actor="agent:test", reason="reviewed follow-up decision")["data"]
    service.review_changeset(id=second["id"], reviewer="reviewer:test", decision="approved")
    applied = service.apply_changeset(id=second["id"], publisher="publisher:test")["data"]
    service.publish_changeset(id=second["id"], publisher="publisher:test", source_revision=applied["source_revision"])
    decision = frontmatter(workspace / "knowledge/entities/decision-gate-a.md")[0]
    assert decision["knowledge"]["attrs"]["summary"] == "Supplier evidence approved."
    assert len(decision["knowledge"]["assertions"]) == 2
    table = {**entity, "type": "decision_table", "attrs": {"decision_definition": {}}, "assertions": []}
    request, output = packet(service, {"kind": "meeting_minutes", "content": quote}, [table])
    assert "expert template workbench" in service.extraction_candidates(request=request, model_output=output)["data"]["rejected"][0]["errors"][0]


def test_tier_a_assessment_requires_stable_locator(service):
    quote = "Project Atlas risk is high."
    entity = {"type": "project", "label": "Project Atlas", "attrs": {},
              "assertions": [{"predicate": "risk_level", "value": "high"}], "relations": [],
              "confidence": 0.9, "evidence": evidence(quote)}
    request, output = packet(service, {"kind": "meeting_minutes", "content": quote}, [entity])
    assert "stable source locator" in service.extraction_candidates(request=request, model_output=output)["data"]["rejected"][0]["errors"][0]
