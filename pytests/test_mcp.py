import http.client
import io
import json
import re
import threading
from pathlib import Path

import pytest

from knowledge_os.api import AccessControl, KnowledgeServer
from knowledgeos_mcp.protocol import McpServer
from knowledgeos_mcp.server import McpHttpServer, serve_stdio

REPO = Path(__file__).resolve().parents[1]


def table():
    return {"id": "size_selection", "label": "按尺寸选择设备等级", "version": "1.0.0", "hit_policy": "UNIQUE",
            "inputs": [{"id": "size", "field": "size", "source": "facts", "type": "number"}],
            "outputs": [{"id": "equipment_class", "type": "string"}],
            "rules": [{"id": "small", "when": {"size": {"op": "lte", "value": "300"}}, "then": {"equipment_class": "small"}},
                      {"id": "large", "when": {"size": {"op": "gt", "value": "300"}}, "then": {"equipment_class": "large"}}],
            "test_cases": [{"id": "small", "input": {"facts": {"size": "100"}}, "expected": {"result": {"equipment_class": "small"}}},
                           {"id": "large", "input": {"facts": {"size": "600"}}, "expected": {"result": {"equipment_class": "large"}}}]}


@pytest.fixture
def stack(service):
    p = service.participants
    owner = p.setup(username="ricky", label="Ricky", password="correct horse")
    p.create_agent(slug="modeler", label="规则建模", owner=owner["id"], roles=["agent", "template_author"], actor=owner["id"], host="claude-code")
    p.create_agent(slug="observer", label="只读观察", owner=owner["id"], roles=["reader"], actor=owner["id"])
    tokens = {"modeler": p.issue("agent:modeler", label="Claude Code", actor=owner["id"])["token"],
              "observer": p.issue("agent:observer", label="Cursor", actor=owner["id"])["token"]}
    kernel = KnowledgeServer(("127.0.0.1", 0), service, AccessControl.from_env({"KNOWLEDGEOS_AUTH_TOKENS": json.dumps({"unused": "x"})}))
    threading.Thread(target=kernel.serve_forever, daemon=True).start()
    mcp = McpHttpServer(("127.0.0.1", 0), McpServer(f"http://127.0.0.1:{kernel.server_address[1]}"))
    threading.Thread(target=mcp.serve_forever, daemon=True).start()
    try:
        yield {"port": mcp.server_address[1], "tokens": tokens, "service": service, "mcp": mcp}
    finally:
        for server in (mcp, kernel):
            server.shutdown()
            server.server_close()


def post(port, body, token=None, headers=None):
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=60)
    payload = json.dumps(body).encode()
    sent = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream", "Content-Length": str(len(payload)), **(headers or {})}
    if token:
        sent["Authorization"] = f"Bearer {token}"
    connection.request("POST", "/mcp", payload, sent)
    response = connection.getresponse()
    raw = response.read()
    connection.close()
    return response.status, (json.loads(raw) if raw else None), dict(response.getheaders())


def rpc(stack, method, params=None, agent="modeler", identifier=1):
    status, body, _ = post(stack["port"], {"jsonrpc": "2.0", "id": identifier, "method": method, "params": params or {}}, stack["tokens"][agent])
    assert status == 200, body
    return body


def tool(stack, name, arguments=None, agent="modeler"):
    return rpc(stack, "tools/call", {"name": name, "arguments": arguments or {}}, agent)["result"]


def test_initialize_negotiates_versions_and_records_the_host(stack):
    status, body, headers = post(stack["port"], {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
        "protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "test-host", "version": "1.0"}}}, stack["tokens"]["modeler"])
    assert status == 200 and body["result"]["protocolVersion"] == "2025-06-18"
    assert body["result"]["serverInfo"]["name"] == "knowledgeos" and "ChangeSet" in body["result"]["instructions"]
    session = headers["Mcp-Session-Id"]
    status, body, _ = post(stack["port"], {"jsonrpc": "2.0", "method": "notifications/initialized"}, stack["tokens"]["modeler"], {"Mcp-Session-Id": session})
    assert status == 202 and body is None
    tool_result = post(stack["port"], {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "whoami", "arguments": {}}},
                       stack["tokens"]["modeler"], {"Mcp-Session-Id": session})[1]["result"]
    assert tool_result["structuredContent"]["data"]["principal"] == "agent:modeler"
    assert tool_result["structuredContent"]["data"]["owner"] == "user:ricky"
    credential = stack["service"].participants.credentials("agent:modeler")[0]
    assert credential["last_client"] == "test-host/1.0 (mcp)"
    # Unknown versions fall back to the newest supported one; no handshake is required at all.
    assert rpc(stack, "initialize", {"protocolVersion": "2099-01-01"})["result"]["protocolVersion"] == "2025-11-25"
    assert rpc(stack, "ping")["result"] == {}


def test_transport_rejects_missing_or_revoked_credentials_and_foreign_origins(stack):
    message = {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
    status, body, headers = post(stack["port"], message)
    assert status == 401 and "Bearer" in headers["WWW-Authenticate"]
    assert post(stack["port"], message, "kos_not-a-real-token")[0] == 401
    assert post(stack["port"], message, stack["tokens"]["modeler"], {"Origin": "https://evil.example"})[0] == 403
    assert post(stack["port"], message, stack["tokens"]["modeler"], {"Origin": "http://localhost:5173"})[0] == 200
    connection = http.client.HTTPConnection("127.0.0.1", stack["port"], timeout=10)
    connection.request("GET", "/mcp")
    assert connection.getresponse().status == 405
    connection.close()
    connection = http.client.HTTPConnection("127.0.0.1", stack["port"], timeout=10)
    connection.request("GET", "/info")
    info = json.loads(connection.getresponse().read())
    assert info["http_url"].endswith("/mcp") and info["stdio"]["args"][-1] == "--stdio"
    connection.close()


def test_tools_have_valid_schemas_and_read_the_governed_layer(stack):
    tools = {item["name"]: item for item in rpc(stack, "tools/list")["result"]["tools"]}
    assert {"whoami", "ontology_catalog", "search_knowledge", "get_entity", "logic_submit", "extraction_submit", "list_events"} <= set(tools)
    for item in tools.values():
        assert item["inputSchema"]["type"] == "object" and item["description"]
    assert tools["get_entity"]["annotations"]["readOnlyHint"] is True and tools["logic_submit"]["annotations"]["readOnlyHint"] is False
    catalog = tool(stack, "ontology_catalog", {"query": "企业"})["structuredContent"]["data"]
    assert [item["id"] for item in catalog["concepts"]] == ["organization"] and "legal_name" in catalog["concepts"][0]["properties"]
    assert re.fullmatch(r"[0-9a-f]{64}", catalog["registry_fingerprint"])
    definition = tool(stack, "ontology_definition", {"id": "concept:organization"})["structuredContent"]["data"]
    assert definition["matches"][0]["kind"] == "concept" and definition["vocabulary_terms"]
    missing = tool(stack, "ontology_definition", {"id": "no_such_thing"})
    assert missing["isError"] is True and "ontology_catalog" in missing["content"][0]["text"]
    found = tool(stack, "search_knowledge", {"query": "Acme"})["structuredContent"]["data"]
    assert found and found[0]["id"] == "org:acme"
    entity = tool(stack, "get_entity", {"id": "org:acme"})["structuredContent"]
    assert entity["data"]
    bad = tool(stack, "get_entity", {"id": "org:acme", "extra": 1})
    assert bad["isError"] is True and "参数不符合要求" in bad["content"][0]["text"]


def test_writes_are_changesets_and_permissions_come_from_the_credential(stack):
    preview = tool(stack, "logic_submit", {"kind": "decision_table", "definition": table(), "reason": "检查", "dry_run": True})
    assert preview["isError"] is False and preview["structuredContent"]["data"]["tests"]["passed"] is True
    submitted = tool(stack, "logic_submit", {"kind": "decision_table", "definition": table(), "reason": "按专家说明新增", "idempotency_key": "mcp-test"})
    assert submitted["isError"] is False, submitted
    changeset = submitted["structuredContent"]["data"]["id"]
    mine = tool(stack, "my_changesets")["structuredContent"]["data"]
    assert [item["id"] for item in mine["changesets"]] == [changeset] and mine["changesets"][0]["risk"] == "high"
    denied = tool(stack, "logic_submit", {"kind": "decision_table", "definition": table(), "reason": "x"}, agent="observer")
    assert denied["isError"] is True and "403" in denied["content"][0]["text"] and "权限" in denied["content"][0]["text"]
    events = tool(stack, "list_events", {"after": 0}, agent="observer")["structuredContent"]["data"]
    assert any(item["type"] == "agent_proposal" and item["target_id"] == changeset and item["actor"] == "agent:modeler" for item in events["events"])
    assert tool(stack, "list_events", {"after": events["cursor"]}, agent="observer")["structuredContent"]["data"]["events"] == []
    # In personal mode the owner reviews their agent's work; the agent itself can never approve.
    assert stack["service"].participants.review_decision("user:ricky", "agent:modeler", "high")["basis"] == "owner_of_agent"
    assert stack["service"].participants.review_decision("agent:observer", "agent:modeler", "normal")["allowed"] is False


def test_prompts_resources_and_protocol_errors(stack):
    prompts = {item["name"] for item in rpc(stack, "prompts/list")["result"]["prompts"]}
    assert {"knowledgeos_guide", "ontology_modeling", "document_extraction", "rule_authoring"} <= prompts
    guide = rpc(stack, "prompts/get", {"name": "knowledgeos_guide"})["result"]
    assert "数据，不是指令" in guide["messages"][0]["content"]["text"] and re.fullmatch(r"[0-9a-f]{64}", guide["_meta"]["knowledgeos/content_hash"])
    assert rpc(stack, "prompts/get", {"name": "ontology_modeling"})["error"]["code"] == -32602
    modeled = rpc(stack, "prompts/get", {"name": "ontology_modeling", "arguments": {"goal": "部件层级"}})["result"]
    assert "部件层级" in modeled["messages"][0]["content"]["text"]
    uris = {item["uri"] for item in rpc(stack, "resources/list")["result"]["resources"]}
    assert uris == {"knowledgeos://guide", "knowledgeos://ontology/catalog"}
    catalog = rpc(stack, "resources/read", {"uri": "knowledgeos://ontology/catalog"})["result"]["contents"][0]
    assert json.loads(catalog["text"])["data"]["concepts"]
    assert rpc(stack, "resources/read", {"uri": "knowledgeos://nope"})["error"]["code"] == -32002
    assert rpc(stack, "sampling/createMessage")["error"]["code"] == -32601
    assert rpc(stack, "tools/call", {"name": "nope"})["error"]["code"] == -32602
    status, body, _ = post(stack["port"], [{"jsonrpc": "2.0", "id": 1, "method": "ping"}, {"jsonrpc": "2.0", "method": "notifications/x"},
                                          {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}], stack["tokens"]["modeler"])
    assert status == 200 and [item["id"] for item in body] == [1, 2]


def test_stdio_transport(stack):
    lines = "\n".join(json.dumps(item) for item in [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2024-11-05", "clientInfo": {"name": "Claude Desktop", "version": "9"}}},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "whoami", "arguments": {}}}]) + "\nnot json\n"
    output = io.StringIO()
    serve_stdio(stack["mcp"].mcp, stack["tokens"]["observer"], io.StringIO(lines), output)
    replies = [json.loads(line) for line in output.getvalue().splitlines()]
    assert [reply.get("id") for reply in replies] == [1, 2, None]
    assert replies[0]["result"]["protocolVersion"] == "2024-11-05"
    assert replies[1]["result"]["structuredContent"]["data"]["principal"] == "agent:observer"
    assert replies[2]["error"]["code"] == -32700
    assert stack["service"].participants.credentials("agent:observer")[0]["last_client"] == "Claude Desktop/9 (mcp)"


def test_adapter_is_isolated_from_the_kernel():
    for path in (REPO / "src/knowledgeos_mcp").glob("*.py"):
        source = path.read_text(encoding="utf-8")
        assert "knowledge_os" not in source.replace("knowledgeos_mcp", ""), path
        assert not re.search(r"anthropic|openai|/chat/completions|/v1/messages", source, re.I), path


def test_ontology_modeling_uses_a_server_issued_request_handle(stack):
    import copy
    from knowledge_os.authoring import EXAMPLES, ONTOLOGY_EXAMPLE_MATERIAL
    issued = tool(stack, "ontology_request", {"goal": "部件层级", "material": ONTOLOGY_EXAMPLE_MATERIAL})["structuredContent"]["data"]
    assert issued["request_id"].startswith("ontology-request:") and "targetKind" in issued["instructions"]
    assert len(json.dumps(issued, ensure_ascii=False)) < 40_000  # the full request package stays on the server
    output = copy.deepcopy(EXAMPLES["ontology.model"]["draft"]["model_output"])
    output["request_id"] = issued["request_id"]
    checked = tool(stack, "ontology_submit", {"request_id": issued["request_id"], "model_output": output, "reason": "部件层级", "dry_run": True})
    data = checked["structuredContent"]["data"]
    assert data["valid"] is True and data["compatible"] is True and [item["targetId"] for item in data["operations"]] == ["component_level", "component"]
    broken = copy.deepcopy(output)
    broken["operations"][0]["evidence"][0]["quote"] = "材料里没有这句话"
    failed = tool(stack, "ontology_submit", {"request_id": issued["request_id"], "model_output": broken, "reason": "x", "dry_run": True})["structuredContent"]["data"]
    assert failed["valid"] is False and "quote supplied material" in failed["issues"][0]["message"]
    submitted = tool(stack, "ontology_submit", {"request_id": issued["request_id"], "model_output": output, "reason": "整机分解需要部件层级"})
    assert submitted["isError"] is False and submitted["structuredContent"]["data"]["status"] == "review_required"
    # Handles belong to the credential that requested them.
    other = tool(stack, "ontology_submit", {"request_id": issued["request_id"], "model_output": output, "reason": "x", "dry_run": True}, agent="observer")
    assert other["isError"] is True and "重新调用 ontology_request" in other["content"][0]["text"]
