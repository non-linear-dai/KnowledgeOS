import http.client
import importlib.util
import json
import re
import shutil
import sys
import threading
from pathlib import Path

import pytest

from knowledge_os.api import AccessControl, KnowledgeServer
from knowledge_os.authoring import Authoring, assistance_metadata, render_instructions
from knowledge_os.core import ValidationError, digest
from knowledge_os.logic_analysis import analyze_business, analyze_decision_table, confirm_cases
from knowledge_os.similarity import dice, similar_concepts, similar_predicates

REPO = Path(__file__).resolve().parents[1]
UNITS = {"kW": {"dimension": "power", "factor_to_base": "1000"}}
DESCRIPTION = "零件尺寸不超过300mm用小型注塑机，300到600用中型，超过600用大型。"


def table(**overrides):
    value = {"id": "size_machine", "version": "1.0.0", "label": "尺寸选机", "description": "按零件尺寸选择注塑机", "hit_policy": "FIRST",
             "inputs": [{"id": "size", "label": "尺寸", "source": "facts", "field": "size", "type": "number"}],
             "outputs": [{"id": "machine", "label": "设备", "type": "string"}],
             "rules": [{"id": "small", "when": {"size": {"op": "lte", "value": "300"}}, "then": {"machine": "small"}},
                       {"id": "mid", "when": {"size": {"op": "between", "value": ["300", "600"]}}, "then": {"machine": "mid"}}]}
    value.update(overrides)
    return value


def draft(definition=None, **overrides):
    value = {"protocol_version": "knowledgeos.authoring.v1", "task": "logic.draft", "summary": "按尺寸分三档", "kind": "decision_table",
             "definition": definition or table(),
             "test_cases": [{"id": "large", "label": "700 mm 用大型", "input": {"facts": {"size": "700"}}, "expected": {"result": {"machine": "large"}}},
                            {"id": "small", "label": "100 mm 用小型", "input": {"facts": {"size": "100"}}, "expected": {"result": {"machine": "small"}}}],
             "evidence": [{"quote": "超过600用大型"}]}
    value.update(overrides)
    return value


def test_decision_coverage_finds_gaps_unreachable_rules_and_boundaries():
    shadowed = table(rules=[*table()["rules"], {"id": "tiny", "when": {"size": {"op": "lt", "value": "100"}}, "then": {"machine": "tiny"}}])
    result = analyze_decision_table(shadowed, UNITS)
    assert result["exhaustive"] is True
    assert result["gaps"] == [{"size": "> 600"}] and result["gap_count"] == 1
    assert result["unreachable_rules"] == ["tiny"]
    labels = {case["input"]["facts"]["size"]: case for case in result["boundary_cases"]}
    assert labels["300"]["actual"] == {"result": {"machine": "small"}} and labels["300"]["matched_rules"] == ["small"]
    assert "600" in labels and labels["600"]["actual"] == {"result": {"machine": "mid"}}
    outside = [case for case in result["boundary_cases"] if "error" in case]
    assert outside and all("no matching rule" in case["error"] for case in outside)
    confirmed = confirm_cases(result["boundary_cases"], [labels["300"]["id"], outside[0]["id"]])
    assert confirmed == [{"id": labels["300"]["id"], "label": labels["300"]["label"], "input": {"facts": {"size": "300"}},
                          "expected": {"result": {"machine": "small"}}}]


def test_decision_coverage_handles_strings_booleans_defaults_and_merges_regions():
    value = table(hit_policy="UNIQUE", inputs=[
        {"id": "material", "source": "facts", "field": "material", "type": "string"},
        {"id": "urgent", "source": "item", "field": "urgent", "type": "boolean"}],
        rules=[{"id": "steel", "when": {"material": {"op": "eq", "value": "steel"}, "urgent": None}, "then": {"machine": "a"}},
               {"id": "rush", "when": {"material": {"op": "eq", "value": "alu"}, "urgent": {"op": "eq", "value": True}}, "then": {"machine": "b"}}])
    result = analyze_decision_table(value, UNITS)
    assert {"material": "其他取值", "urgent": "任意"} in result["gaps"]
    assert {"material": "= alu", "urgent": "否"} in result["gaps"]
    assert result["boundary_cases"][0]["input"] == {"facts": {"material": "steel"}, "item": {"urgent": True}}
    defaulted = analyze_decision_table({**value, "default_output": {"machine": "manual"}}, UNITS)
    assert defaulted["gaps"] == [] and defaulted["has_default"] is True


def test_decision_coverage_falls_back_to_rule_regions_when_space_is_large():
    inputs = [{"id": f"c{i}", "source": "facts", "field": f"c{i}", "type": "number"} for i in range(7)]
    rules = [{"id": f"r{j}", "when": {f"c{i}": {"op": "gt", "value": str(j + i)} for i in range(7)}, "then": {"machine": str(j)}} for j in range(3)]
    result = analyze_decision_table(table(inputs=inputs, rules=rules), UNITS)
    assert result["exhaustive"] is False and result["regions_checked"] < result["regions_total"]
    assert result["unreachable_rules"] == []


def test_business_condition_contradictions():
    found = analyze_business({"checks": [{"left": "power", "operator": "gt", "right": {"value": "10"}},
                                         {"left": "power", "operator": "lte", "right": {"value": "5"}},
                                         {"left": "grade", "operator": "eq", "right": {"value": "A"}},
                                         {"left": "grade", "operator": "ne", "right": {"value": "A"}},
                                         {"left": "ok", "operator": "gte", "right": {"value": "1"}},
                                         {"left": "ok", "operator": "lte", "right": {"value": "1"}}]})
    assert [item["input"] for item in found["contradictions"]] == ["power", "grade"]


def test_similarity_reuses_card_terms_and_flags_dimension_conflicts(service):
    cards = service._extraction_v2().cards
    assert dice("额定功率", "额定功率") == 1.0 and dice("", "x") == 0.0
    reuse = similar_predicates(service.registry, {"label": "铭牌功率", "id": "nameplate_power", "value_type": "quantity", "dimension": "power"}, cards)
    assert reuse[0]["id"] == "rated_power" and reuse[0]["verdict"] == "reuse" and reuse[0]["existing_name"]["origin"] == "抽取说明卡其他叫法"
    conflict = similar_predicates(service.registry, {"label": "功率", "value_type": "quantity", "dimension": "energy"}, cards)
    assert conflict[0]["verdict"] == "dimension_conflict"
    assert similar_predicates(service.registry, {"label": "完全无关的叫法xyz"}, cards) == []
    concept = similar_concepts(service.registry, {"label": "供应商", "id": "supplier"}, cards)
    assert concept[0]["id"] == "organization" and concept[0]["verdict"] == "reuse"


def test_context_is_deterministic_and_carries_schema_and_instructions(service):
    authoring = Authoring(service)
    first = authoring.context("logic.draft", {"description": DESCRIPTION})
    second = authoring.context("logic.draft", {"description": DESCRIPTION})
    assert first["instructions"]["content_hash"] == second["instructions"]["content_hash"] and first["context_hash"] == second["context_hash"]
    assert first["write_performed"] is False and first["instructions"]["renderer_version"]
    markdown = first["instructions"]["markdown"]
    assert "只起草，不写入" in markdown and "rated_power" in markdown and "额定功率" in markdown and '"logic.draft"' in markdown
    assert render_instructions("logic.draft", first["input"], first["context"], first["output_schema"]) == markdown
    other = authoring.context("logic.draft", {"description": DESCRIPTION + "补充"})
    assert other["request_id"] != first["request_id"]
    assert "instructions" not in authoring.context("logic.draft", {"description": DESCRIPTION}, include=[])
    with pytest.raises(ValidationError):
        authoring.context("logic.draft", {"description": " "})
    with pytest.raises(ValidationError):
        authoring.context("unknown.task", {})
    with pytest.raises(ValidationError):
        authoring.context("logic.draft", {"description": "x", "kind": "model"})


def test_all_task_contexts_render(service):
    authoring = Authoring(service)
    ontology = authoring.context("ontology.model", {"goal": "建模检验报告", "material": "检验报告记录产品的检验结论。"})
    assert ontology["ontology_request"]["request_id"].startswith("ontology-request:")
    assert ontology["output_schema"]["properties"]["model_output"] == ontology["ontology_request"]["output_schema"]
    card = authoring.context("extraction.card", {"concept": "equipment"})
    assert card["context"]["current_card"]["concept"] == "equipment" and "_path" not in card["context"]["current_card"]
    interview = authoring.context("template.interview", {"conversation": [{"role": "expert", "content": "先内层蚀刻，每层一次，再压合。"}]})
    assert interview["context"]["gaps"] and "当前草稿仍缺少" in interview["instructions"]["markdown"]
    with pytest.raises(ValidationError):
        authoring.context("template.interview", {"conversation": [{"role": "assistant", "content": "你好"}]})


def test_logic_check_reports_analysis_intent_mismatch_and_evidence(service):
    result = Authoring(service).check("logic.draft", draft(evidence=[{"quote": "超过600用大型"}, {"quote": "原文没有这句"}]), {"description": DESCRIPTION})
    codes = [item["code"] for item in result["issues"]]
    assert result["valid"] is True and result["write_performed"] is False
    assert "COVERAGE_GAP" in codes and "INTENT_MISMATCH" in codes and "EVIDENCE_NOT_FOUND" in codes
    intent = {row["id"]: row for row in result["analysis"]["intent_tests"]}
    assert intent["small"]["passed"] is True and intent["large"]["passed"] is False
    assert result["draft_hash"] == digest(result["submitted"])
    invalid = Authoring(service).check("logic.draft", draft(table(hit_policy="UNIQUE")), {"description": DESCRIPTION})
    assert invalid["valid"] is False and invalid["issues"][0]["code"] == "INVALID_DEFINITION" and "overlap" in invalid["issues"][0]["message"]


def test_rule_check_reports_contradictions(service):
    rule = {"format": "knowledgeos.business-constraint.v1", "id": "power_window", "label": "功率窗口", "description": "功率必须大于 10 且不超过 5",
            "version": "1.0.0", "status": {"lifecycle": "active"}, "scope": {"subject_concept": "equipment"},
            "inputs": [{"id": "power", "role": "subject", "predicate": "rated_power"}],
            "checks": [{"left": "power", "operator": "gt", "right": {"value": {"value": "10", "unit": "kW"}}},
                       {"left": "power", "operator": "lte", "right": {"value": {"value": "5", "unit": "kW"}}}]}
    result = Authoring(service).check("logic.draft", {"kind": "business_constraint", "definition": rule, "test_cases": []})
    assert result["valid"] is True
    assert [item["code"] for item in result["issues"]].count("CONTRADICTION") == 1
    assert result["analysis"]["conditions"]["contradictions"][0]["input"] == "power"
    mixed = analyze_business({"checks": [{"left": "p", "operator": "gte", "right": {"value": {"value": "1", "unit": "kW"}}},
                                         {"left": "p", "operator": "lte", "right": {"value": {"value": "1000", "unit": "W"}}}]},
                             service.registry.units)
    assert mixed == {"contradictions": []}


def test_ontology_check_flags_near_duplicate_predicate(service):
    authoring = Authoring(service)
    material = "设备铭牌功率是设备额定输出能力，单位为千瓦。"
    context = authoring.context("ontology.model", {"goal": "补充设备铭牌功率", "material": material})
    request = context["ontology_request"]
    example = next(item for item in service.studio()["data"]["definitions"] if item["id"] == "rated_power")
    after = {**example, "id": "nameplate_power", "label": "铭牌功率", "source_path": "control/predicates/nameplate_power.yaml",
             "sourcePath": "control/predicates/nameplate_power.yaml", "files": ["control/predicates/nameplate_power.yaml"], "lifecycle": "draft"}
    output = {"protocol_version": "knowledgeos.ontology-assist.v1", "request_id": request["request_id"],
              "operations": [{"operation": {"type": "create", "targetKind": "predicate", "targetId": "nameplate_power", "after": after},
                              "evidence": [{"segment_id": "material:1", "quote": "设备铭牌功率"}]}]}
    result = authoring.check("ontology.model", {"ontology_request": request, "model_output": output})
    duplicate = [item for item in result["issues"] if item["code"] == "LIKELY_DUPLICATE"]
    assert duplicate and "rated_power" in duplicate[0]["message"]
    assert result["analysis"]["similar"][0]["matches"][0]["id"] == "rated_power"
    stale = authoring.check("ontology.model", {"ontology_request": {**request, "registry_fingerprint": "old"}, "model_output": output})
    assert stale["valid"] is False and stale["issues"][0]["code"] == "STALE_REQUEST"


def seed_feedback(service, workspace):
    shutil.copytree(REPO / "docs/examples", workspace / "docs/examples")
    spec = importlib.util.spec_from_file_location("seed_extraction_demo", REPO / "scripts/seed_extraction_demo.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    service.close()
    argv = sys.argv
    sys.argv = ["seed", "--root", str(workspace)]
    try:
        assert module.main() == 0
    finally:
        sys.argv = argv


def test_card_check_reports_which_feedback_suggestions_a_draft_resolves(service, workspace):
    seed_feedback(service, workspace)
    from knowledge_os.core import Config
    from knowledge_os.service import Service
    fresh = Service(Config(workspace))
    try:
        authoring = Authoring(fresh)
        context = authoring.context("extraction.card", {"concept": "equipment"})
        unit = next(item for item in context["context"]["suggestions"] if item["kind"] == "unit_alias")
        assert unit["predicate"] == "rated_power" and "千瓦特" in unit["text"]
        card = json.loads(json.dumps(context["context"]["current_card"]))
        card["card_version"] = "1.0.1"
        card["properties"]["rated_power"]["unit_aliases"] = {"千瓦特": "kW"}
        result = authoring.check("extraction.card", {"card": card}, {"concept": "equipment"})
        assert result["valid"] is True and [item["id"] for item in result["analysis"]["resolves"]] == [unit["id"]]
        stale_version = authoring.check("extraction.card", {"card": {**card, "card_version": "1.0.0"}}, {"concept": "equipment"})
        assert stale_version["valid"] is False and stale_version["submitted"] is None
    finally:
        fresh.close()


def test_template_check_lists_gaps_and_previews_cases(service):
    sys.path.insert(0, str(REPO / "pytests"))
    from test_templates import pcb_template
    gaps = Authoring(service).check("template.interview", {"template": {"id": "draft", "steps": [{"id": "etch", "label": "蚀刻"}]}})
    assert gaps["valid"] is False and any(item["path"] == "steps/0/cost_basis" for item in gaps["issues"])
    template = pcb_template()
    template["cases"][0]["expected_operations"] = 2
    result = Authoring(service).check("template.interview", {"template": template})
    assert result["valid"] is True
    rows = {row["case"]: row for row in result["analysis"]["cases"]}
    assert rows["four_layers"]["operation_count"] == 3 and rows["four_layers"]["matches_expected"] is False
    assert any(item["code"] == "CASE_COUNT" for item in result["issues"])
    assert Authoring(service).check("template.interview", {"template": None})["issues"][0]["code"] == "NO_TEMPLATE"


def attestation(output, **overrides):
    value = {"suggestion_id": "sugg-1", "task": "logic.draft", "provider": "anthropic", "model": "claude-sonnet-5-5",
             "renderer_version": "1.0.0", "instructions_hash": "a" * 64, "context_hash": "b" * 64, "output_hash": digest(output),
             "items_total": 4, "items_accepted": 3, "repair_rounds": 0}
    value.update(overrides)
    return value


def test_assistance_declaration_is_validated_and_recorded_with_the_changeset(service):
    from knowledge_os.logic_workbench import LogicWorkbench
    definition = {**table(), "rules": [*table()["rules"], {"id": "large", "when": {"size": {"op": "gt", "value": "600"}}, "then": {"machine": "large"}}],
                  "test_cases": [{"id": "small", "input": {"facts": {"size": "100"}}, "expected": {"result": {"machine": "small"}}}]}
    workbench = LogicWorkbench(service)
    prepared = workbench.prepare("decision_table", definition)
    with pytest.raises(ValidationError):
        workbench.propose(actor="expert:a", kind="decision_table", definition=definition, reason="r", base_revision=service.registry.fingerprint,
                          assistance=attestation(prepared, task="unknown"))
    with pytest.raises(ValidationError):
        assistance_metadata(attestation(prepared, items_accepted=9), prepared)
    with pytest.raises(ValidationError):
        assistance_metadata({**attestation(prepared), "secret": "x"}, prepared)
    created = workbench.propose(actor="expert:a", kind="decision_table", definition=definition, reason="助手起草，专家确认",
                                base_revision=service.registry.fingerprint, idempotency_key="k1", assistance=attestation(prepared))["data"]
    listed = next(item for item in service.changesets()["data"] if item["id"] == created["id"])
    assert listed["proposal_metadata"]["assistance"]["suggestion_id"] == "sugg-1"
    row = service.db.first("SELECT proposal_metadata_json,risk FROM changeset WHERE id=?", (created["id"],))
    recorded = json.loads(row["proposal_metadata_json"])["assistance"]
    assert row["risk"] == "high" and recorded["modified_after_suggestion"] is False and recorded["submitted_hash"] == digest(prepared)
    again = workbench.propose(actor="expert:a", kind="decision_table", definition=definition, reason="助手起草，专家确认",
                              base_revision=service.registry.fingerprint, idempotency_key="k1", assistance=attestation(prepared))["data"]
    assert again["id"] == created["id"]
    edited = {**definition, "version": "1.0.1"}
    workbench.propose(actor="expert:a", kind="decision_table", definition=edited, reason="改过", base_revision=service.registry.fingerprint,
                      assistance=attestation(prepared, suggestion_id="sugg-2"))
    plain = {**definition, "version": "1.0.2"}
    workbench.propose(actor="expert:a", kind="decision_table", definition=plain, reason="手工", base_revision=service.registry.fingerprint)
    feedback = Authoring(service).feedback()
    stats = feedback["tasks"][0]
    assert stats["task"] == "logic.draft" and stats["submitted"] == 2 and stats["modified"] == 1 and stats["modification_rate"] == 0.5
    assert stats["items_total"] == 8 and stats["item_acceptance_rate"] == 0.75 and stats["approval_rate"] is None
    service.review_changeset(id=created["id"], reviewer="expert:reviewer", decision="approved")
    assert Authoring(service).feedback()["tasks"][0]["approval_rate"] == 1.0
    assert service.ledger.verify()["valid"]


def test_extraction_and_template_proposals_accept_assistance(service):
    card = json.loads(json.dumps(Authoring(service).context("extraction.card", {"concept": "equipment"})["context"]["current_card"]))
    card["card_version"] = "1.0.1"
    proposal = service.extraction_v2_asset_propose(kind="card", document=card, reason="助手起草", actor="author:a",
                                                   assistance=attestation(card, task="extraction.card"))["data"]
    metadata = json.loads(service.db.first("SELECT proposal_metadata_json FROM changeset WHERE id=?", (proposal["id"],))["proposal_metadata_json"])
    assert metadata["assistance"]["task"] == "extraction.card" and metadata["assistance"]["modified_after_suggestion"] is False


def request(port, method, path, token, body=None):
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=60)
    headers = {"Authorization": f"Bearer {token}"}
    payload = None
    if body is not None:
        payload = json.dumps(body).encode()
        headers.update({"Content-Type": "application/json", "Content-Length": str(len(payload))})
    connection.request(method, path, payload, headers)
    response = connection.getresponse()
    data = json.loads(response.read() or b"{}")
    connection.close()
    return response.status, data


def test_authoring_api_permissions_and_roundtrip(service):
    auth = AccessControl.from_env({"KNOWLEDGEOS_AUTH_TOKENS": json.dumps({
        "reader": {"principal": "user:reader", "roles": ["reader"]},
        "agent": {"principal": "agent:a", "roles": ["agent"]},
        "expert": {"principal": "expert:e", "roles": ["template_author"]}})})
    server = KnowledgeServer(("127.0.0.1", 0), service, auth)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        port = server.server_address[1]
        status, _ = request(port, "POST", "/v1/authoring/context", "reader", {"task": "logic.draft", "input": {"description": DESCRIPTION}})
        assert status == 403
        status, body = request(port, "GET", "/v1/authoring/tasks", "reader")
        assert status == 200 and {item["id"] for item in body["data"]["tasks"]} >= {"logic.draft", "template.interview"}
        status, body = request(port, "POST", "/v1/authoring/context", "expert", {"task": "logic.draft", "input": {"description": DESCRIPTION}})
        assert status == 200 and re.fullmatch(r"[0-9a-f]{64}", body["data"]["instructions"]["content_hash"])
        status, body = request(port, "POST", "/v1/authoring/check", "agent", {"task": "logic.draft", "draft": draft(), "input": {"description": DESCRIPTION}})
        assert status == 200 and body["data"]["valid"] is True and body["quality_status"]["write_performed"] is False
        status, _ = request(port, "GET", "/v1/authoring/context", "agent")
        assert status == 405
        status, body = request(port, "GET", "/v1/authoring/feedback", "reader")
        assert status == 200 and body["data"]["tasks"] == []
    finally:
        server.shutdown()
        server.server_close()


def test_kernel_never_calls_models():
    """The assistant gateway is the only component allowed to reach a model provider."""
    for path in (REPO / "src/knowledge_os").rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        assert "knowledgeos_assistant" not in text, path
        assert not re.search(r"anthropic|openai|chat/completions|/v1/messages", text, re.IGNORECASE), path


def test_instruction_examples_are_valid_and_pass_their_cases(service):
    """Examples shown to models must themselves pass the deterministic engine."""
    from knowledge_os.authoring import EXAMPLES
    from knowledge_os.logic_workbench import LogicWorkbench, contains_expected
    workbench = LogicWorkbench(service)
    for example in EXAMPLES["logic.draft"]:
        definition = workbench.prepare(example["kind"], example["definition"])
        for case in example["test_cases"]:
            assert contains_expected(workbench.evaluate(example["kind"], definition, case["input"]), case["expected"])
    template = EXAMPLES["template.interview"]["template"]
    from knowledge_os.templates import TemplateWorkbench
    assert TemplateWorkbench(service).preview(template, template["cases"][0])["matches_expected"] is True
