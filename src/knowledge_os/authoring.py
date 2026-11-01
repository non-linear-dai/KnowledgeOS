"""Authoring assistance contract: deterministic context, instructions and checks.

KnowledgeOS never calls a language model. An external assistant (for example
the optional assistant gateway) asks this module for

* ``context``  – the registry slice, the current draft and the output schema a
  model must follow for one authoring task, plus standard Chinese instructions
  rendered deterministically from that context (``content_hash`` and
  ``renderer_version`` identify exactly what the model was told);
* ``check``    – a read-only dry run of a draft: the same validators used at
  proposal time, plus static analysis (decision table coverage, unsatisfiable
  rule conditions, near-duplicate predicates and concepts, template gaps,
  extraction suggestions the draft would resolve).

A person then adopts the draft in Studio and submits it through the existing,
unchanged proposal endpoint. The optional ``assistance`` block on that
proposal is a declaration by the submitting author; it is recorded with the
ChangeSet so reviewers can see that a draft was assistant-written and whether
the author changed it, and so acceptance can be measured. It grants nothing.
"""
from __future__ import annotations

import copy
import hashlib
import json
import re

import jsonschema

from .core import ConflictError, NotFoundError, ValidationError, digest
from .logic_analysis import analyze_business, analyze_decision_table
from .logic_workbench import LogicWorkbench, contains_expected, public
from .similarity import similar_concepts, similar_predicates

PROTOCOL = "knowledgeos.authoring.v1"
RENDERER_VERSION = "1.0.0"
TASKS = {
    "logic.draft": "业务逻辑起草（决策表、匹配规则、业务约束）",
    "ontology.model": "本体建模起草（概念、属性、关系）",
    "extraction.card": "抽取说明卡改进起草",
    "template.interview": "专家工艺模板访谈式采集",
}
LOGIC_KINDS = ("decision_table", "business_rule", "business_constraint")
HASH = re.compile(r"^[0-9a-f]{64}$")
TEXT_LIMIT = 8000
MATERIAL_LIMIT = 30000
CATALOG_LIMIT = 400


def _text(value, name, limit=TEXT_LIMIT, required=True):
    if value is None and not required:
        return ""
    if not isinstance(value, str) or (required and not value.strip()) or len(value) > limit:
        raise ValidationError(f"{name} must be text of at most {limit} characters")
    return value.strip()


def _issue(severity, code, message, path=None, **extra):
    item = {"severity": severity, "code": code, "message": message}
    if path:
        item["path"] = path
    item.update(extra)
    return item


def _schema_errors(schema, value, limit=8):
    validator = jsonschema.Draft202012Validator(schema)
    errors = sorted(validator.iter_errors(value), key=lambda error: list(error.absolute_path))
    return [_issue("error", "OUTPUT_SCHEMA", error.message, "/".join(str(part) for part in error.absolute_path) or None) for error in errors[:limit]]


def _envelope_schema(task, required, properties):
    return {"$schema": "https://json-schema.org/draft/2020-12/schema", "type": "object",
            "required": ["protocol_version", "task", "summary", *required], "additionalProperties": False,
            "properties": {"protocol_version": {"const": PROTOCOL}, "task": {"const": task},
                           "summary": {"type": "string", "minLength": 1, "maxLength": 2000},
                           "open_questions": {"type": "array", "maxItems": 20, "items": {"type": "string", "maxLength": 500}},
                           **properties}}


EVIDENCE = {"type": "array", "maxItems": 40, "items": {"type": "object", "required": ["quote"], "additionalProperties": False,
                                                       "properties": {"quote": {"type": "string", "minLength": 1, "maxLength": 500},
                                                                      "supports": {"type": "string", "maxLength": 200}}}}


def assistance_metadata(value, submitted):
    """Validate the author's declaration that a submitted draft came from an assistant suggestion."""
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ValidationError("assistance must be an object")
    allowed = {"suggestion_id", "task", "provider", "model", "renderer_version", "instructions_hash", "context_hash",
               "output_hash", "created_at", "items_total", "items_accepted", "repair_rounds"}
    if set(value) - allowed:
        raise ValidationError("assistance has unsupported fields")
    for name in ("suggestion_id", "task", "provider", "model", "instructions_hash", "output_hash"):
        if not isinstance(value.get(name), str) or not value[name].strip() or len(value[name]) > 200:
            raise ValidationError(f"assistance.{name} is required")
    if value["task"] not in TASKS:
        raise ValidationError("assistance.task is not a known authoring task")
    for name in ("instructions_hash", "output_hash", "context_hash"):
        if name in value and not HASH.fullmatch(str(value[name])):
            raise ValidationError(f"assistance.{name} must be a sha256 hex digest")
    for name in ("renderer_version", "created_at"):
        if name in value and (not isinstance(value[name], str) or len(value[name]) > 64):
            raise ValidationError(f"assistance.{name} must be short text")
    for name in ("items_total", "items_accepted", "repair_rounds"):
        if name in value and (isinstance(value[name], bool) or not isinstance(value[name], int) or not 0 <= value[name] <= 1000):
            raise ValidationError(f"assistance.{name} must be a small nonnegative integer")
    if value.get("items_accepted", 0) > value.get("items_total", value.get("items_accepted", 0)):
        raise ValidationError("assistance.items_accepted exceeds items_total")
    submitted_hash = digest(submitted)
    return {**copy.deepcopy(value), "submitted_hash": submitted_hash,
            "modified_after_suggestion": submitted_hash != value["output_hash"],
            "scope": "declared by the submitting author; records origin, not a proof of model output"}


class Authoring:
    def __init__(self, service):
        self.service = service
        self.registry = service.registry

    # ------------------------------------------------------------------ catalog
    def tasks(self):
        return {"protocol_version": PROTOCOL, "renderer_version": RENDERER_VERSION, "registry_fingerprint": self.registry.fingerprint,
                "tasks": [{"id": key, "label": label} for key, label in TASKS.items()], "write_performed": False,
                "model_calls": "none; an external assistant calls models and submits through governed proposals"}

    def _cards(self):
        try:
            return self.service._extraction_v2().cards
        except (ValidationError, KeyError, OSError):
            return {}

    def _zh_names(self):
        """predicate id -> preferred Chinese names gathered from extraction cards."""
        names = {}
        for card in self._cards().values():
            for identifier, prop in (card.get("properties") or {}).items():
                terms = (prop or {}).get("terms") or {}
                bucket = names.setdefault(identifier, [])
                for value in [*((terms.get("pref") or {}).values()), *(terms.get("alt") or [])]:
                    if value not in bucket:
                        bucket.append(value)
        return names

    def _concept_terms(self):
        terms = {}
        for vocabulary in self.registry.vocabularies.values():
            for term in vocabulary.get("terms", []):
                if term.get("concept_ref"):
                    bucket = terms.setdefault(term["concept_ref"], [])
                    for value in [*(term.get("pref_labels") or {}).values(), *[v for values in (term.get("alt_labels") or {}).values() for v in values]]:
                        if value not in bucket:
                            bucket.append(value)
        for concept_id, card in self._cards().items():
            bucket = terms.setdefault(concept_id, [])
            for value in [*(((card.get("terms") or {}).get("pref") or {}).values()), *((card.get("terms") or {}).get("alt") or [])]:
                if value not in bucket:
                    bucket.append(value)
        return terms

    def _catalog(self):
        zh, concept_terms = self._zh_names(), self._concept_terms()
        concepts = [{"id": item["id"], "label": item.get("label", item["id"]), "names": concept_terms.get(item["id"], [])[:8],
                     "description": item.get("description", ""), "lifecycle": item.get("status", {}).get("lifecycle", "active"),
                     "properties": [binding["predicate"] for binding in item.get("properties", [])]}
                    for item in self.registry.ontology.get("concept_types", [])]
        predicates = []
        for identifier, item in sorted(self.registry.predicates.items()):
            value = item.get("value", {})
            entry = {"id": identifier, "label": item.get("label", identifier), "names": zh.get(identifier, [])[:8],
                     "type": value.get("type"), "description": (item.get("semantics") or {}).get("description", "")}
            for key in ("dimension", "units", "default_unit", "allowed", "cardinality"):
                if value.get(key):
                    entry[key] = value[key]
            predicates.append(entry)
        relations = [{"id": item["id"], "label": item.get("label", item["id"]),
                      "endpoints": [[edge["source_type"], edge["target_type"]] for edge in item.get("connections", [])]}
                     for item in self.registry.ontology.get("relation_types", [])]
        units = [{"id": identifier, "label": item.get("label", identifier), "dimension": item.get("dimension")}
                 for identifier, item in sorted(self.registry.units.items())]
        return {"concepts": concepts[:CATALOG_LIMIT], "predicates": predicates[:CATALOG_LIMIT], "relations": relations[:CATALOG_LIMIT],
                "units": units, "currencies": sorted(self.registry.currencies),
                "truncated": len(predicates) > CATALOG_LIMIT or len(concepts) > CATALOG_LIMIT}

    # ------------------------------------------------------------------ context
    def context(self, task, task_input=None, include=("instructions",)):
        if task not in TASKS:
            raise ValidationError(f"unknown authoring task: {task}")
        task_input = task_input or {}
        if not isinstance(task_input, dict):
            raise ValidationError("authoring input must be an object")
        include = list(include or [])
        if any(item not in ("instructions",) for item in include):
            raise ValidationError("authoring include accepts instructions only")
        self.service.refresh_registry()
        self.registry = self.service.registry
        builder = {"logic.draft": self._logic_context, "ontology.model": self._ontology_context,
                   "extraction.card": self._card_context, "template.interview": self._template_context}[task]
        normalized, context, schema, extra = builder(task_input)
        packet = {"protocol_version": PROTOCOL, "task": task, "task_label": TASKS[task],
                  "registry_fingerprint": self.registry.fingerprint, "input": normalized,
                  "context": context, "output_schema": schema, **extra}
        packet["context_hash"] = digest({"input": normalized, "context": context, "output_schema": schema, **extra})
        packet["request_id"] = "authoring:" + packet["context_hash"][:32]
        if "instructions" in include:
            markdown = render_instructions(task, normalized, context, schema)
            packet["instructions"] = {"format": "markdown", "language": "zh", "renderer_version": RENDERER_VERSION,
                                      "content_hash": hashlib.sha256(markdown.encode("utf-8")).hexdigest(), "markdown": markdown}
        packet["write_performed"] = False
        return packet

    def _logic_context(self, task_input):
        description = _text(task_input.get("description"), "description")
        kind = task_input.get("kind")
        if kind is not None and kind not in LOGIC_KINDS:
            raise ValidationError(f"logic assistance supports {', '.join(LOGIC_KINDS)}")
        current = task_input.get("current")
        if current is not None:
            if not isinstance(current, dict) or current.get("kind") not in LOGIC_KINDS or not isinstance(current.get("definition"), dict):
                raise ValidationError("current draft requires a supported kind and definition object")
            current = {"kind": current["kind"], "definition": copy.deepcopy(current["definition"])}
            kind = kind or current["kind"]
        catalog = self._catalog()
        assets = [{"ref": item["ref"], "kind": item["kind"], "id": item["id"], "version": item["version"], "label": item["label"],
                   "description": item["definition"].get("description", "")}
                  for item in LogicWorkbench(self.service).catalog()["assets"]][:CATALOG_LIMIT]
        normalized = {"description": description, "kind": kind, "current": current}
        context = {**catalog, "assets": assets}
        schema = _envelope_schema("logic.draft", ["kind", "definition", "test_cases"], {
            "kind": {"enum": [kind] if kind else list(LOGIC_KINDS)},
            "definition": {"type": "object"},
            "assumptions": {"type": "array", "maxItems": 20, "items": {"type": "string", "maxLength": 500}},
            "test_cases": {"type": "array", "minItems": 1, "maxItems": 20,
                           "items": {"type": "object", "required": ["id", "label", "input", "expected"], "additionalProperties": False,
                                     "properties": {"id": {"type": "string", "pattern": "^[a-z][a-z0-9_-]{0,63}$"},
                                                    "label": {"type": "string", "minLength": 1, "maxLength": 200},
                                                    "input": {"type": "object"}, "expected": {"type": "object", "minProperties": 1},
                                                    "rationale": {"type": "string", "maxLength": 500}}}},
            "evidence": EVIDENCE})
        return normalized, context, schema, {}

    def _ontology_context(self, task_input):
        from .semantic_workbench import SemanticWorkbench
        goal = _text(task_input.get("goal"), "goal", 4000)
        material = _text(task_input.get("material"), "material", MATERIAL_LIMIT)
        request = SemanticWorkbench(self.service).prepare(goal=goal, material=material)
        catalog = self._catalog()
        examples = {}
        for definition in request["definition_examples"]:
            if definition["kind"] in ("concept", "predicate", "relation") and definition["kind"] not in examples:
                examples[definition["kind"]] = definition
        context = {**catalog, "definition_examples": examples, "segment_ids": [segment["id"] for segment in request["segments"]]}
        schema = _envelope_schema("ontology.model", ["model_output"], {
            "model_output": request["output_schema"],
            "notes": {"type": "array", "maxItems": 40, "items": {"type": "object", "required": ["target_id", "explanation"], "additionalProperties": False,
                                                               "properties": {"target_id": {"type": "string"}, "explanation": {"type": "string", "maxLength": 800},
                                                                              "reused": {"type": "array", "items": {"type": "string"}}}}}})
        return {"goal": goal, "material": material}, context, schema, {"ontology_request": request}

    def _card_context(self, task_input):
        from .extraction_feedback import Feedback
        concept = _text(task_input.get("concept"), "concept", 64)
        include_samples = task_input.get("include_samples", True)
        if not isinstance(include_samples, bool):
            raise ValidationError("include_samples must be true or false")
        engine = self.service._extraction_v2()
        if concept not in engine.concepts:
            raise NotFoundError(f"unknown concept: {concept}")
        focus = task_input.get("suggestion_ids") or []
        if not isinstance(focus, list) or len(focus) > 50 or any(not isinstance(item, str) for item in focus):
            raise ValidationError("suggestion_ids must be a list of up to 50 ids")
        feedback = Feedback(engine).build(concept=concept)
        suggestions = [item for item in feedback["suggestions"] if item["status"] == "open" and (not focus or item["id"] in focus)][:40]
        concept_def = engine.concepts[concept]
        properties = []
        for binding in concept_def.get("properties", []):
            predicate = self.registry.predicates[binding["predicate"]]
            value = predicate.get("value", {})
            entry = {"id": binding["predicate"], "label": predicate.get("label", binding["predicate"]), "type": value.get("type"),
                     "required": binding.get("required", False)}
            for key in ("dimension", "units", "default_unit", "allowed"):
                if value.get(key):
                    entry[key] = value[key]
            properties.append(entry)
        current = public(copy.deepcopy(engine.cards.get(concept))) if engine.cards.get(concept) else None
        context = {"concept": {"id": concept, "label": concept_def.get("label", concept), "description": concept_def.get("description", ""),
                               "properties": properties},
                   "current_card": current, "card_exists": current is not None,
                   "suggestions": [{key: item.get(key) for key in ("id", "kind", "predicate", "predicate_label", "text", "count", "documents",
                                                                    *(("samples",) if include_samples else ()), "raw_values", "action", "advice")}
                                   for item in suggestions],
                   "feedback_totals": feedback["totals"],
                   "units": [{"id": identifier, "dimension": item.get("dimension")} for identifier, item in sorted(self.registry.units.items())]}
        schema = _envelope_schema("extraction.card", ["card", "changes"], {
            "card": {"type": "object", "required": ["format", "concept", "card_version"],
                     "properties": {"format": {"const": "knowledgeos.extraction-card.v1"}, "concept": {"const": concept},
                                    "card_version": {"type": "string", "pattern": r"^[0-9]+\.[0-9]+\.[0-9]+$"}}},
            "changes": {"type": "array", "minItems": 1, "maxItems": 40,
                        "items": {"type": "object", "required": ["field", "description"], "additionalProperties": False,
                                  "properties": {"suggestion_id": {"type": "string"}, "field": {"type": "string", "maxLength": 200},
                                                 "description": {"type": "string", "maxLength": 800}}}}})
        return {"concept": concept, "suggestion_ids": focus, "include_samples": include_samples}, context, schema, {}

    def _template_context(self, task_input):
        from .templates import TemplateWorkbench
        conversation = task_input.get("conversation") or []
        if not isinstance(conversation, list) or len(conversation) > 40:
            raise ValidationError("conversation must be a list of at most 40 turns")
        turns = []
        for turn in conversation:
            if not isinstance(turn, dict) or turn.get("role") not in ("expert", "assistant"):
                raise ValidationError("conversation turns require role expert or assistant")
            turns.append({"role": turn["role"], "content": _text(turn.get("content"), "conversation content", 4000)})
        if not turns or turns[-1]["role"] != "expert":
            raise ValidationError("the last conversation turn must come from the expert")
        current = task_input.get("current")
        if current is not None and not isinstance(current, dict):
            raise ValidationError("current template draft must be an object")
        workbench = TemplateWorkbench(self.service)
        hidden_operations = self.service._unpublished_template_assets()[1]
        operations = [{"ref": item["id"], "label": item.get("label"), "cost_basis": item.get("cost_basis")}
                      for item in workbench.list_operations() if item["id"] not in hidden_operations][:CATALOG_LIMIT]
        duration_models = [f"{identifier}@{item.get('version')}" for identifier, item in sorted(self.registry.models.items())
                           if self.registry.units.get(item.get("output_unit"), {}).get("dimension") == "duration"]
        decisions = [f"decision:{item['id']}:{item['version']}" for item in workbench.list_decisions()]
        templates = [{"id": item["id"], "label": item.get("label")} for item in workbench.list_templates()]
        gaps = template_gaps(current or {}, {item["ref"] for item in operations})
        context = {"published_operations": operations, "duration_models": duration_models, "decision_tables": decisions,
                   "existing_templates": templates, "units": [{"id": identifier, "dimension": item.get("dimension")} for identifier, item in sorted(self.registry.units.items())],
                   "current_template": copy.deepcopy(current), "gaps": gaps}
        schema = _envelope_schema("template.interview", ["reply", "questions"], {
            "reply": {"type": "string", "minLength": 1, "maxLength": 3000},
            "questions": {"type": "array", "maxItems": 5, "items": {"type": "object", "required": ["id", "question"], "additionalProperties": False,
                                                                    "properties": {"id": {"type": "string", "maxLength": 64}, "question": {"type": "string", "maxLength": 500},
                                                                                   "why": {"type": "string", "maxLength": 500},
                                                                                   "options": {"type": "array", "maxItems": 6, "items": {"type": "string", "maxLength": 200}}}}},
            "template": {"type": ["object", "null"]},
            "changes": {"type": "array", "maxItems": 40, "items": {"type": "string", "maxLength": 500}}})
        return {"conversation": turns, "current": copy.deepcopy(current)}, context, schema, {}

    # ------------------------------------------------------------------ check
    def check(self, task, draft, task_input=None):
        if task not in TASKS:
            raise ValidationError(f"unknown authoring task: {task}")
        if not isinstance(draft, dict):
            raise ValidationError("draft must be an object")
        self.service.refresh_registry()
        self.registry = self.service.registry
        handler = {"logic.draft": self._check_logic, "ontology.model": self._check_ontology,
                   "extraction.card": self._check_card, "template.interview": self._check_template}[task]
        issues, analysis, submitted = handler(draft, task_input or {})
        errors = [item for item in issues if item["severity"] == "error"]
        return {"protocol_version": PROTOCOL, "task": task, "registry_fingerprint": self.registry.fingerprint,
                "valid": not errors, "error_count": len(errors), "warning_count": sum(item["severity"] == "warning" for item in issues),
                "issues": issues, "analysis": analysis,
                "draft_hash": digest(submitted) if submitted is not None else None, "submitted": submitted,
                "write_performed": False}

    def _check_logic(self, draft, task_input):
        issues, analysis = [], {}
        kind, definition = draft.get("kind"), draft.get("definition")
        if kind not in LOGIC_KINDS or not isinstance(definition, dict):
            return [_issue("error", "INVALID_DRAFT", "草稿需要受支持的类型（决策表、匹配规则或业务约束）和定义对象")], analysis, None
        workbench = LogicWorkbench(self.service)
        try:
            prepared = workbench.prepare(kind, definition)
        except (ValidationError, NotFoundError, KeyError, TypeError) as exc:
            return [_issue("error", "INVALID_DEFINITION", f"定义未通过校验：{exc}")], analysis, None
        published = [item for item in workbench.catalog()["assets"] if item["kind"] == kind and item["id"] == prepared["id"]]
        if any(item["version"] == prepared["version"] for item in published):
            issues.append(_issue("error", "VERSION_EXISTS", f"{prepared['id']} v{prepared['version']} 已发布，请提高版本号", "definition/version"))
        elif published:
            issues.append(_issue("info", "NEW_VERSION", f"将作为已登记逻辑 {prepared['id']} 的新版本提交（已有 {', '.join(item['version'] for item in published)}）"))
        if kind == "decision_table":
            coverage = analyze_decision_table(prepared, self.registry.units)
            analysis["coverage"] = coverage
            if coverage["gap_count"]:
                issues.append(_issue("warning", "COVERAGE_GAP", f"有 {coverage['gap_count']} 类输入组合没有规则命中，且没有默认结果；运行时会报错", "definition/rules",
                                     regions=coverage["gaps"][:5]))
            for rule_id in coverage["unreachable_rules"]:
                issues.append(_issue("warning", "UNREACHABLE_RULE", f"规则 {rule_id} 被排在前面的规则完全覆盖，按“首条命中”永远不会生效", f"definition/rules/{rule_id}"))
            if not coverage["exhaustive"]:
                issues.append(_issue("info", "COVERAGE_PARTIAL", "输入组合过多，覆盖检查只分析了每条规则自身的区域"))
        else:
            business = analyze_business(prepared, self.registry.units)
            analysis["conditions"] = business
            for item in business["contradictions"]:
                issues.append(_issue("warning", "CONTRADICTION", item["message"], f"definition/checks/{item['checks'][0]}"))
        regression = workbench.tests(kind, prepared)
        analysis["regression_tests"] = regression
        for row in regression["cases"]:
            if not row["passed"]:
                issues.append(_issue("warning", "REGRESSION_FAILED", f"已有回归案例 {row['id']} 未通过" + (f"：{row['error']}" if row.get("error") else ""), f"definition/test_cases/{row['id']}"))
        intent = []
        for index, case in enumerate(draft.get("test_cases") or []):
            if not isinstance(case, dict) or not isinstance(case.get("input"), dict) or not isinstance(case.get("expected"), dict):
                issues.append(_issue("error", "INVALID_TEST", f"第 {index + 1} 个意图案例缺少 input 或 expected", f"test_cases/{index}"))
                continue
            row = {"id": case.get("id"), "label": case.get("label"), "input": case["input"], "expected": case["expected"], "rationale": case.get("rationale")}
            try:
                actual = workbench.evaluate(kind, prepared, case["input"])
                row["actual"] = actual
                row["passed"] = contains_expected(actual, case["expected"])
            except (ValidationError, KeyError, TypeError, ValueError) as exc:
                row["passed"], row["error"] = False, str(exc)
            if not row["passed"]:
                issues.append(_issue("warning", "INTENT_MISMATCH", f"意图案例「{case.get('label') or case.get('id')}」的预期与定义的实际结果不一致：需由专家判断是定义还是预期有误", f"test_cases/{index}"))
            intent.append(row)
        analysis["intent_tests"] = intent
        source = str(task_input.get("description") or "")
        unverified = [item.get("quote") for item in draft.get("evidence") or [] if isinstance(item, dict) and source and item.get("quote") and item["quote"] not in source]
        if unverified:
            issues.append(_issue("warning", "EVIDENCE_NOT_FOUND", f"{len(unverified)} 处引用在业务说明中找不到原文", "evidence", quotes=unverified[:5]))
        if not prepared.get("test_cases") and not any(row.get("passed") for row in intent):
            issues.append(_issue("info", "NEEDS_CONFIRMED_CASES", "提交审核前至少需要一个由专家确认的回归案例"))
        return issues, analysis, prepared

    def _check_ontology(self, draft, task_input):
        from .semantic_workbench import SemanticWorkbench
        issues, analysis = [], {"similar": []}
        request, output = draft.get("ontology_request"), draft.get("model_output")
        if not isinstance(request, dict) or not isinstance(output, dict):
            return [_issue("error", "INVALID_DRAFT", "需要建模请求包和模型草稿")], analysis, None
        try:
            preview = SemanticWorkbench(self.service).finalize(request=request, model_output=output)
        except ConflictError as exc:
            return [_issue("error", "STALE_REQUEST", f"控制定义已变化，请重新起草：{exc}")], analysis, None
        except (ValidationError, KeyError, TypeError) as exc:
            return [_issue("error", "INVALID_DEFINITION", f"建模草稿未通过校验：{exc}")], analysis, None
        analysis["impact"] = preview["impact"]
        analysis["operations"] = preview["operations"]
        for item in preview["impact"]["issues"]:
            issues.append(_issue("error", "IMPACT", f"候选定义编译或兼容性检查未通过：{item['message']}"))
        cards = self._cards()
        for operation in preview["operations"]:
            if operation.get("type") != "create":
                continue
            after = operation.get("after") or {}
            config = after.get("config") or {}
            proposal = {"id": operation["targetId"], "label": after.get("label"), "aliases": after.get("aliases") or [],
                        "value_type": config.get("value_type"), "dimension": config.get("dimension")}
            if operation["targetKind"] == "predicate":
                matches = similar_predicates(self.registry, proposal, cards)
            elif operation["targetKind"] == "concept":
                matches = similar_concepts(self.registry, proposal, cards)
            else:
                matches = []
            if matches:
                analysis["similar"].append({"target_kind": operation["targetKind"], "target_id": operation["targetId"], "label": after.get("label"), "matches": matches})
                best = matches[0]
                kind_label = "属性" if operation["targetKind"] == "predicate" else "概念"
                if best["verdict"] == "reuse":
                    issues.append(_issue("warning", "LIKELY_DUPLICATE", f"新{kind_label}「{after.get('label') or operation['targetId']}」与已有{kind_label} {best['id']}（{best['existing_name']['name']}）几乎同名，建议复用已有定义", f"operations/{operation['targetId']}"))
                elif best["verdict"] == "dimension_conflict":
                    issues.append(_issue("warning", "NAME_DIMENSION_CONFLICT", f"新属性「{after.get('label') or operation['targetId']}」与已有属性 {best['id']} 名称相近但量纲不同，请确认是不同含义并区分命名", f"operations/{operation['targetId']}"))
                else:
                    issues.append(_issue("info", "SIMILAR_DEFINITION", f"新{kind_label}「{after.get('label') or operation['targetId']}」与 {best['id']} 相近（相似度 {best['score']}）", f"operations/{operation['targetId']}"))
        return issues, analysis, output

    def _check_card(self, draft, task_input):
        from .extraction_feedback import Feedback
        from .extraction_v2 import ExtractionV2
        issues, analysis = [], {}
        card = draft.get("card")
        if not isinstance(card, dict):
            return [_issue("error", "INVALID_DRAFT", "需要说明卡文档")], analysis, None
        concept = card.get("concept")
        engine = self.service._extraction_v2()
        if task_input.get("concept") and concept != task_input["concept"]:
            issues.append(_issue("error", "CONCEPT_MISMATCH", "说明卡概念与起草任务不一致"))
        open_items = []
        if concept in engine.concepts:
            open_items = [item for item in Feedback(engine).build(concept=concept)["suggestions"] if item["status"] == "open"]
        samples = []
        for item in open_items:
            if item["kind"] == "unit_alias":
                samples.extend({"concept": concept, "predicate": item["predicate"], "raw": raw} for raw in item.get("raw_values") or [] if raw)
            elif item["kind"] in ("enum_alias", "value_format") and item.get("predicate"):
                samples.append({"concept": concept, "predicate": item["predicate"], "raw": item["text"]})
        preview = engine.asset_preview("card", card, samples=samples[:50])
        analysis["preview"] = {key: value for key, value in preview.items() if key != "instructions"}
        for message in preview.get("errors", []):
            issues.append(_issue("error", "INVALID_CARD", message))
        for message in preview.get("warnings", []):
            issues.append(_issue("warning", "CARD_WARNING", message if isinstance(message, str) else json.dumps(message, ensure_ascii=False)))
        resolved = []
        if not preview.get("errors"):
            candidate = ExtractionV2(self.service)
            candidate.cards = {**candidate.cards, concept: card}
            judge = Feedback(candidate)
            for item in open_items:
                status, _, advice = judge._resolve(item)
                if status == "resolved":
                    resolved.append({"id": item["id"], "kind": item["kind"], "text": item["text"], "advice": advice})
        analysis["resolves"] = resolved
        analysis["open_before"] = len(open_items)
        if open_items:
            issues.append(_issue("info", "RESOLVES_SUGGESTIONS", f"此草稿可解决 {len(resolved)} / {len(open_items)} 条待处理的抽取建议"))
        return issues, analysis, card if not preview.get("errors") else None

    def _check_template(self, draft, task_input):
        from .templates import TemplateWorkbench
        issues, analysis = [], {}
        template = draft.get("template")
        if template is None:
            return [_issue("info", "NO_TEMPLATE", "本轮只有追问，尚未形成模板草稿")], analysis, None
        if not isinstance(template, dict):
            return [_issue("error", "INVALID_DRAFT", "模板草稿必须是对象")], analysis, None
        workbench = TemplateWorkbench(self.service)
        hidden = self.service._unpublished_template_assets()[1]
        published = {item["id"] for item in workbench.list_operations() if item["id"] not in hidden}
        gaps = template_gaps(template, published)
        analysis["gaps"] = gaps
        for gap in gaps:
            issues.append(_issue("warning" if gap["blocking"] else "info", "TEMPLATE_GAP", gap["message"], gap["path"]))
        try:
            analysis["summary"] = workbench.validate(template, check_refs=True)
        except (ValidationError, ConflictError, KeyError, TypeError) as exc:
            issues.append(_issue("error", "INVALID_TEMPLATE", f"模板未通过校验：{exc}"))
            return issues, analysis, None
        previews = []
        for case in template.get("cases", []):
            try:
                result = workbench.preview(template, case)
                previews.append({"case": case["id"], "operation_count": result["operation_count"], "expected": case.get("expected_operations"),
                                 "matches_expected": result["operation_count"] == case.get("expected_operations")})
            except (ValidationError, KeyError, TypeError) as exc:
                previews.append({"case": case.get("id"), "error": str(exc)})
        analysis["cases"] = previews
        for row in previews:
            if row.get("error"):
                issues.append(_issue("error", "CASE_FAILED", f"样例 {row['case']} 无法展开：{row['error']}"))
            elif not row["matches_expected"]:
                issues.append(_issue("warning", "CASE_COUNT", f"样例 {row['case']} 展开得到 {row['operation_count']} 道工序，与专家预期 {row['expected']} 不一致，请专家确认"))
        return issues, analysis, template

    # ------------------------------------------------------------------ feedback
    def feedback(self):
        rows = self.service.db.execute("SELECT id,title,status,created_at,actor,proposal_metadata_json FROM changeset WHERE proposal_metadata_json IS NOT NULL ORDER BY created_at DESC")
        tasks, recent = {}, []
        for row in rows:
            try:
                metadata = json.loads(row["proposal_metadata_json"])
            except (TypeError, json.JSONDecodeError):
                continue
            assistance = metadata.get("assistance") if isinstance(metadata, dict) else None
            if not isinstance(assistance, dict) or assistance.get("task") not in TASKS:
                continue
            stats = tasks.setdefault(assistance["task"], {"task": assistance["task"], "label": TASKS[assistance["task"]], "submitted": 0,
                                                          "statuses": {}, "modified": 0, "items_total": 0, "items_accepted": 0, "models": {}})
            stats["submitted"] += 1
            stats["statuses"][row["status"]] = stats["statuses"].get(row["status"], 0) + 1
            stats["modified"] += bool(assistance.get("modified_after_suggestion"))
            stats["items_total"] += int(assistance.get("items_total") or 0)
            stats["items_accepted"] += int(assistance.get("items_accepted") or 0)
            model = f"{assistance.get('provider')}/{assistance.get('model')}"
            stats["models"][model] = stats["models"].get(model, 0) + 1
            if len(recent) < 20:
                recent.append({"id": row["id"], "title": row["title"], "status": row["status"], "created_at": row["created_at"], "actor": row["actor"],
                               "task": assistance["task"], "modified_after_suggestion": assistance.get("modified_after_suggestion"), "model": model})
        for stats in tasks.values():
            decided = sum(count for status, count in stats["statuses"].items() if status in ("approved", "rejected", "changes_requested", "published"))
            accepted = sum(count for status, count in stats["statuses"].items() if status in ("approved", "published"))
            stats["approval_rate"] = round(accepted / decided, 4) if decided else None
            stats["modification_rate"] = round(stats["modified"] / stats["submitted"], 4) if stats["submitted"] else None
            stats["item_acceptance_rate"] = round(stats["items_accepted"] / stats["items_total"], 4) if stats["items_total"] else None
        return {"protocol_version": PROTOCOL, "tasks": sorted(tasks.values(), key=lambda item: item["task"]), "recent": recent,
                "scope": "proposals that declared assistant origin; discarded suggestions never reach KnowledgeOS", "write_performed": False}


def template_gaps(spec, published_operations):
    """Non-raising checklist of what a template draft still needs; drives interview questions."""
    gaps = []

    def gap(path, message, blocking=True):
        gaps.append({"path": path, "message": message, "blocking": blocking})

    for name, label in (("id", "模板标识"), ("version", "版本"), ("label", "模板名称"), ("family", "工艺族"), ("output_basis", "产出计量基准")):
        if not str(spec.get(name) or "").strip():
            gap(name, f"缺少{label}")
    steps = spec.get("steps") if isinstance(spec.get("steps"), list) else []
    groups = spec.get("groups") if isinstance(spec.get("groups"), list) else []
    operations = spec.get("operations") if isinstance(spec.get("operations"), list) else []
    cases = spec.get("cases") if isinstance(spec.get("cases"), list) else []
    new_refs = {f"operation:{item.get('id')}:{item.get('version')}" for item in operations if isinstance(item, dict)}
    if not steps:
        gap("steps", "还没有工艺步骤")
    for index, step in enumerate(steps):
        if not isinstance(step, dict):
            continue
        name = step.get("label") or step.get("id") or f"第 {index + 1} 步"
        if not step.get("cost_basis"):
            gap(f"steps/{index}/cost_basis", f"步骤「{name}」缺少计价基准（如按件、按板）")
        ref = step.get("operation_ref")
        if not ref:
            gap(f"steps/{index}/operation_ref", f"步骤「{name}」没有关联工序模板")
        elif ref not in published_operations and ref not in new_refs:
            gap(f"steps/{index}/operation_ref", f"步骤「{name}」引用的工序 {ref} 既未发布也不在本次新建工序中")
    for index, group in enumerate(groups):
        if not isinstance(group, dict):
            continue
        if group.get("group_mode", "repeat") == "repeat" and not group.get("iteration_set"):
            gap(f"groups/{index}/iteration_set", f"重复组「{group.get('label') or group.get('id')}」没有说明按什么集合重复（如每个内层、每个特征面）")
        if not any(isinstance(step, dict) and step.get("parent") == group.get("id") for step in steps):
            gap(f"groups/{index}", f"分组「{group.get('label') or group.get('id')}」中还没有步骤")
    for index, operation in enumerate(operations):
        if isinstance(operation, dict) and not operation.get("resources") and not operation.get("processing") and not operation.get("time_model_ref"):
            gap(f"operations/{index}", f"新工序「{operation.get('label') or operation.get('id')}」尚未说明资源、工时或时间模型", blocking=False)
    if not cases:
        gap("cases", "还没有专家样例：至少需要一个样例及其预期工序数")
    for index, case in enumerate(cases):
        if isinstance(case, dict) and not isinstance(case.get("expected_operations"), int):
            gap(f"cases/{index}/expected_operations", f"样例「{case.get('id') or index + 1}」缺少专家预期的工序数量")
    return gaps


# ---------------------------------------------------------------------- render
COMMON_RULES = [
    "你只起草，不写入。所有草稿都会经过 KnowledgeOS 的确定性校验、专家确认和独立审核后才可能生效。",
    "只使用“可用定义”中列出的标识（概念、属性、单位、工序、模型等）。不要杜撰不存在的标识；确实需要新定义时，在 open_questions 中说明。",
    "依据用户提供的说明或材料起草；不确定的地方写进 assumptions 或 open_questions，不要猜测业务数值。",
    "数值一律写成十进制字符串（如 \"300\"、\"7.5\"），不要做任何换算或计算；单位、币种必须使用已注册的标识。",
    "只输出一个 JSON 对象，必须符合下方“输出格式”，不要输出 Markdown 代码块或其他文字。",
    "summary 用中文向业务人员说明草稿做了什么、依据是什么。",
]

TASK_RULES = {
    "logic.draft": [
        "kind 选择：按条件组合查表得出结果用 decision_table；从候选实例中筛选合格者用 business_rule；校验单个实例事实是否合规用 business_constraint。",
        "决策表的 rules 中每条规则必须给出全部输入列（不限制时写 null）和全部输出列；hit_policy 用 UNIQUE（互斥）、FIRST（按顺序首条命中）或 COLLECT（收集全部）。",
        "UNIQUE 的规则区间不能重叠；边界值归属必须明确（例如 ≤300 与 >300）。如说明中存在“其他情况”，使用 default_output。",
        "业务规则/约束的 inputs 只能引用已绑定到对应概念的属性；比较 right 写 {\"input\": 输入id} 或 {\"value\": 字面值}，数量值写 {\"value\": \"5\", \"unit\": \"kW\"}。",
        "test_cases 是“意图案例”：根据业务说明独立写出输入和预期结果，不要根据你写的定义倒推；KnowledgeOS 会用确定性引擎执行，不一致的地方会交给专家判断。",
        "决策表案例 input 写 {\"facts\": {...}}（列 source 为 item 时另给 \"item\"），expected 写 {\"result\": {输出列: 值}}；规则/约束案例 expected 写 {\"status\": ...} 或 {\"eligible_candidate_ids\": [...]}。",
        "evidence.quote 必须是业务说明中的原文片段，用于标明每条规则的依据。",
        "如果提供了当前草稿（current），在其基础上修改并保留稳定标识；新版本须提高 version。",
    ],
    "ontology.model": [
        "优先复用已有概念和属性：列表中 names 是业务人员和抽取说明卡使用的中文叫法。含义相同就复用，不要新建近义属性。",
        "model_output 必须符合 knowledgeos.ontology-assist.v1：request_id 使用输入中给出的值；每个操作的 evidence 引用 segment_id \"material:1\" 中的原文。",
        "新属性需要完整的值与策略配置（value_type、cardinality、storage_mode、authority、freshness、provenance_tier、temporal、evidence、history、write），参照 definition_examples 中的写法；数量型属性必须声明 dimension 与 units。",
        "先注册属性再绑定到概念；不要写入任何业务实例事实。",
        "notes 中逐项说明每个操作的理由；如复用了已有定义，在 reused 中列出其标识。",
    ],
    "extraction.card": [
        "在当前说明卡（current_card）基础上修改，保留已有内容；card_version 必须高于当前版本（不存在时从 1.0.0 开始）。",
        "逐条处理 suggestions：单位写法加入对应属性的 unit_aliases（目标必须是该属性允许的单位）；枚举说法加入 enum_map；属性叫法加入 properties.<属性>.terms.alt；过宽或误抽的线索写入 negative_examples 或 boundary_notes。",
        "只有确认原文写法与某个已注册单位/可选值等价时才加映射；无法判断的写入 open_questions。",
        "changes 中逐条说明修改，处理某条建议时填写 suggestion_id。",
    ],
    "template.interview": [
        "你是工艺专家访谈助手。根据对话和当前模板草稿，先在 reply 中用一两句话复述你理解的工艺，再提出最多 3 个最关键的问题（questions），优先补齐 gaps 中标为缺失的信息。",
        "一次只问专家能直接回答的具体问题（例如：这一步按什么计价？对每个内层都要做吗？什么条件下跳过？），可给出 options 供选择。",
        "当信息足够时，在 template 中给出完整模板草稿（结构参照示例）；信息不足时 template 可以为 null。",
        "步骤引用的工序优先使用 published_operations；确需新工序时在 operations 中新建，并在 changes 中说明。",
        "cases 中 expected_operations 必须来自专家明确给出的数量，不要自己推算；未确认时在 questions 中询问。",
    ],
}

EXAMPLES = {
    "logic.draft": [{"kind": "decision_table", "definition": {
        "id": "size_equipment_class", "version": "1.0.0", "label": "按零件尺寸选择设备等级", "description": "尺寸不超过 300 mm 用小型设备，否则用大型设备",
        "hit_policy": "UNIQUE", "inputs": [{"id": "size", "label": "零件尺寸", "source": "facts", "field": "size", "type": "number"}],
        "outputs": [{"id": "equipment_class", "label": "设备等级", "type": "string"}],
        "rules": [{"id": "small", "when": {"size": {"op": "lte", "value": "300"}}, "then": {"equipment_class": "small"}},
                  {"id": "large", "when": {"size": {"op": "gt", "value": "300"}}, "then": {"equipment_class": "large"}}]},
        "test_cases": [{"id": "boundary_300", "label": "恰好 300 mm 属于小型", "input": {"facts": {"size": "300"}}, "expected": {"result": {"equipment_class": "small"}}}]},
        {"kind": "business_rule", "note": "结构示意：主体与候选均为设备，候选的额定功率不低于主体要求", "definition": {
            "format": "knowledgeos.business-rule.v1", "id": "power_capability_match", "version": "1.0.0", "label": "功率能力匹配",
            "description": "候选设备的额定功率不低于需求设备的额定功率", "status": {"lifecycle": "active"},
            "scope": {"subject_concept": "equipment", "candidate_concept": "equipment", "context_concepts": {}},
            "inputs": [{"id": "required", "role": "subject", "predicate": "rated_power"}, {"id": "available", "role": "candidate", "predicate": "rated_power"}],
            "checks": [{"left": "available", "operator": "gte", "right": {"input": "required"}, "message": "候选功率应达到需求"},
                       {"left": "available", "operator": "lte", "right": {"value": {"value": "50", "unit": "kW"}}, "message": "不超过 50 kW"}]},
         "test_cases": [{"id": "enough_power", "label": "10 kW 候选满足 7.5 kW 需求",
                         "input": {"subject": {"rated_power": {"literal": "7.5", "unit": "kW"}}, "candidates": [{"id": "eq_a", "rated_power": {"literal": "10", "unit": "kW"}}]},
                         "expected": {"eligible_candidate_ids": ["eq_a"]}}]}],
    "template.interview": {"template": {
        "id": "rigid_multilayer", "version": "1.0.0", "label": "多层刚性板", "family": "pcb", "output_basis": "board", "summary": "专家标准路线",
        "groups": [{"id": "inner_circuit", "label": "内层线路", "iteration_set": "inner_surfaces", "join_policy": "all"}],
        "steps": [{"id": "inner_etch", "label": "内层蚀刻", "parent": "inner_circuit", "operation_ref": "operation:etch:1.0.0", "cost_basis": "panel"},
                  {"id": "lamination", "label": "压合", "parent": None, "operation_ref": "operation:laminate:1.0.0", "cost_basis": "press_load"}],
        "edges": [{"from": "inner_circuit", "to": "lamination"}],
        "operations": [{"id": "etch", "version": "1.0.0", "label": "蚀刻", "cost_basis": "panel"},
                       {"id": "laminate", "version": "1.0.0", "label": "压合", "cost_basis": "press_load"}],
        "cases": [{"id": "four_layers", "facts": {}, "sets": {"inner_surfaces": [{"id": "L2"}, {"id": "L3"}]}, "expected_operations": 3}]}},
}


def _json(value):
    return json.dumps(value, ensure_ascii=False, indent=1, sort_keys=False)


def _table(rows, columns):
    if not rows:
        return "（无）\n"
    lines = ["| " + " | ".join(label for _, label in columns) + " |", "|" + "---|" * len(columns)]
    for row in rows:
        cells = []
        for key, _ in columns:
            value = row.get(key)
            if isinstance(value, list):
                value = "、".join(str(item) for item in value)
            cells.append(str(value if value not in (None, "") else "").replace("|", "\\|").replace("\n", " "))
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines) + "\n"


def render_instructions(task, task_input, context, schema):
    """Deterministic Chinese instructions for one authoring task (same input → same bytes)."""
    parts = [f"# KnowledgeOS 编写助手说明 · {TASKS[task]}", "",
             f"> 协议 {PROTOCOL} · 渲染器 {RENDERER_VERSION}", "", "## 必须遵守", ""]
    parts += [f"{index}. {rule}" for index, rule in enumerate(COMMON_RULES + TASK_RULES[task], 1)]
    parts += ["", "## 可用定义", ""]
    if task in ("logic.draft", "ontology.model"):
        parts += ["### 概念", "", _table(context["concepts"], [("id", "标识"), ("label", "名称"), ("names", "中文叫法"), ("properties", "已绑定属性")]),
                  "### 属性", "", _table(context["predicates"], [("id", "标识"), ("label", "名称"), ("names", "中文叫法"), ("type", "值类型"), ("dimension", "量纲"), ("units", "单位"), ("allowed", "可选值")]),
                  "### 单位", "", _table(context["units"], [("id", "标识"), ("label", "名称"), ("dimension", "量纲")])]
    if task == "ontology.model":
        parts += ["### 关系", "", _table(context["relations"], [("id", "标识"), ("label", "名称"), ("endpoints", "端点")]),
                  "### 定义写法示例（Studio 操作格式）", "", "```json", _json(context["definition_examples"]), "```", ""]
    if task == "logic.draft" and context.get("assets"):
        parts += ["### 已登记业务逻辑", "", _table(context["assets"], [("ref", "引用"), ("label", "名称"), ("description", "业务目的")])]
    if task == "extraction.card":
        concept = context["concept"]
        parts += [f"### 概念：{concept['label']}（{concept['id']}）", "", concept.get("description", ""), "",
                  _table(concept["properties"], [("id", "属性"), ("label", "名称"), ("type", "值类型"), ("units", "允许单位"), ("allowed", "可选值")]),
                  "### 当前说明卡", "", "```json", _json(context["current_card"]), "```", "",
                  f"### 待处理的抽取建议（{len(context['suggestions'])} 条）", ""]
        for item in context["suggestions"]:
            sample = next((entry.get("text") for entry in item.get("samples") or [] if entry.get("text")), "")
            parts += [f"- `{item['id']}` [{item['kind']}] {item.get('predicate_label') or item.get('predicate') or ''}「{item['text']}」出现 {item['count']} 次：{item['advice']}"
                      + (f" 原文样例：{sample[:160]}" if sample else "")]
        parts += [""]
    if task == "template.interview":
        parts += ["### 已发布工序", "", _table(context["published_operations"], [("ref", "引用"), ("label", "名称"), ("cost_basis", "计价基准")]),
                  "### 时间模型", "", "、".join(context["duration_models"]) or "（无）", "",
                  "### 当前草稿仍缺少", ""]
        parts += [f"- {item['message']}" for item in context["gaps"]] or ["- （当前没有草稿，从了解工艺开始）"]
        parts += [""]
    if task in EXAMPLES:
        parts += ["## 示例", "", "```json", _json(EXAMPLES[task]), "```", ""]
    parts += ["## 输出格式（JSON Schema）", "", "```json", _json(schema), "```", ""]
    return "\n".join(parts)
