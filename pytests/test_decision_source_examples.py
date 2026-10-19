"""Verify the two authored teaching assets against compilation, governance and HTTP."""
import http.client
import json
import threading
from pathlib import Path

import pytest

from knowledge_os.api import AccessControl, KnowledgeServer
from knowledge_os.core import ConflictError, ValidationError, frontmatter, load_yaml
from knowledge_os.logic_workbench import LogicWorkbench, asset_ref, contains_expected


ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def source_examples(service):
    document, _ = frontmatter(ROOT / "knowledge/decisions/demo_size_equipment/v1.0.0.md")
    table = document["knowledge"]["attrs"]["decision_definition"]
    model = load_yaml(ROOT / "control/decision-models/demo_size_duration_energy/v1.0.0.yaml")
    for kind, definition in (("decision_table", table), ("decision_model", model)):
        workbench = LogicWorkbench(service)
        assert workbench.preview(kind, definition)["tests"]["passed"]
        proposal = workbench.propose(actor="teaching:author", kind=kind, definition=definition,
                                     reason="Verify authored teaching assets", base_revision=service.registry.fingerprint)["data"]
        with pytest.raises(ValidationError, match="independent reviewer"):
            service.review_changeset(id=proposal["id"], reviewer="teaching:author", decision="approved")
        service.review_changeset(id=proposal["id"], reviewer="teaching:reviewer", decision="approved")
        revision = service.apply_changeset(id=proposal["id"], publisher="teaching:publisher")["data"]["source_revision"]
        service.publish_changeset(id=proposal["id"], publisher="teaching:publisher", source_revision=revision)
    return table, model


def test_source_examples_compile_link_and_preserve_all_seven_expert_cases(service, source_examples):
    table, model = source_examples
    service.compile(rebuild=True)
    workbench = LogicWorkbench(service)
    catalog = {asset["ref"]: asset for asset in workbench.catalog()["assets"]}
    assert len(table["test_cases"]) == 3 and len(model["test_cases"]) == 4
    assert any(use["id"] == asset_ref("decision_model", model) for use in catalog[asset_ref("decision_table", table)]["used_by"])
    for kind, definition in (("decision_table", table), ("decision_model", model)):
        for case in definition["test_cases"]:
            result = workbench.preview(kind, definition, case["input"])
            assert contains_expected(result["result"], case["expected"])
        with pytest.raises(ConflictError, match="immutable"):
            workbench.propose(actor="teaching:author", kind=kind, definition=definition,
                              reason="Cannot overwrite a published example", base_revision=service.registry.fingerprint)
    assert service.ledger.verify()["valid"]


def test_source_decision_chain_http_preview_run_permissions_and_snapshot_replay(service, source_examples):
    _, model = source_examples
    auth = AccessControl.from_env({"KNOWLEDGEOS_AUTH_TOKENS": json.dumps({
        "reader": {"principal": "teaching:reader", "roles": ["reader"]},
        "agent": {"principal": "teaching:operator", "roles": ["agent"]},
    })})
    server = KnowledgeServer(("127.0.0.1", 0), service, auth)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    def call(method, path, body=None, token="reader"):
        connection = http.client.HTTPConnection("127.0.0.1", server.server_port)
        connection.request(method, path, None if body is None else json.dumps(body),
                           {"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
        response = connection.getresponse()
        result = json.loads(response.read())
        connection.close()
        return response.status, result

    try:
        case = next(case for case in model["test_cases"] if case["id"] == "large_part")
        status, result = call("POST", "/v1/logic/preview", {"kind": "decision_model", "definition": model, "facts": case["input"]})
        assert status == 200 and contains_expected(result["data"]["result"], case["expected"])
        assert service.db.first("SELECT count(*) AS n FROM durable.logic_run")["n"] == 0
        payload = {"ref": asset_ref("decision_model", model), "facts": case["input"], "idempotency_key": "source-example-large"}
        assert call("POST", "/v1/logic/run", payload)[0] == 403
        status, result = call("POST", "/v1/logic/run", payload, "agent")
        assert status == 200 and contains_expected(result["data"]["record"]["result"], case["expected"])
        status, replay = call("POST", "/v1/logic/replay", {"id": result["data"]["id"]})
        assert status == 200 and replay["data"]["matches_recorded"]
        assert service.ledger.verify()["valid"]
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
