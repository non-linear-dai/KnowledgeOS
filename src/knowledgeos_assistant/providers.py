"""Model providers for the assistant gateway.

Each provider turns (system instructions, chat messages) into text. The
gateway parses and validates that text; providers know nothing about
KnowledgeOS. Network access uses only the standard library.
"""
from __future__ import annotations

import hashlib
import json
import urllib.error
import urllib.request


class ProviderError(RuntimeError):
    def __init__(self, message, status=502):
        super().__init__(message)
        self.status = status


def _post(url, headers, body, timeout):
    data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(url, data=data, method="POST", headers={"Content-Type": "application/json", **headers})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            payload = json.loads(exc.read().decode("utf-8"))
            detail = (payload.get("error") or {}).get("message") if isinstance(payload.get("error"), dict) else str(payload.get("error") or "")
        except (ValueError, AttributeError):
            pass
        status = 429 if exc.code == 429 else 502
        raise ProviderError(f"模型服务返回 HTTP {exc.code}{('：' + detail[:300]) if detail else ''}", status) from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise ProviderError(f"无法连接模型服务：{getattr(exc, 'reason', exc)}") from exc


class AnthropicProvider:
    name = "anthropic"

    def __init__(self, config):
        self.config = config

    def complete(self, system, messages, packet=None):
        body = {"model": self.config.model, "max_tokens": self.config.max_tokens, "system": system,
                "messages": [{"role": item["role"], "content": item["content"]} for item in messages]}
        result = _post(f"{self.config.base_url}/v1/messages",
                       {"x-api-key": self.config.api_key, "anthropic-version": "2023-06-01"}, body, self.config.timeout)
        text = "".join(block.get("text", "") for block in result.get("content", []) if block.get("type") == "text")
        if result.get("stop_reason") == "max_tokens":
            raise ProviderError("模型输出被截断：请提高 KNOWLEDGEOS_ASSISTANT_MAX_TOKENS 或缩小任务范围")
        usage = result.get("usage") or {}
        return text, {"input_tokens": usage.get("input_tokens"), "output_tokens": usage.get("output_tokens")}


class OpenAICompatibleProvider:
    """Any endpoint that implements the chat completions API (self-hosted or regional providers)."""
    name = "openai_compatible"

    def __init__(self, config):
        self.config = config

    def complete(self, system, messages, packet=None):
        body = {"model": self.config.model, "max_tokens": self.config.max_tokens,
                "messages": [{"role": "system", "content": system}, *messages]}
        if self.config.json_mode:
            body["response_format"] = {"type": "json_object"}
        result = _post(f"{self.config.base_url}/chat/completions", {"Authorization": f"Bearer {self.config.api_key}"}, body, self.config.timeout)
        choices = result.get("choices") or []
        if not choices:
            raise ProviderError("模型服务没有返回内容")
        if choices[0].get("finish_reason") == "length":
            raise ProviderError("模型输出被截断：请提高 KNOWLEDGEOS_ASSISTANT_MAX_TOKENS 或缩小任务范围")
        usage = result.get("usage") or {}
        return choices[0].get("message", {}).get("content") or "", {"input_tokens": usage.get("prompt_tokens"), "output_tokens": usage.get("completion_tokens")}


def _short(text):
    return hashlib.sha256(str(text).encode("utf-8")).hexdigest()[:6]


class DemoProvider:
    """Fixed, clearly labelled examples for UI walkthroughs without a model key. Never use for real authoring."""
    name = "demo"

    def __init__(self, config):
        self.config = config

    def complete(self, system, messages, packet=None):
        task = (packet or {}).get("task")
        builder = {"logic.draft": self._logic, "ontology.model": self._ontology,
                   "extraction.card": self._card, "template.interview": self._template}.get(task)
        if not builder:
            raise ProviderError("演示模式不支持该任务", 400)
        output = {"protocol_version": "knowledgeos.authoring.v1", "task": task, **builder(packet)}
        output["summary"] = "【演示模式：固定样例，未调用模型】" + output["summary"]
        return json.dumps(output, ensure_ascii=False), {"input_tokens": 0, "output_tokens": 0}

    @staticmethod
    def _logic(packet):
        task_input = packet["input"]
        current = task_input.get("current")
        if current and current["kind"] == "decision_table" and current["definition"].get("rules"):
            definition = current["definition"]
            rule = definition["rules"][0]
            facts, item = {}, {}
            for column in definition.get("inputs", []):
                cell = (rule.get("when") or {}).get(column["id"])
                value = {"number": "0", "boolean": True}.get(column.get("type"), "x")
                if cell:
                    raw = cell["value"][0] if isinstance(cell["value"], list) else cell["value"]
                    if cell["op"] in ("lt", "gt"):
                        from decimal import Decimal
                        raw = str(Decimal(str(raw)) + (Decimal(-1) if cell["op"] == "lt" else Decimal(1)))
                    value = raw if cell["op"] != "ne" else value
                (item if column.get("source") == "item" else facts)[column.get("field", column["id"])] = value
            case_input = {"facts": facts, **({"item": item} if item else {})}
            return {"kind": "decision_table", "definition": definition, "summary": "保留当前决策表，按第一条规则补充一个意图案例供专家确认。",
                    "test_cases": [{"id": "demo_first_rule", "label": f"演示：规则 {rule['id']} 的典型输入", "input": case_input, "expected": {"result": rule["then"]}}],
                    "open_questions": ["演示模式无法理解说明内容，请配置模型后重试。"]}
        if task_input.get("kind") in ("business_rule", "business_constraint"):
            kind = task_input["kind"]
            definition = {"format": f"knowledgeos.{kind.replace('_', '-')}.v1", "id": f"demo_power_{_short(task_input['description'])}", "version": "1.0.0",
                          "label": "演示：功率要求", "description": "演示用：设备额定功率不低于 5 kW", "status": {"lifecycle": "active"},
                          "scope": {"subject_concept": "equipment", **({"candidate_concept": "equipment", "context_concepts": {}} if kind == "business_rule" else {})},
                          "inputs": [{"id": "power", "role": "subject" if kind == "business_constraint" else "candidate", "predicate": "rated_power"}],
                          "checks": [{"left": "power", "operator": "gte", "right": {"value": {"value": "5", "unit": "kW"}}, "message": "额定功率不低于 5 kW"}]}
            facts = {"rated_power": {"literal": "7.5", "unit": "kW"}}
            case_input = {"subject": facts} if kind == "business_constraint" else {"subject": {}, "candidates": [{"id": "eq_demo", **facts}]}
            expected = {"status": "complete"} if kind == "business_constraint" else {"eligible_candidate_ids": ["eq_demo"]}
            return {"kind": kind, "definition": definition, "summary": "演示：按额定功率下限判断。",
                    "test_cases": [{"id": "enough", "label": "7.5 kW 满足 5 kW 下限", "input": case_input, "expected": expected}]}
        return {"kind": "decision_table", "summary": "按零件尺寸分为小、中、大三档设备，边界值 300 与 600 归入较小一档。",
                "definition": {"id": f"demo_size_class_{_short(task_input['description'])}", "version": "1.0.0", "label": "演示：按尺寸选择设备等级",
                               "description": "尺寸不超过 300 用小型，不超过 600 用中型，其余用大型", "hit_policy": "FIRST",
                               "inputs": [{"id": "size", "label": "零件尺寸", "source": "facts", "field": "size", "type": "number"}],
                               "outputs": [{"id": "equipment_class", "label": "设备等级", "type": "string"}],
                               "rules": [{"id": "small", "when": {"size": {"op": "lte", "value": "300"}}, "then": {"equipment_class": "small"}},
                                         {"id": "medium", "when": {"size": {"op": "lte", "value": "600"}}, "then": {"equipment_class": "medium"}},
                                         {"id": "large", "when": {"size": {"op": "gt", "value": "600"}}, "then": {"equipment_class": "large"}}]},
                "assumptions": ["边界值 300、600 归入较小一档。"],
                "test_cases": [{"id": "size_300", "label": "300 属于小型", "input": {"facts": {"size": "300"}}, "expected": {"result": {"equipment_class": "small"}}},
                               {"id": "size_450", "label": "450 属于中型", "input": {"facts": {"size": "450"}}, "expected": {"result": {"equipment_class": "medium"}}},
                               {"id": "size_900", "label": "900 属于大型", "input": {"facts": {"size": "900"}}, "expected": {"result": {"equipment_class": "large"}}}]}

    @staticmethod
    def _ontology(packet):
        material = packet["input"]["material"]
        quote = material[:min(len(material), 30)]
        identifier = f"demo_concept_{_short(material)}"
        label = packet["input"]["goal"].replace("建模", "").strip("：: ") or "演示概念"
        after = {"id": identifier, "kind": "concept", "label": label, "description": material[:200], "lifecycle": "draft",
                 "sourcePath": "control/ontology/core.yaml", "config": {}, "refs": 0, "files": ["control/ontology/core.yaml"],
                 "bindings": [{"predicateId": "summary", "required": False, "cardinality": "inherit", "group": "profile"}]}
        return {"summary": f"新建概念「{label}」，复用已有的摘要属性。",
                "model_output": {"protocol_version": "knowledgeos.ontology-assist.v1", "request_id": packet["ontology_request"]["request_id"],
                                 "operations": [{"operation": {"type": "create", "targetKind": "concept", "targetId": identifier, "after": after},
                                                 "evidence": [{"segment_id": "material:1", "quote": quote}]}]},
                "notes": [{"target_id": identifier, "explanation": "演示模式：按材料首句建立概念。", "reused": ["summary"]}]}

    @staticmethod
    def _card(packet):
        context = packet["context"]
        concept = context["concept"]["id"]
        card = json.loads(json.dumps(context.get("current_card") or {"format": "knowledgeos.extraction-card.v1", "concept": concept, "card_version": "0.0.0",
                                                                     "card": {"definition": {"zh": context["concept"].get("description") or concept}}}))
        major, minor, patch = (int(part) for part in card["card_version"].split("."))
        card["card_version"] = f"{major}.{minor}.{patch + 1}" if (major, minor, patch) != (0, 0, 0) else "1.0.0"
        changes = []
        for item in context.get("suggestions", []):
            choices = (item.get("action") or {}).get("choices") or []
            if item["kind"] == "unit_alias" and item.get("predicate") and choices:
                entry = card.setdefault("properties", {}).setdefault(item["predicate"], {})
                entry.setdefault("unit_aliases", {})[item["text"]] = choices[0]
                changes.append({"suggestion_id": item["id"], "field": f"properties.{item['predicate']}.unit_aliases", "description": f"「{item['text']}」视为 {choices[0]}（演示：取第一个允许单位，请专家核对）"})
        if not changes:
            card.setdefault("card", {}).setdefault("boundary_notes", []).append("演示：此条为固定样例。")
            changes.append({"field": "card.boundary_notes", "description": "演示：加入一条边界说明"})
        return {"summary": f"处理 {len(changes)} 项说明卡修改。", "card": card, "changes": changes}

    @staticmethod
    def _template(packet):
        turns = [turn for turn in packet["input"]["conversation"] if turn["role"] == "expert"]
        if len(turns) < 2 and not packet["input"].get("current"):
            return {"summary": "先确认工艺骨架。", "reply": "我理解这是一条多层板路线：内层逐层加工后统一压合。",
                    "questions": [{"id": "basis", "question": "内层蚀刻按什么计价？", "options": ["按板（panel）", "按件（piece）"]},
                                  {"id": "repeat", "question": "是否每个内层都要做一次蚀刻？", "options": ["是", "否，按条件"]},
                                  {"id": "sample", "question": "4 层板的样例中，总共应展开几道工序？"}],
                    "template": None, "changes": []}
        suffix = _short(json.dumps(turns, ensure_ascii=False))
        template = {"id": f"demo_multilayer_{suffix}", "version": "1.0.0", "label": "演示：多层刚性板", "family": "pcb", "output_basis": "board",
                    "summary": "演示模式生成的固定路线",
                    "groups": [{"id": "inner_circuit", "label": "内层线路", "iteration_set": "inner_surfaces", "join_policy": "all"}],
                    "steps": [{"id": "inner_etch", "label": "内层蚀刻", "parent": "inner_circuit", "operation_ref": f"operation:demo_etch_{suffix}:1.0.0", "cost_basis": "panel"},
                              {"id": "lamination", "label": "压合", "parent": None, "operation_ref": f"operation:demo_laminate_{suffix}:1.0.0", "cost_basis": "press_load"}],
                    "edges": [{"from": "inner_circuit", "to": "lamination"}],
                    "operations": [{"id": f"demo_etch_{suffix}", "version": "1.0.0", "label": "蚀刻", "cost_basis": "panel"},
                                   {"id": f"demo_laminate_{suffix}", "version": "1.0.0", "label": "压合", "cost_basis": "press_load"}],
                    "cases": [{"id": "four_layers", "facts": {}, "sets": {"inner_surfaces": [{"id": "L2"}, {"id": "L3"}]}, "expected_operations": 3}]}
        return {"summary": "根据回答整理出模板草稿。", "reply": "已按您的描述整理出路线草稿，请核对样例工序数。",
                "questions": [{"id": "resources", "question": "蚀刻和压合分别使用哪些设备或资源？"}], "template": template,
                "changes": ["新建内层重复组与压合步骤", "新建两道工序模板"]}


def provider_for(config):
    if not config.configured:
        return None
    return {"anthropic": AnthropicProvider, "openai_compatible": OpenAICompatibleProvider, "demo": DemoProvider}[config.provider](config)
