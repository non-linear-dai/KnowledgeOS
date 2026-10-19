"""Read-only instance directory coverage, filters, pagination and API access."""
import json
import threading
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from knowledge_os.api import AccessControl, KnowledgeServer
from knowledge_os.core import NotFoundError, ValidationError


def test_mixed_directory_includes_nodes_edges_attributes_assertions_and_model_runs(service):
    result = service.calculate("cost_rollup", {"material_cost": "10", "labor_hours": "2", "labor_rate": "3", "overhead": "4"})["data"]
    before = service.ledger.verify()
    page = service.instances(limit=200)["data"]
    assert {item["kind"] for item in page["items"]} == {"concept", "relation", "predicate", "model"}
    assert {item["record_kind"] for item in page["items"]} == {"node", "edge", "attribute", "assertion", "model_run"}
    assert len({item["key"] for item in page["items"]}) == page["total"] == page["overall_total"]
    counts = {facet["value"]: facet["count"] for facet in page["facets"]["kind"]}
    assert sum(counts.values()) == page["total"]
    assert counts["concept"] == 5 and counts["model"] == 1
    model = next(item for item in page["items"] if item["kind"] == "model")
    detail = service.instance_get(model["key"])["data"]
    assert detail["data"]["run_id"] == result["run_id"]
    assert detail["data"]["output"]["value"] == "20.0"
    assert detail["data"]["model_snapshot"]["id"] == "cost_rollup"
    assert detail["data"]["trace"]
    assert service.ledger.verify() == before, "browsing must not record or rerun a model"


def test_nonconcept_search_and_detail_preserve_subjects_values_and_provenance(service):
    page = service.instances("portfolio", kind="predicate", type="finding", lifecycle="confirmed")["data"]
    assert page["items"] and all(item["kind"] == "predicate" for item in page["items"])
    assertion = next(item for item in page["items"] if item["subject_id"] == "org:acme")
    detail = service.instance_get(assertion["key"])["data"]
    original = next(item for item in service.history("org:acme")["data"]["assertions"] if item["id"] == assertion["id"])
    assert detail["data"] == original
    assert detail["source_refs"] == original["provenance"]["source_refs"]
    assert detail["evidence_refs"] == original["provenance"]["evidence_refs"]
    assert detail["references"][0]["id"] == "org:acme"
    attributes = service.instances("China", kind="predicate", type="country")["data"]["items"]
    assert attributes[0]["record_kind"] == "attribute"
    assert service.instance_get(attributes[0]["key"])["data"]["data"]["value"] == "China"
    relations = service.instances("Acme", kind="relation", type="supplies")["data"]["items"]
    assert relations and all(item["type"] == "supplies" for item in relations)
    relation = service.instance_get(relations[0]["key"])["data"]
    assert relation["data"]["src"] == "org:acme"
    assert relation["data"]["dst"] == "product:servo-module"
    assert {ref["id"] for ref in relation["references"]} == {"org:acme", "product:servo-module"}
    # Searches include registered labels, not just predicate or relation IDs.
    assert service.instances("Research finding", kind="predicate")["data"]["items"]
    assert not service.instances("Acme", kind="model")["data"]["items"]


def test_mixed_pagination_uses_namespaced_keys_and_finite_temporal_edges(service):
    service.db.write("INSERT INTO edge(src,predicate,dst,valid_from,valid_to,source_path) VALUES(?,?,?,?,?,?)",
                     ("org:acme", "supplies", "product:servo-module", "2099-01-01T00:00:00+08:00", "", "knowledge/entities/org-acme.md"))
    service.db.write("INSERT INTO edge(src,predicate,dst,valid_from,valid_to,source_path) VALUES(?,?,?,?,?,?)",
                     ("org:acme", "supplies", "product:servo-module", "1999-01-01T00:00:00Z", "2000-01-01T00:00:00Z", "knowledge/entities/org-acme.md"))
    relation_page = service.instances(kind="relation", type="supplies")["data"]
    assert len({item["key"] for item in relation_page["items"]}) == 3
    assert {item["lifecycle"] for item in relation_page["items"]} == {"active", "scheduled", "expired"}
    expired = service.instances(kind="relation", lifecycle="expired")["data"]["items"]
    assert len(expired) == 1 and service.instance_get(expired[0]["key"])["data"]["data"]["valid_to"]
    first = service.instances(limit=3)["data"]
    keys = [item["key"] for offset in range(0, first["total"], 3)
            for item in service.instances(limit=3, offset=offset)["data"]["items"]]
    assert len(keys) == len(set(keys)) == first["total"]
    assert service.instances(limit=3)["data"]["items"] == first["items"]


def test_attribute_values_preserve_json_types_and_literal_search(service):
    service.db.write("UPDATE node SET attrs_json=? WHERE id=?", (json.dumps({"finding": False, "country": "100% literal_value", "summary": {"ref": "org:acme", "note": "a:b/[]"}}), "org:acme"))
    values = service.instances(kind="predicate")["data"]["items"]
    attribute = next(item for item in values if item["record_kind"] == "attribute" and item["type"] == "finding")
    assert attribute["value"] is False
    assert service.instance_get(attribute["key"])["data"]["data"]["value"] is False
    literal = service.instances("100% literal_value", kind="predicate")["data"]["items"]
    assert len(literal) == 1 and literal[0]["value"] == "100% literal_value"
    obj = service.instances("a:b/[]", kind="predicate")["data"]["items"][0]
    assert service.instance_get(obj["key"])["data"]["data"]["value"]["note"] == "a:b/[]"


def test_applied_template_instances_remain_hidden_until_publication(service):
    template = {"id": "directory_route", "version": "1.0.0", "label": "Directory route", "family": "general",
                "output_basis": "piece", "summary": "Publication boundary fixture", "groups": [],
                "steps": [{"id": "inspect", "label": "Inspect", "parent": None,
                           "operation_ref": "operation:directory_inspect:1.0.0", "cost_basis": "piece"}],
                "edges": [], "operations": [{"id": "directory_inspect", "version": "1.0.0", "label": "Directory inspect", "cost_basis": "piece"}],
                "cases": [{"id": "basic", "facts": {}, "sets": {}, "expected_operations": 1}]}
    proposal = service.template_propose(actor="test:author", template=template, reason="Directory publication test")["data"]
    service.review_changeset(id=proposal["id"], reviewer="test:reviewer", decision="approved")
    revision = service.apply_changeset(id=proposal["id"], publisher="test:publisher")["data"]["source_revision"]
    service.compile()
    assert service.db.first("SELECT id FROM node WHERE id='route:directory_route:1.0.0'")
    assert service.instances("directory_", limit=200)["data"]["items"] == []
    with pytest.raises(NotFoundError):
        service.instance_get("concept:route:directory_route:1.0.0")
    service.publish_changeset(id=proposal["id"], publisher="test:publisher", source_revision=revision)
    assert service.instances(kind="concept", type="route_template")["data"]["items"]
    assert service.instances(kind="concept", type="operation_template")["data"]["items"]
    assert service.instances(kind="relation", type="route_uses_operation")["data"]["items"]


def test_record_keys_are_unique_when_node_and_assertion_ids_collide(service):
    identifier = service.db.first("SELECT id FROM assertion ORDER BY id LIMIT 1")["id"]
    original = service.db.first("SELECT * FROM node WHERE id='org:acme'")
    row = {**original, "id": identifier, "natural_key": None, "label": "Collision node", "aliases_json": '["specialalias"]'}
    service.db.write(f"INSERT INTO node({','.join(row)}) VALUES({','.join('?' for _ in row)})", list(row.values()))
    matches = [item for item in service.instances(identifier, limit=200)["data"]["items"] if item["id"] == identifier]
    assert len(matches) == 2
    assert len({item["key"] for item in matches}) == 2
    assert service.instances("specialalias", kind="concept")["data"]["items"][0]["id"] == identifier


def test_instance_directory_lists_every_instance_beyond_search_cap(service):
    # Add projection-only fixtures; this endpoint must not create durable state.
    original = service.db.first("SELECT * FROM node ORDER BY id LIMIT 1")
    columns = list(original)
    for index in range(61):
        row = {**original, "id": f"test:directory:{index:03}", "label": f"Directory {index:03}", "natural_key": f"DIRECTORY-{index:03}"}
        service.db.write(f"INSERT INTO node({','.join(columns)}) VALUES({','.join('?' for _ in columns)})",
                         [row[column] for column in columns])
    expected = service.db.first("SELECT count(*) AS n FROM node")["n"]
    ledger_count = service.ledger.connection.execute("SELECT count(*) FROM event").fetchone()[0]
    pages = [service.instances(kind="concept", limit=25, offset=offset)["data"] for offset in range(0, expected, 25)]
    ids = [item["id"] for page in pages for item in page["items"]]
    assert len(ids) == len(set(ids)) == expected
    assert all(page["total"] == expected and page["overall_total"] > expected for page in pages)
    assert sum(facet["count"] for facet in pages[0]["facets"]["type"]) == expected
    assert service.ledger.connection.execute("SELECT count(*) FROM event").fetchone()[0] == ledger_count


def test_instance_directory_searches_identity_and_combines_filters(service):
    result = service.instances("org:acme", type="organization", lifecycle="active", source_class="git_authored")["data"]
    assert [item["id"] for item in result["items"]] == ["org:acme"]
    assert result["total"] == 1
    assert result["overall_total"] > 1
    assert service.instances("acme")["data"]["items"]
    assert not service.instances("org:acme", type="product")["data"]["items"]
    assert not service.instances("' OR 1=1 --")["data"]["items"]
    assert not service.instances("%")["data"]["items"]
    assert not service.instances("\\")["data"]["items"]
    assert service.instances(offset=10000)["data"]["items"] == []
    assert service.instances(sort="updated")["data"]["items"]
    # Literal underscores must not match arbitrary characters.
    service.db.write("UPDATE node SET label=? WHERE id=?", ("literal_value", "org:acme"))
    assert [item["id"] for item in service.instances("literal_value", kind="concept")["data"]["items"]] == ["org:acme"]
    assert not service.instances("literal%value", kind="concept")["data"]["items"]


def test_domain_filters_every_record_kind_before_counts_and_pagination(service):
    service.calculate("cost_rollup", {"material_cost": "10", "labor_hours": "2", "labor_rate": "3", "overhead": "4"})
    before = service.ledger.verify()
    cost = service.instances(domain="cost", limit=200)["data"]
    pm = service.instances(domain="pm", limit=200)["data"]
    assert {item["id"] for item in cost["domains"]} == {"cost", "industry", "pm"}
    assert cost["domain"] == "cost" and cost["total"] == cost["overall_total"]
    assert {item["kind"] for item in cost["items"]} == {"concept", "relation", "predicate", "model"}
    node_types = {row["id"]: row["type"] for row in service.db.execute("SELECT id,type FROM node")}
    for item in cost["items"]:
        if item["kind"] == "concept":
            assert item["type"] in {"product", "organization", "equipment"}
        if item["kind"] in {"predicate", "relation"}:
            assert node_types[item["subject_id"]] in {"product", "organization", "equipment"}
        if item["kind"] == "relation":
            assert node_types[item["target_id"]] in {"product", "organization", "equipment"}
    assert not any(item["kind"] == "model" for item in pm["items"])
    # Shared attributes and the contains relation must not pull project records into cost.
    assert not {item["key"] for item in service.instances(domain="cost", type="summary")["data"]["items"]} & {
        item["key"] for item in service.instances(domain="pm", type="summary")["data"]["items"]}
    expected = [item["key"] for item in cost["items"]]
    pages = [service.instances(domain="cost", limit=2, offset=offset)["data"] for offset in range(0, cost["total"], 2)]
    assert [item["key"] for page in pages for item in page["items"]] == expected
    assert all(page["total"] == cost["total"] and page["facets"] == cost["facets"] for page in pages)
    subset = service.instances("Acme", domain="cost", kind="concept", type="organization", lifecycle="active", source_class="git_authored")["data"]
    assert subset["total"] == 1 and subset["items"][0]["id"] == "org:acme"
    assert sum(facet["count"] for facet in cost["facets"]["kind"]) == cost["total"]
    assert not {"task", "project", "task_status"} & {facet["value"] for facet in cost["facets"]["type"]}
    assert service.ledger.verify() == before


def test_domain_validation_and_empty_scope_are_fail_closed(service):
    for domain in ("missing", "domain:cost", "' OR 1=1 --"):
        with pytest.raises(ValidationError, match="unknown work domain"):
            service.instances(domain=domain)
    service.registry.domains["empty"] = {"id": "empty", "label": "Empty domain"}
    empty = service.instances(domain="empty")["data"]
    assert empty["items"] == [] and empty["total"] == empty["overall_total"] == 0
    assert all(facet["count"] == 0 for facet in empty["facets"]["kind"])


@pytest.mark.parametrize("options", [
    {"limit": 0}, {"limit": 201}, {"limit": "1.5"}, {"limit": True}, {"offset": -1},
    {"offset": "invalid"}, {"sort": "label; DROP TABLE node"},
    {"kind": "unknown"}, {"offset": "9223372036854775808"}, {"offset": "9" * 5000},
])
def test_instance_directory_rejects_invalid_pagination_and_sort(service, options):
    with pytest.raises(ValidationError):
        service.instances(**options)


def test_instance_directory_reader_api_and_validation(service):
    auth = AccessControl.from_env({"KNOWLEDGEOS_AUTH_TOKENS": json.dumps({
        "reader-test": {"principal": "test:reader", "roles": ["reader"]},
    })})
    server = KnowledgeServer(("127.0.0.1", 0), service, auth)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    def get(path, token="reader-test"):
        request = Request(f"http://127.0.0.1:{server.server_port}{path}",
                          headers={"Authorization": f"Bearer {token}"} if token else {})
        try:
            with urlopen(request) as response:
                return response.status, json.load(response)
        except HTTPError as error:
            return error.code, json.load(error)

    try:
        assert get("/v1/instances", token=None)[0] == 401
        status, payload = get("/v1/instances?kind=concept&query=org%3Aacme&limit=1&offset=0")
        assert status == 200
        assert payload["data"]["items"][0]["id"] == "org:acme"
        assert payload["data"]["total"] == 1
        assert get("/v1/instances?limit=201")[0] == 422
        assert get("/v1/instances?offset=-1")[0] == 422
        assert get("/v1/instances?sort=unknown")[0] == 422
        assert get("/v1/instances?kind=unknown")[0] == 422
        assert get("/v1/instances?domain=missing")[0] == 422
        status, cost = get("/v1/instances?domain=cost&kind=concept&limit=1")
        assert status == 200 and cost["data"]["domain"] == "cost"
        assert cost["data"]["items"][0]["type"] in {"organization", "product", "equipment"}
        assert all(item["value"] not in {"project", "task"} for item in cost["data"]["facets"]["type"])
        status, relations = get("/v1/instances?kind=relation")
        assert status == 200 and relations["data"]["items"]
        from urllib.parse import urlencode
        key = relations["data"]["items"][0]["key"]
        status, detail = get("/v1/instances/get?" + urlencode({"key": key}))
        assert status == 200 and detail["data"]["summary"]["kind"] == "relation"
        assert get("/v1/instances/get?" + urlencode({"key": key}), token=None)[0] == 401
        assert get("/v1/instances/get?key=relation:malformed")[0] == 404
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
