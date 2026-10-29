import http.client
import json
import re
import threading
from pathlib import Path

import pytest

from knowledge_os.api import AccessControl, KnowledgeServer
from knowledgeos_assistant.config import ConfigurationError, GatewayConfig
from knowledgeos_assistant.gateway import Assistant, AssistantServer, digest, extract_json
from knowledgeos_assistant.providers import DemoProvider, ProviderError

REPO = Path(__file__).resolve().parents[1]
TOKENS = {"author": {"principal": "expert:author", "roles": ["agent", "template_author"]},
          "reviewer": {"principal": "expert:reviewer", "roles": ["reviewer", "template_reviewer"]},
          "reader": {"principal": "user:reader", "roles": ["reader"]}}
DESCRIPTION = "零件尺寸不超过300mm用小型注塑机，超过300用大型。"


def call(port, method, path, token=None, body=None):
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=120)
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    payload = None
    if body is not None:
        payload = json.dumps(body).encode()
        headers.update({"Content-Type": "application/json", "Content-Length": str(len(payload))})
    connection.request(method, path, payload, headers)
    response = connection.getresponse()
    data = json.loads(response.read() or b"{}")
    connection.close()
    return response.status, data


@pytest.fixture
def kernel(service):
    server = KnowledgeServer(("127.0.0.1", 0), service, AccessControl.from_env({"KNOWLEDGEOS_AUTH_TOKENS": json.dumps(TOKENS)}))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()


def gateway(assistant):
    server = AssistantServer(("127.0.0.1", 0), assistant)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


class Scripted:
    """A provider that replays fixed replies and records what it was sent."""
    name = "scripted"

    def __init__(self, replies):
        self.replies, self.calls = list(replies), []

    def complete(self, system, messages, packet=None):
        self.calls.append({"system": system, "messages": [dict(item) for item in messages]})
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply if isinstance(reply, str) else json.dumps(reply, ensure_ascii=False), {"input_tokens": 10, "output_tokens": 5}


def logic_reply(**overrides):
    value = {"protocol_version": "knowledgeos.authoring.v1", "task": "logic.draft", "summary": "按尺寸两档", "kind": "decision_table",
             "definition": {"id": "size_two", "version": "1.0.0", "label": "尺寸两档", "description": "按尺寸选机", "hit_policy": "UNIQUE",
                            "inputs": [{"id": "size", "source": "facts", "field": "size", "type": "number"}],
                            "outputs": [{"id": "machine", "type": "string"}],
                            "rules": [{"id": "small", "when": {"size": {"op": "lte", "value": "300"}}, "then": {"machine": "small"}},
                                      {"id": "large", "when": {"size": {"op": "gt", "value": "300"}}, "then": {"machine": "large"}}]},
             "test_cases": [{"id": "at_300", "label": "300 用小型", "input": {"facts": {"size": "300"}}, "expected": {"result": {"machine": "small"}}}],
             "evidence": [{"quote": "超过300用大型"}]}
    value.update(overrides)
    return value


def config(kernel_url, **overrides):
    values = {"kernel_url": kernel_url, "provider": "anthropic", "model": "test-model", "api_key": "k", "base_url": "http://unused"}
    values.update(overrides)
    return GatewayConfig(**values)


def test_config_from_env_validates_and_never_exposes_the_key():
    value = GatewayConfig.from_env({"KNOWLEDGEOS_ASSISTANT_PROVIDER": "anthropic", "KNOWLEDGEOS_ASSISTANT_API_KEY": "secret"})
    assert value.configured and value.model and value.base_url == "https://api.anthropic.com"
    assert "secret" not in json.dumps(value.describe()) and "secret" not in repr(value)
    assert GatewayConfig.from_env({}).configured is False
    assert GatewayConfig.from_env({"KNOWLEDGEOS_ASSISTANT_PROVIDER": "openai_compatible", "KNOWLEDGEOS_ASSISTANT_API_KEY": "x"}).configured is False
    with pytest.raises(ConfigurationError):
        GatewayConfig.from_env({"KNOWLEDGEOS_ASSISTANT_PROVIDER": "other"})
    with pytest.raises(ConfigurationError):
        GatewayConfig.from_env({"KNOWLEDGEOS_ASSISTANT_MAX_REPAIRS": "9"})


def test_extract_json_tolerates_fences_and_prose():
    assert extract_json('```json\n{"a": "}"}\n```') == {"a": "}"}
    assert extract_json('说明：{"a": {"b": [1, "\\"x"]}} 完毕') == {"a": {"b": [1, '"x']}}
    with pytest.raises(ValueError):
        extract_json("没有 JSON")


def test_suggest_repairs_invalid_output_and_attests_the_checked_draft(service, kernel):
    overlapping = logic_reply()
    overlapping["definition"]["rules"][1]["when"]["size"] = {"op": "gte", "value": "300"}
    provider = Scripted(["这不是 JSON", overlapping, logic_reply()])
    assistant = Assistant(config(kernel), provider=provider)
    result = assistant.suggest("Bearer author", "logic.draft", {"description": DESCRIPTION})
    assert result["write_performed"] is False and result["assistance"]["repair_rounds"] == 2
    assert [item["errors"] for item in result["attempts"]] == [1, 1, 0]
    assert provider.calls[0]["system"].startswith("# KnowledgeOS 编写助手说明") and DESCRIPTION in provider.calls[0]["messages"][0]["content"]
    assert "NOT_JSON" in provider.calls[1]["messages"][-1]["content"] and "overlap" in provider.calls[2]["messages"][-1]["content"]
    assert result["check"]["valid"] is True and result["assistance"]["output_hash"] == result["check"]["draft_hash"]
    assert re.fullmatch(r"[0-9a-f]{64}", result["assistance"]["instructions_hash"]) and result["usage"] == {"input_tokens": 30, "output_tokens": 15}
    cases = [case for case in result["check"]["analysis"]["coverage"]["boundary_cases"] if case["input"]["facts"]["size"] == "300"]
    definition = {**result["check"]["submitted"], "test_cases": [{"id": cases[0]["id"], "label": cases[0]["label"], "input": cases[0]["input"], "expected": cases[0]["actual"]}]}
    port = int(kernel.rsplit(":", 1)[1])
    status, catalog = call(port, "GET", "/v1/logic", "author")
    status, created = call(port, "POST", "/v1/logic/propose", "author", {"kind": "decision_table", "definition": definition, "reason": "助手起草，专家确认边界",
                                                                         "base_revision": catalog["data"]["registry_fingerprint"],
                                                                         "assistance": {**result["assistance"], "items_accepted": 1}})
    assert status == 200, created
    metadata = json.loads(service.db.first("SELECT proposal_metadata_json FROM changeset WHERE id=?", (created["data"]["id"],))["proposal_metadata_json"])
    assert metadata["assistance"]["modified_after_suggestion"] is True  # confirmed regression cases were added by the expert
    assert metadata["assistance"]["model"] == "test-model"


def test_suggest_gives_up_after_bounded_repairs_and_relays_provider_errors(kernel):
    assistant = Assistant(config(kernel, max_repairs=1), provider=Scripted(["x", "y"]))
    with pytest.raises(Exception) as caught:
        assistant.suggest("Bearer author", "logic.draft", {"description": DESCRIPTION})
    assert getattr(caught.value, "code", None) == "INVALID_MODEL_OUTPUT"
    failing = Assistant(config(kernel), provider=Scripted([ProviderError("模型服务返回 HTTP 429", 429)]))
    with pytest.raises(Exception) as caught:
        failing.suggest("Bearer author", "logic.draft", {"description": DESCRIPTION})
    assert caught.value.status == 429


def test_http_gateway_status_auth_permissions_and_rate_limit(kernel):
    unconfigured = gateway(Assistant(GatewayConfig(kernel_url=kernel)))
    configured = gateway(Assistant(config(kernel, requests_per_minute=1), provider=Scripted([logic_reply(), logic_reply()])))
    try:
        port = unconfigured.server_address[1]
        status, body = call(port, "GET", "/v1/assistant/status")
        assert status == 200 and body["data"]["configured"] is False
        status, body = call(port, "POST", "/v1/assistant/suggest", "author", {"task": "logic.draft", "input": {"description": DESCRIPTION}})
        assert status == 503 and body["code"] == "ASSISTANT_NOT_CONFIGURED"
        port = configured.server_address[1]
        assert call(port, "POST", "/v1/assistant/suggest", None, {"task": "logic.draft", "input": {}})[0] == 401
        status, body = call(port, "POST", "/v1/assistant/suggest", "reader", {"task": "logic.draft", "input": {"description": DESCRIPTION}})
        assert status == 403 and "KnowledgeOS" in body["error"]
        status, body = call(port, "POST", "/v1/assistant/suggest", "author", {"task": "logic.draft", "input": {"description": DESCRIPTION}})
        assert status == 200 and body["data"]["check"]["valid"] is True
        status, body = call(port, "POST", "/v1/assistant/suggest", "author", {"task": "logic.draft", "input": {"description": DESCRIPTION}})
        assert status == 429 and body["code"] == "RATE_LIMITED"
        status, body = call(port, "POST", "/v1/assistant/suggest", "reviewer", {"task": "nope", "input": {}})
        assert status == 422
    finally:
        for server in (unconfigured, configured):
            server.shutdown()
            server.server_close()


@pytest.mark.parametrize("task,task_input", [
    ("logic.draft", {"description": DESCRIPTION}),
    ("logic.draft", {"description": "设备额定功率不低于 5 kW", "kind": "business_constraint"}),
    ("ontology.model", {"goal": "建模检验报告", "material": "检验报告记录产品的检验结论和检验日期。"}),
    ("extraction.card", {"concept": "equipment"}),
    ("template.interview", {"conversation": [{"role": "expert", "content": "多层板先做内层蚀刻，每层一次，然后压合。"}]}),
    ("template.interview", {"conversation": [{"role": "expert", "content": "多层板先做内层蚀刻，每层一次，然后压合。"},
                                              {"role": "assistant", "content": "按什么计价？"}, {"role": "expert", "content": "按板，4 层板共 3 道工序。"}]}),
])
def test_demo_provider_produces_checked_suggestions_for_every_task(kernel, task, task_input):
    assistant = Assistant(GatewayConfig(kernel_url=kernel, provider="demo", model="demo-fixed-examples"))
    result = assistant.suggest("Bearer author", task, task_input)
    assert result["demo"] is True and result["output"]["summary"].startswith("【演示模式")
    assert result["check"]["valid"] is True, result["check"]["issues"]
    assert result["assistance"]["provider"] == "demo"
    if task == "ontology.model":
        assert result["ontology_request"]["request_id"] == result["output"]["model_output"]["request_id"]


def test_demo_refinement_keeps_current_table(kernel):
    definition = logic_reply()["definition"]
    result = Assistant(GatewayConfig(kernel_url=kernel, provider="demo")).suggest(
        "Bearer author", "logic.draft", {"description": "补充案例", "current": {"kind": "decision_table", "definition": definition}})
    assert result["output"]["definition"] == definition and result["check"]["analysis"]["intent_tests"][0]["passed"] is True


def test_document_samples_can_be_withheld(kernel):
    provider = Scripted([ProviderError("stop", 502)])
    assistant = Assistant(config(kernel, send_document_samples=False), provider=provider)
    with pytest.raises(Exception):
        assistant.suggest("Bearer author", "extraction.card", {"concept": "equipment"})
    assert "原文样例" not in provider.calls[0]["system"]


def test_gateway_digest_matches_kernel_digest():
    from knowledge_os.core import digest as kernel_digest
    value = {"b": [1, "二", {"z": None, "a": True}], "a": "x"}
    assert digest(value) == kernel_digest(value)


def test_gateway_is_isolated_from_kernel_internals():
    for path in (REPO / "src/knowledgeos_assistant").rglob("*.py"):
        assert not re.search(r"^\s*(from|import)\s+knowledge_os", path.read_text(encoding="utf-8"), re.MULTILINE), path


def test_demo_provider_rejects_unknown_tasks():
    with pytest.raises(ProviderError):
        DemoProvider(None).complete("", [], {"task": "x"})


def test_provider_wire_formats_against_local_stub_endpoints():
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from knowledgeos_assistant.providers import AnthropicProvider, OpenAICompatibleProvider
    seen = []

    class Stub(BaseHTTPRequestHandler):
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            seen.append({"path": self.path, "headers": {key.lower(): value for key, value in self.headers.items()}, "body": body})
            if self.path == "/v1/messages":
                reply = {"content": [{"type": "text", "text": '{"ok": 1}'}], "stop_reason": "end_turn", "usage": {"input_tokens": 3, "output_tokens": 2}}
            elif body["model"] == "truncate":
                reply = {"choices": [{"message": {"content": "{"}, "finish_reason": "length"}]}
            else:
                reply = {"choices": [{"message": {"content": '{"ok": 2}'}, "finish_reason": "stop"}], "usage": {"prompt_tokens": 4, "completion_tokens": 1}}
            data = json.dumps(reply).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *args):
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Stub)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        text, usage = AnthropicProvider(GatewayConfig(kernel_url="", provider="anthropic", model="m", api_key="key-a", base_url=base)).complete("系统", [{"role": "user", "content": "问"}])
        assert text == '{"ok": 1}' and usage == {"input_tokens": 3, "output_tokens": 2}
        assert seen[0]["headers"]["x-api-key"] == "key-a" and seen[0]["headers"]["anthropic-version"] and seen[0]["body"]["system"] == "系统"
        compatible = GatewayConfig(kernel_url="", provider="openai_compatible", model="m", api_key="key-b", base_url=base)
        text, usage = OpenAICompatibleProvider(compatible).complete("系统", [{"role": "user", "content": "问"}])
        assert text == '{"ok": 2}' and seen[1]["path"] == "/chat/completions" and seen[1]["headers"]["authorization"] == "Bearer key-b"
        assert seen[1]["body"]["messages"][0] == {"role": "system", "content": "系统"} and seen[1]["body"]["response_format"] == {"type": "json_object"}
        compatible.model = "truncate"
        with pytest.raises(ProviderError, match="截断"):
            OpenAICompatibleProvider(compatible).complete("s", [])
    finally:
        server.shutdown()
        server.server_close()
