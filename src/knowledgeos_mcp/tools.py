"""MCP tools: thin, documented wrappers over the public KnowledgeOS API.

Reads return KnowledgeOS envelopes (data + source/temporal/quality status,
conflicts and gaps). Writes only ever create ChangeSets or dry runs; review,
approval and publication stay with people in Studio.
"""
from __future__ import annotations

import hashlib
import threading
import time
from dataclasses import dataclass, field
from typing import Callable

from .client import KnowledgeOSClient, KnowledgeOSError

TASKS = ["logic.draft", "ontology.model", "extraction.card", "template.interview"]
LOGIC_KINDS = ["decision_table", "business_rule", "business_constraint", "model"]


@dataclass
class Tool:
    name: str
    title: str
    description: str
    input_schema: dict
    handler: Callable[[KnowledgeOSClient, dict], dict]
    read_only: bool = True
    idempotent: bool = True
    annotations: dict = field(default_factory=dict)

    def describe(self):
        return {"name": self.name, "title": self.title, "description": self.description, "inputSchema": self.input_schema,
                "outputSchema": {"type": "object"},
                "annotations": {"title": self.title, "readOnlyHint": self.read_only, "destructiveHint": False,
                                "idempotentHint": self.idempotent, "openWorldHint": False}}


def schema(properties=None, required=()):
    return {"type": "object", "properties": properties or {}, "required": list(required), "additionalProperties": False}


STRING = {"type": "string", "minLength": 1, "maxLength": 2000}
AS_OF = {"type": "string", "description": "业务时点（ISO 8601），省略为当前"}


def trimmed(envelope):
    """Keep KnowledgeOS status fields that carry information; drop empty ones."""
    if not isinstance(envelope, dict) or "data" not in envelope:
        return {"data": envelope}
    return {key: value for key, value in envelope.items() if key == "data" or value not in ({}, [], None)}


def _labels(vocabularies):
    labels = {}
    for vocabulary in (vocabularies or {}).values():
        for term in vocabulary.get("terms", []):
            ref = term.get("concept_ref")
            if ref:
                names = [term.get("pref_labels", {}).get("zh")] + list(term.get("alt_labels", {}).get("zh", []))
                labels.setdefault(ref, []).extend(name for name in names if name)
    return labels


def ontology_catalog(client, args):
    data = client.get("/v1/semantics")["data"]
    zh = _labels(data.get("vocabularies"))
    query = str(args.get("query") or "").strip().lower()

    def keep(*texts):
        return not query or any(query in str(text or "").lower() for text in texts)

    concepts = [{"id": item["id"], "label": item.get("label"), "zh": zh.get(item["id"], []), "description": item.get("description", ""),
                 "properties": [prop["predicate"] for prop in item.get("properties", [])]}
                for item in data.get("concepts", []) if keep(item["id"], item.get("label"), item.get("description"), *zh.get(item["id"], []))]
    relations = [{"id": item["id"], "label": item.get("label"), "description": item.get("description", ""),
                  "connections": [f"{edge['source_type']} → {edge['target_type']}" for edge in item.get("connections", [])]}
                 for item in data.get("relations", []) if keep(item["id"], item.get("label"), item.get("description"))]
    predicates = [{"id": key, "label": item.get("label"), "type": item.get("value", {}).get("type"),
                   "cardinality": item.get("value", {}).get("cardinality"), "unit": item.get("value", {}).get("unit") or item.get("value", {}).get("dimension"),
                   "description": item.get("semantics", {}).get("description", "")}
                  for key, item in sorted((data.get("predicates") or {}).items()) if keep(key, item.get("label"), item.get("semantics", {}).get("description"))]
    return {"data": {"registry_fingerprint": data.get("registry_fingerprint"), "concepts": concepts, "relations": relations, "predicates": predicates},
            "note": "注册目录摘要。完整定义用 ontology_definition；新增概念/属性/关系用 ontology_request → ontology_submit。"}


def ontology_definition(client, args):
    data = client.get("/v1/semantics")["data"]
    identifier = args["id"].split(":", 1)[-1]
    found = []
    for kind, items in (("concept", data.get("concepts", [])), ("relation", data.get("relations", []))):
        found += [{"kind": kind, "definition": item} for item in items if item["id"] == identifier]
    if identifier in (data.get("predicates") or {}):
        found.append({"kind": "predicate", "definition": data["predicates"][identifier]})
    terms = [term for vocabulary in (data.get("vocabularies") or {}).values() for term in vocabulary.get("terms", [])
             if term.get("concept_ref") == identifier or term.get("id") == identifier]
    if not found:
        raise LookupError(f"未找到定义：{args['id']}（先用 ontology_catalog 查询）")
    return {"data": {"matches": found, "vocabulary_terms": terms, "registry_fingerprint": data.get("registry_fingerprint")}}


def whoami(client, args):
    session = client.get("/v1/session")
    return {"data": {key: session.get(key) for key in ("principal", "label", "kind", "owner", "roles", "permissions", "policy")}}


def my_changesets(client, args):
    me = client.get("/v1/session")["principal"]
    items = client.get("/v1/changesets", status=args.get("status"), limit=500)["data"]
    mine = [{key: item.get(key) for key in ("id", "title", "status", "risk", "target_source", "reason", "created_at", "reviewed_by", "review_note", "published_at")}
            for item in items if item.get("actor") == me][: int(args.get("limit") or 50)]
    return {"data": {"principal": me, "changesets": mine}}


def logic_submit(client, args):
    body = {"kind": args["kind"], "definition": args["definition"]}
    if args.get("dry_run"):
        return trimmed(client.post("/v1/logic/preview", body))
    base = args.get("base_revision")
    if not base:
        base = client.get("/v1/logic")["data"]["registry_fingerprint"]
    result = client.post("/v1/logic/propose", {**body, "reason": args["reason"], "base_revision": base,
                                               **({"idempotency_key": args["idempotency_key"]} if args.get("idempotency_key") else {}),
                                               **({"assistance": args["assistance"]} if args.get("assistance") else {})})
    return {**trimmed(result), "note": "已提交 ChangeSet，等待人工审核；用 my_changesets 查看状态。"}


class RequestStore:
    """Server-issued handles: the full modeling request stays here, the model only carries its id."""

    def __init__(self, ttl=6 * 3600, limit=500):
        self.ttl, self.limit, self.items, self.lock = ttl, limit, {}, threading.Lock()

    @staticmethod
    def key(client, request_id):
        return hashlib.sha256(client.token.encode()).hexdigest() + ":" + request_id

    def put(self, client, request_id, value):
        with self.lock:
            now = time.monotonic()
            self.items = {key: item for key, item in self.items.items() if item[0] > now}
            while len(self.items) >= self.limit:
                self.items.pop(min(self.items, key=lambda key: self.items[key][0]))
            self.items[self.key(client, request_id)] = (now + self.ttl, value)

    def get(self, client, request_id):
        with self.lock:
            item = self.items.get(self.key(client, request_id))
        if not item or item[0] <= time.monotonic():
            raise LookupError(f"建模请求 {request_id} 不存在或已过期（6 小时），请重新调用 ontology_request。")
        return item[1]


REQUESTS = RequestStore()


def ontology_request(client, args):
    context = client.post("/v1/authoring/context", {"task": "ontology.model", "input": {"goal": args["goal"], "material": args["material"]},
                                                    "include": ["instructions"]})["data"]
    request = context["ontology_request"]
    REQUESTS.put(client, request["request_id"], {"request": request, "input": {"goal": args["goal"], "material": args["material"]}})
    return {"data": {"request_id": request["request_id"], "registry_fingerprint": context["registry_fingerprint"], "segments": request["segments"],
                     "instructions": context["instructions"]["markdown"], "instructions_hash": context["instructions"]["content_hash"]},
            "note": "按 instructions 起草；把输出中的 model_output（其 request_id 必须等于上面的 request_id）连同 request_id 传给 ontology_submit，先 dry_run=true。"}


def ontology_submit(client, args):
    stored = REQUESTS.get(client, args["request_id"])
    output = args["model_output"]
    if output.get("request_id") != args["request_id"]:
        raise LookupError("model_output.request_id 必须等于 ontology_request 返回的 request_id")
    body = {"request": stored["request"], "model_output": output}
    if args.get("dry_run"):
        draft = {"protocol_version": "knowledgeos.authoring.v1", "task": "ontology.model", "summary": args.get("summary") or args["reason"],
                 "model_output": output, "ontology_request": stored["request"]}
        check = client.post("/v1/authoring/check", {"task": "ontology.model", "draft": draft, "input": stored["input"]})["data"]
        impact = check["analysis"].get("impact") or {}
        return {"data": {"valid": check["valid"], "issues": check["issues"], "similar": check["analysis"].get("similar", []),
                         "operations": [{"type": item.get("type"), "targetKind": item.get("targetKind"), "targetId": item.get("targetId")}
                                        for item in check["analysis"].get("operations", [])],
                         "compatible": impact.get("compatible")},
                "note": "修正 issues 中的 error，确认 warning（例如与已有定义近似）后再以 dry_run=false 提交。"}
    result = client.post("/v1/ontology/propose", {**body, "reason": args["reason"],
                                                  **({"idempotency_key": args["idempotency_key"]} if args.get("idempotency_key") else {})})
    return {**trimmed(result), "note": "已提交 ChangeSet，等待人工审核。"}


def extraction_submit(client, args):
    message = dict(args["submission"])
    message["mode"] = "precheck" if args.get("dry_run", True) else "propose"
    path = "/v2/extraction/precheck" if message["mode"] == "precheck" else "/v2/extraction/submissions"
    return trimmed(client.post(path, message))


TOOLS = [
    Tool("whoami", "当前身份", "返回当前凭证代表的参与者（人或 Agent）、负责人、角色与权限，以及审批策略。开始工作前先调用。", schema(), whoami),
    Tool("ontology_catalog", "本体目录", "查询已注册的概念、关系与属性（谓词）摘要，含中文叫法和 registry_fingerprint。写任何内容前先确认要用的概念与属性已注册；不要自造谓词。",
         schema({"query": {**STRING, "description": "按标识、名称、中文叫法或说明过滤，省略返回全部"}}), ontology_catalog),
    Tool("ontology_definition", "本体定义", "返回一个概念、关系或属性的完整定义与业务术语。",
         schema({"id": {**STRING, "description": "例如 organization、supplies、lead_time_days"}}, ["id"]), ontology_definition),
    Tool("search_knowledge", "检索知识", "按关键词与语义检索权威知识层中的实体。",
         schema({"query": STRING, "type": {**STRING, "description": "概念标识，例如 organization"},
                 "mode": {"enum": ["hybrid", "keyword", "vector"], "default": "hybrid"}}, ["query"]),
         lambda c, a: trimmed(c.get("/v1/search", query=a["query"], type=a.get("type"), mode=a.get("mode")))),
    Tool("get_entity", "实体卡片", "读取实体的已审核事实（含来源、时点、权威等级）；include_history 返回断言历史。",
         schema({"id": STRING, "include_history": {"type": "boolean", "default": False}}, ["id"]),
         lambda c, a: trimmed(c.get("/v1/get", id=a["id"], history="true" if a.get("include_history") else None))),
    Tool("get_neighbors", "关系遍历", "沿关系遍历相邻实体，可按关系类型、深度与时点过滤。",
         schema({"id": STRING, "relation_types": {**STRING, "description": "逗号分隔的关系标识"},
                 "depth": {"type": "integer", "minimum": 1, "maximum": 3, "default": 1}, "as_of": AS_OF}, ["id"]),
         lambda c, a: trimmed(c.get("/v1/neighbors", id=a["id"], relation_types=a.get("relation_types"), depth=a.get("depth"), as_of=a.get("as_of")))),
    Tool("get_context", "上下文包", "按领域为一个实体组装可直接引用的上下文包（事实、关系、缺口与冲突）。",
         schema({"id": STRING, "domain": {**STRING, "description": "领域标识，例如 procurement"}, "as_of": AS_OF}, ["id", "domain"]),
         lambda c, a: trimmed(c.get("/v1/context", id=a["id"], domain=a["domain"], as_of=a.get("as_of")))),
    Tool("explain", "来源解释", "解释一条事实或实体字段的来源、权威与推导过程。",
         schema({"target": STRING, "item": STRING}, ["target"]),
         lambda c, a: trimmed(c.get("/v1/explain", target=a["target"], item=a.get("item")))),
    Tool("calculate", "确定性计算", "用已注册的确定性模型计算（数值与单位换算一律交给模型，不要自己心算）。",
         schema({"model_id": STRING, "inputs": {"type": "object"}, "scenario": {**STRING, "default": "default"}}, ["model_id", "inputs"]),
         lambda c, a: trimmed(c.post("/v1/calculate", {"model_id": a["model_id"], "inputs": a["inputs"], "scenario": a.get("scenario", "default")}))),
    Tool("authoring_context", "起草说明", "获取某类起草任务的标准说明、可用定义目录、示例与输出 JSON Schema（带内容哈希）。起草规则、本体或抽取说明卡前调用。",
         schema({"task": {"enum": TASKS}, "input": {"type": "object", "description": "任务输入，例如 {\"description\": \"业务说明\"}"}}, ["task"]),
         lambda c, a: trimmed(c.post("/v1/authoring/context", {"task": a["task"], "input": a.get("input") or {}, "include": ["instructions"]}))),
    Tool("authoring_check", "起草试运行", "对草稿做确定性试运行：结构校验、决策表覆盖缺口、矛盾条件、与已有属性/概念的近似重复等。不写入。",
         schema({"task": {"enum": TASKS}, "draft": {"type": "object"}, "input": {"type": "object"}}, ["task", "draft"]),
         lambda c, a: trimmed(c.post("/v1/authoring/check", {"task": a["task"], "draft": a["draft"], **({"input": a["input"]} if a.get("input") else {})}))),
    Tool("ontology_request", "本体建模请求", "用建模目标和材料原文生成建模请求：返回 request_id 与中文起草说明（可用定义、写法示例、输出格式）。按说明起草后调用 ontology_submit。",
         schema({"goal": STRING, "material": {"type": "string", "minLength": 1, "maxLength": 200000}}, ["goal", "material"]),
         ontology_request, read_only=True, idempotent=False),
    Tool("ontology_submit", "提交本体变更", "传入 ontology_request 的 request_id 与起草的 model_output。dry_run=true 时做确定性检查（含与已有定义近似的提示）和隔离编译预览，不写入；dry_run=false 提交 ChangeSet 等待人工审核。",
         schema({"request_id": STRING, "model_output": {"type": "object"}, "reason": STRING, "summary": STRING,
                 "dry_run": {"type": "boolean", "default": False}, "idempotency_key": STRING}, ["request_id", "model_output", "reason"]),
         ontology_submit, read_only=False, idempotent=True),
    Tool("logic_submit", "提交业务逻辑", "提交决策表、业务规则、业务约束或计算模型。dry_run=true 只运行案例预览；否则提交 ChangeSet（须含通过的回归案例）。base_revision 用目录中的 registry_fingerprint，省略时取当前值。",
         schema({"kind": {"enum": LOGIC_KINDS}, "definition": {"type": "object"}, "reason": STRING, "dry_run": {"type": "boolean", "default": False},
                 "base_revision": STRING, "idempotency_key": STRING, "assistance": {"type": "object"}}, ["kind", "definition", "reason"]),
         logic_submit, read_only=False),
    Tool("extraction_contract", "抽取契约", "按 knowledgeos.extraction.v2 协议获取抽取契约（概念、属性写法、验收规则）。message 为 contract_request 消息。",
         schema({"message": {"type": "object"}}, ["message"]), lambda c, a: trimmed(c.post("/v2/extraction/contract", a["message"]))),
    Tool("extraction_register_source", "登记来源文档", "登记 source_document（块结构、原件哈希）；返回 source_receipt 与版本差异。",
         schema({"message": {"type": "object"}}, ["message"]), lambda c, a: trimmed(c.post("/v2/extraction/sources", a["message"])), read_only=False),
    Tool("extraction_submit", "提交抽取结果", "提交 extraction_submission。默认 dry_run=true 只验收不写入；验收通过后用 dry_run=false 生成 ChangeSet。",
         schema({"submission": {"type": "object"}, "dry_run": {"type": "boolean", "default": True}}, ["submission"]), extraction_submit, read_only=False),
    Tool("my_changesets", "我的提案", "列出当前参与者提交的 ChangeSet 及审核状态与意见。",
         schema({"status": {"enum": ["proposed", "review_required", "approved", "rejected", "changes_requested", "published"]},
                 "limit": {"type": "integer", "minimum": 1, "maximum": 200, "default": 50}}), my_changesets),
    Tool("list_events", "增量事件", "按游标读取提案、审核、发布等治理事件（从 0 开始，之后传回上次的 cursor）。用于了解其他参与者的进展。",
         schema({"after": {"type": "integer", "minimum": 0, "default": 0}, "limit": {"type": "integer", "minimum": 1, "maximum": 500, "default": 100}}),
         lambda c, a: trimmed(c.get("/v1/events", after=a.get("after", 0), limit=a.get("limit", 100)))),
]
BY_NAME = {tool.name: tool for tool in TOOLS}
