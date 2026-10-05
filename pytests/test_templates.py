import http.client
import json
import threading

import pytest
import yaml

from knowledge_os.api import AccessControl, KnowledgeServer
from knowledge_os.core import ConflictError, NotFoundError, ValidationError
from knowledge_os.templates import TemplateWorkbench


def pcb_template():
    return {
        "id": "rigid_multilayer", "version": "1.0.0", "label": "Rigid multilayer PCB",
        "family": "pcb", "output_basis": "board", "summary": "Expert standard route",
        "groups": [{"id": "inner_circuit", "label": "Inner circuit", "iteration_set": "inner_surfaces", "join_policy": "all"}],
        "steps": [
            {"id": "inner_etch", "label": "Inner etch", "parent": "inner_circuit",
             "operation_ref": "operation:etch:1.0.0", "cost_basis": "panel"},
            {"id": "lamination", "label": "Lamination", "parent": None,
             "operation_ref": "operation:laminate:1.0.0", "cost_basis": "press_load"},
        ],
        "edges": [{"from": "inner_circuit", "to": "lamination"}],
        "operations": [
            {"id": "etch", "version": "1.0.0", "label": "Etch", "cost_basis": "panel"},
            {"id": "laminate", "version": "1.0.0", "label": "Lamination", "cost_basis": "press_load"},
        ],
        "cases": [
            {"id": "four_layers", "facts": {}, "sets": {"inner_surfaces": [{"id": "L2"}, {"id": "L3"}]}, "expected_operations": 3},
            {"id": "eight_layers", "facts": {}, "sets": {"inner_surfaces": [{"id": f"L{i}"} for i in range(2, 8)]}, "expected_operations": 7},
        ],
    }


def machining_template():
    return {
        "id": "machined_part", "version": "1.0.0", "label": "Machined part",
        "family": "machining", "output_basis": "part", "summary": "Generic feature route",
        "groups": [{"id": "features", "label": "Features", "iteration_set": "surfaces", "join_policy": "all"}],
        "steps": [
            {"id": "mill", "label": "Milling", "parent": "features", "operation_ref": "operation:mill:1.0.0",
             "cost_basis": "surface", "when": {"source": "item", "field": "requires_milling", "op": "eq", "value": True}},
            {"id": "inspection", "label": "Inspection", "parent": None, "operation_ref": "operation:inspect:1.0.0",
             "cost_basis": "part"},
        ],
        "edges": [{"from": "features", "to": "inspection"}],
        "operations": [
            {"id": "mill", "version": "1.0.0", "label": "Milling", "cost_basis": "surface"},
            {"id": "inspect", "version": "1.0.0", "label": "Inspection", "cost_basis": "part"},
        ],
        "cases": [{"id": "sample", "facts": {}, "sets": {"surfaces": [
            {"id": "face_a", "requires_milling": True}, {"id": "face_b", "requires_milling": False},
            {"id": "face_c", "requires_milling": True}]}, "expected_operations": 3}],
    }


def test_generic_route_expansion_and_join(service):
    workbench = TemplateWorkbench(service)
    pcb = pcb_template()
    four = workbench.preview(pcb, pcb["cases"][0])
    eight = workbench.preview(pcb, pcb["cases"][1])
    assert four["operation_counts"] == {"operation:etch:1.0.0": 2, "operation:laminate:1.0.0": 1}
    assert eight["operation_count"] == 7
    assert {edge["from"] for edge in four["edges"] if edge["to"] == "lamination"} == {
        "inner_circuit/L2/inner_etch", "inner_circuit/L3/inner_etch"}
    machining = machining_template()
    result = workbench.preview(machining, machining["cases"][0])
    assert result["operation_counts"] == {"operation:inspect:1.0.0": 1, "operation:mill:1.0.0": 2}
    assert all(instance["item_id"] != "face_b" for instance in result["instances"])


def test_template_publish_compiler_and_ledger(service, workspace):
    template = pcb_template()
    proposal = service.template_propose(actor="expert:pcb", template=template, reason="Expert-approved route", idempotency_key="pcb-route-v1")["data"]
    assert proposal["status"] == "review_required"
    assert service.db.first("SELECT risk FROM changeset WHERE id=?", (proposal["id"],))["risk"] == "high"
    with pytest.raises(ValidationError, match="independent reviewer"):
        service.review_changeset(id=proposal["id"], reviewer="expert:pcb", decision="approved")
    service.review_changeset(id=proposal["id"], reviewer="reviewer:routes", decision="approved")
    revision = service.apply_changeset(id=proposal["id"], publisher="publisher:routes")["data"]["source_revision"]
    assert service.template_catalog()["data"]["templates"] == []
    assert service.template_catalog()["data"]["operations"] == []
    with pytest.raises(NotFoundError, match="not published"):
        service.template_get("route:rigid_multilayer:1.0.0")
    published = service.publish_changeset(id=proposal["id"], publisher="publisher:routes", source_revision=revision)["data"]
    assert published["status"] == "published"
    assert [item["id"] for item in service.template_catalog()["data"]["templates"]] == ["route:rigid_multilayer:1.0.0"]
    assert service.template_propose(actor="expert:pcb", template=template, reason="Expert-approved route",
                                    idempotency_key="pcb-route-v1")["data"]["id"] == proposal["id"]
    assert service.template_get("route:rigid_multilayer:1.0.0")["data"]["cases"] == template["cases"]
    assert service.db.first("SELECT type FROM node WHERE id='route-step:rigid_multilayer:1.0.0:lamination'")["type"] == "route_step"
    assert service.db.first("SELECT type FROM node WHERE id='operation:etch:1.0.0'")["type"] == "operation_template"
    assert service.ledger.verify()["valid"]
    with pytest.raises(ConflictError, match="already exists"):
        service.template_propose(actor="expert:pcb", template=template, reason="Overwrite")
    route_file = workspace / "knowledge/templates/rigid_multilayer/v1.0.0/groups/inner_circuit.md"
    raw = route_file.read_text()
    route_file.write_text(raw.replace("route-step:rigid_multilayer:1.0.0:inner_etch", "route-group:rigid_multilayer:1.0.0:inner_circuit"))
    with pytest.raises(ValidationError):
        service.compile()
    assert service.db.first("SELECT type FROM node WHERE id='route:rigid_multilayer:1.0.0'")["type"] == "route_template"


def test_template_validation_rejects_invalid_graph_and_case(service):
    workbench = TemplateWorkbench(service)
    template = pcb_template()
    template["edges"].append({"from": "lamination", "to": "inner_circuit"})
    with pytest.raises(ValidationError, match="cycle"):
        workbench.validate(template)
    template = pcb_template()
    template["cases"][0]["expected_operations"] = 2
    with pytest.raises(ValidationError, match="sample cases failed"):
        service.template_propose(actor="expert:pcb", template=template, reason="Bad sample")
    template = pcb_template()
    template["groups"][0]["iteration_set"] = "missing_set"
    with pytest.raises(ValidationError, match="missing iteration set"):
        workbench.preview(template, template["cases"][0])
    template = pcb_template()
    template["operations"][0]["resources"] = [{"kind": "energy", "amount": "2", "unit": "kWh"}]
    workbench.validate(template)
    template["operations"][0]["resources"][0]["unit"] = "imaginary_unit"
    with pytest.raises(ValidationError, match="registered unit"):
        workbench.validate(template)
    template = pcb_template()
    template["operations"][0]["time_model_ref"] = "cost_rollup@1.0.0"
    with pytest.raises(ValidationError, match="must output duration"):
        workbench.validate(template)
    assert all(model["id"] != "cost_rollup" for model in service.template_catalog()["data"]["models"])
    service.registry.models["machining_time"] = {"id": "machining_time", "version": "1.0.0", "output_unit": "hour"}
    template["operations"][0]["time_model_ref"] = "machining_time@1.0.0"
    workbench.validate(template)
    assert "machining_time" in {model["id"] for model in service.template_catalog()["data"]["models"]}


def test_template_api_permissions_are_separate_from_ontology(service):
    auth = AccessControl.from_env({"KNOWLEDGEOS_AUTH_TOKENS": json.dumps({
        "author": {"principal": "expert:author", "roles": ["template_author"]},
        "reviewer": {"principal": "expert:reviewer", "roles": ["template_reviewer"]},
        "publisher": {"principal": "expert:publisher", "roles": ["template_publisher"]},
        "ontology": {"principal": "ontology:editor", "roles": ["agent", "reviewer", "publisher"]},
    })})
    server = KnowledgeServer(("127.0.0.1", 0), service, auth)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    def request(path, token, body=None):
        connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=5)
        method = "POST" if body is not None else "GET"
        connection.request(method, path, body=json.dumps(body) if body is not None else None,
                           headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"})
        response = connection.getresponse()
        output = response.status, json.loads(response.read())
        connection.close()
        return output

    try:
        template = pcb_template()
        assert request("/v1/templates", "author")[0] == 200
        assert request("/v1/studio", "author")[0] == 200
        assert request("/v1/templates/preview", "ontology", {"template": template, "case": template["cases"][0]})[0] == 403
        assert request("/v1/templates/preview", "author", {"template": template, "case": template["cases"][0]})[0] == 200
        scenario = {"quantity": "10", "currency": "CNY", "rates": {}}
        assert request("/v1/templates/calculate", "ontology", {"template": template, "case": template["cases"][0], "scenario": scenario})[0] == 403
        assert request("/v1/templates/calculate", "author", {"template": template, "case": template["cases"][0], "scenario": scenario})[1]["data"]["status"] == "incomplete"
        assert request("/v1/templates/calculate", "reviewer", {"template": template, "case": template["cases"][0], "scenario": scenario})[0] == 200
        assert request("/v1/templates/calculate", "author")[0] == 405
        assert request("/v1/propose", "ontology", {"target_source": "knowledge/decisions/example/v1.0.0.md", "patch": {"op": "add", "path": "", "value": {}}, "reason": "bypass"})[0] == 422
        assert request("/v1/templates/preview", "reviewer", {"template": template, "case": template["cases"][0]})[0] == 200
        assert request("/v1/templates/propose", "ontology", {"template": template, "reason": "route"})[0] == 403
        status, payload = request("/v1/templates/propose", "author", {"template": template, "reason": "route"})
        assert status == 201
        proposal_id = payload["data"]["id"]
        assert request("/v1/changesets/review", "ontology", {"id": proposal_id, "decision": "approved"})[0] == 403
        assert request("/v1/propose", "ontology", {"target_source": "knowledge/templates/rigid_multilayer/v1.0.0/template.md",
            "patch": {"op": "add", "path": "", "value": {}}, "reason": "bypass"})[0] == 422
        assert request("/v1/changesets/review", "reviewer", {"id": proposal_id, "decision": "approved"})[0] == 200
        assert request("/v1/changesets/apply", "ontology", {"id": proposal_id})[0] == 403
        applied = request("/v1/changesets/apply", "publisher", {"id": proposal_id})
        assert applied[0] == 200
        revision = applied[1]["data"]["source_revision"]
        assert request("/v1/changesets/publish", "publisher", {"id": proposal_id, "source_revision": revision})[0] == 200
        assert request("/v1/propose", "author", {"target_source": "control/ontology/core.yaml",
            "patch": {"op": "replace", "path": "/concept_types/0/label", "value": "Changed"}, "reason": "bypass"})[0] == 403
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_staged_authoring_roundtrip_preserves_once_repeat_and_complete_joins(service):
    """The text authoring UI emits ordinary one-level group semantics."""
    template = pcb_template()
    template['groups'] = [
        {'id': 'preparation', 'label': 'Preparation', 'group_mode': 'conditional',
         'iteration_set': 'items_preparation', 'execution_mode': 'parallel', 'join_policy': 'all'},
        {**template['groups'][0], 'group_mode': 'repeat', 'execution_mode': 'parallel'},
        {'id': 'board', 'label': 'Board', 'group_mode': 'conditional',
         'iteration_set': 'items_board', 'execution_mode': 'parallel', 'join_policy': 'all'},
    ]
    template['steps'][1]['parent'] = 'board'
    template['steps'].extend([
        {'id': 'wash', 'label': 'Wash', 'parent': 'preparation',
         'operation_ref': 'operation:wash:1.0.0', 'cost_basis': 'panel'},
        {'id': 'inspect', 'label': 'Inspect', 'parent': 'board',
         'operation_ref': 'operation:inspect:1.0.0', 'cost_basis': 'board'},
    ])
    template['operations'].extend([
        {'id': 'wash', 'version': '1.0.0', 'label': 'Wash', 'cost_basis': 'panel'},
        {'id': 'inspect', 'version': '1.0.0', 'label': 'Inspect', 'cost_basis': 'board'},
    ])
    template['edges'] = [{'from': 'preparation', 'to': 'inner_circuit'},
                         {'from': 'inner_circuit', 'to': 'board'},
                         {'from': 'lamination', 'to': 'inspect'}]
    for case in template['cases']:
        case['expected_operations'] += 2
        preview = service.template_preview(template, case)['data']
        assert preview['matches_expected']
        assert preview['operation_counts']['operation:wash:1.0.0'] == 1
        assert preview['operation_counts']['operation:laminate:1.0.0'] == 1
        assert {e['from'] for e in preview['edges'] if e['to'] == 'board/lamination'} == {
            f"inner_circuit/{item['id']}/inner_etch" for item in case['sets']['inner_surfaces']}
        assert preview['instances'][0]['id'] == 'preparation/wash'
        assert preview['instances'][-1]['id'] == 'board/inspect'
    proposal = service.template_propose(actor='expert:stages', template=template, reason='Confirmed stages')['data']
    service.review_changeset(id=proposal['id'], reviewer='reviewer:stages', decision='approved')
    revision = service.apply_changeset(id=proposal['id'], publisher='publisher:stages')['data']['source_revision']
    service.publish_changeset(id=proposal['id'], publisher='publisher:stages', source_revision=revision)
    restored = service.template_get('route:rigid_multilayer:1.0.0')['data']
    assert {g['id']: g['group_mode'] for g in restored['groups']} == {
        'preparation': 'conditional', 'inner_circuit': 'repeat', 'board': 'conditional'}
    assert all(service.template_preview(restored, case)['data']['matches_expected'] for case in restored['cases'])
    assert service.ledger.verify()['valid']
