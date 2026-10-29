"""Feedback loop for extraction protocol v2.

Aggregates recorded proposal submissions (prechecks are read-only and not recorded) into
signals that tell business users which extraction card to change, and measures the effect
of each published configuration by comparing registry fingerprints.

Suggestions are recomputed on every call: a suggestion is "resolved" once the current
cards already handle it (for example the unit alias now normalizes), so no extra state is
kept and nothing here writes. Applying a suggestion only edits a Studio draft; the change
still goes through the governed ChangeSet flow.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime

from .core import ValidationError
from .normalization import NormalizationError, nfkc, parse_number_expression

CARD_CODES = {"UNIT_NOT_ALLOWED", "ENUM_NOT_ALLOWED", "VALUE_UNPARSEABLE"}
OK_FIELDS = ("accepted", "already_present", "conflict", "upstream_required")
INSTANCE_OUTCOMES = ("matched", "new", "draft", "needs_resolution", "upstream_required", "rejected")
SAMPLES = 3


def _key(*parts):
    return hashlib.sha256(json.dumps(parts, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()[:16]


def _fold(text):
    return " ".join(nfkc(text).casefold().split())


class Feedback:
    def __init__(self, engine):
        self.engine = engine
        self.db = engine.db
        self.registry = engine.registry

    def _rows(self, since, until):
        sql, binds = "SELECT * FROM durable.extraction_submission WHERE 1=1", []
        for value, operator in ((since, ">="), (until, "<")):
            if value:
                try:
                    datetime.fromisoformat(str(value).replace("Z", "+00:00"))
                except ValueError as exc:
                    raise ValidationError("since/until must be ISO 8601 timestamps") from exc
                sql += f" AND recorded_at {operator} ?"
                binds.append(str(value))
        return self.db.execute(sql + " ORDER BY recorded_at, submission_id", binds)

    def _blocks(self, cache, document_id, version):
        key = (document_id, version)
        if key not in cache:
            row = self.db.first("SELECT blocks_json FROM durable.extraction_source WHERE document_id=? AND document_version=?", (document_id, version))
            cache[key] = {block["id"]: block["text"] for block in json.loads(row["blocks_json"])} if row else {}
        return cache[key]

    def _concept_label(self, concept_id):
        concept = self.engine.concepts.get(concept_id) or {}
        pref, _ = self.engine._terms(concept_id) if concept_id in self.engine.concepts else ({}, [])
        return pref.get("zh") or concept.get("label") or concept_id

    def _predicate_label(self, concept_id, predicate_id):
        entry = ((self.engine.cards.get(concept_id) or {}).get("properties") or {}).get(predicate_id) or {}
        pref = (entry.get("terms") or {}).get("pref") or {}
        return pref.get("zh") or self.registry.predicates.get(predicate_id, {}).get("label") or predicate_id

    def _known_terms(self, concept_id):
        """Every name the current configuration recognizes for a concept and its predicates."""
        pref, alt = self.engine._terms(concept_id)
        names = {_fold(value) for value in [*pref.values(), *alt]}
        properties = ((self.engine.cards.get(concept_id) or {}).get("properties") or {})
        for binding in self.engine.concepts.get(concept_id, {}).get("properties", []):
            predicate = self.registry.predicates[binding["predicate"]]
            entry = properties.get(binding["predicate"]) or {}
            terms = entry.get("terms") or {}
            names |= {_fold(value) for value in [predicate.get("label") or "", binding["predicate"], *(terms.get("pref") or {}).values(), *(terms.get("alt") or [])] if value}
        return names

    def _normalizes(self, concept_id, predicate_id, value):
        try:
            self.engine.normalizer.normalize(predicate_id, value, card=self.engine.cards.get(concept_id))
            return True
        except NormalizationError:
            return False

    def _unit_text(self, concept_id, predicate_id, value):
        if value.get("unit_raw"):
            return nfkc(value["unit_raw"]).strip()
        definition = self.registry.predicates[predicate_id].get("value", {})
        if definition.get("type") == "currency":
            return nfkc(value.get("raw", "")).strip()
        words = list(self.engine.normalizer.unit_aliases(predicate_id, self.engine.cards.get(concept_id))) + list(self.registry.units)
        try:
            return parse_number_expression(value.get("raw", ""), words).unit_text or nfkc(value.get("raw", "")).strip()
        except NormalizationError:
            return nfkc(value.get("raw", "")).strip()

    def build(self, concept=None, since=None, until=None):
        if concept and concept not in self.engine.concepts:
            raise ValidationError(f"unknown concept: {concept}")
        rows = self._rows(since, until)
        cache = {}
        suggestions, concepts, versions, documents = {}, {}, {}, set()
        current = self.registry.fingerprint

        def concept_stats(concept_id):
            return concepts.setdefault(concept_id, {"concept": concept_id, "label": self._concept_label(concept_id),
                                                    "instances": {key: 0 for key in INSTANCE_OUTCOMES},
                                                    "fields": {key: 0 for key in (*OK_FIELDS, "rejected")},
                                                    "issues": {}, "submissions": 0})

        def suggest(kind, concept_id, predicate_id, text, sample, **extra):
            identifier = _key(kind, concept_id, predicate_id, _fold(text))
            item = suggestions.setdefault(identifier, {"id": identifier, "kind": kind, "concept": concept_id, "concept_label": self._concept_label(concept_id) if concept_id else None,
                                                       "predicate": predicate_id, "predicate_label": self._predicate_label(concept_id, predicate_id) if predicate_id and concept_id else None,
                                                       "text": text, "count": 0, "documents": set(), "samples": [], "last_seen": None, **extra})
            item["count"] += 1
            item["documents"].add(sample["document_id"])
            item["last_seen"] = max(item["last_seen"] or "", sample["recorded_at"])
            if len(item["samples"]) < SAMPLES and all(existing["block_id"] != sample["block_id"] or existing["document_id"] != sample["document_id"] for existing in item["samples"]):
                item["samples"].append({key: value for key, value in sample.items() if key != "recorded_at"})
            for key, value in extra.items():
                if isinstance(value, set):
                    item[key] = item.get(key, set()) | value

        for row in rows:
            submission, report = json.loads(row["submission_json"]), json.loads(row["report_json"])
            blocks = self._blocks(cache, row["document_id"], row["document_version"])
            documents.add(row["document_id"])
            version = versions.setdefault(row["registry_fingerprint"], {"registry_fingerprint": row["registry_fingerprint"], "first_seen": row["recorded_at"],
                                                                        "last_seen": row["recorded_at"], "submissions": 0, "documents": set(),
                                                                        "fields": 0, "fields_ok": 0, "normalization_failures": 0,
                                                                        "uncovered_cues": 0, "unmapped_facts": 0, "needs_resolution": 0})
            version["submissions"] += 1
            version["documents"].add(row["document_id"])
            version["last_seen"] = row["recorded_at"]
            base = {"document_id": row["document_id"], "document_version": row["document_version"], "submission_id": row["submission_id"], "recorded_at": row["recorded_at"]}

            def sample_for(anchors, base=base, blocks=blocks):
                anchor = next((item for item in anchors if item.get("block_id") in blocks), None)
                if not anchor:
                    return {**base, "block_id": None, "text": ""}
                return {**base, "block_id": anchor["block_id"], "text": blocks[anchor["block_id"]][:400]}

            instances = {item["local_id"]: item for item in submission.get("instances") or []}
            seen_concepts = set()
            for result in report.get("instances") or []:
                item = instances.get(result["local_id"])
                if not item:
                    continue
                concept_id = item["concept"]
                if concept and concept_id != concept:
                    continue
                stats = concept_stats(concept_id)
                seen_concepts.add(concept_id)
                stats["instances"][result["outcome"]] = stats["instances"].get(result["outcome"], 0) + 1
                for issue in result.get("issues") or []:
                    stats["issues"][issue["code"]] = stats["issues"].get(issue["code"], 0) + 1
                if result["outcome"] == "needs_resolution":
                    version["needs_resolution"] += 1
                    suggest("identity_rule", concept_id, None, item["label"]["raw"], sample_for(item["label"].get("evidence") or []),
                            reason=(result.get("resolution") or {}).get("reason", ""))
                for field_result in result.get("fields") or []:
                    outcome = field_result["outcome"]
                    stats["fields"][outcome if outcome in stats["fields"] else "rejected"] += 1
                    version["fields"] += 1
                    version["fields_ok"] += outcome in OK_FIELDS
                    codes = [issue["code"] for issue in field_result.get("issues") or []]
                    for code in codes:
                        stats["issues"][code] = stats["issues"].get(code, 0) + 1
                    card_codes = [code for code in codes if code in CARD_CODES]
                    if outcome != "rejected" or not card_codes:
                        continue
                    version["normalization_failures"] += 1
                    index = field_result["field_index"]
                    fields = item.get("fields") or []
                    if index >= len(fields):
                        continue
                    field = fields[index]
                    predicate_id, value = field["predicate"], field["value"]
                    if predicate_id not in self.registry.predicates:
                        continue
                    sample = sample_for(field.get("evidence") or [])
                    code = card_codes[0]
                    if code == "UNIT_NOT_ALLOWED":
                        text = self._unit_text(concept_id, predicate_id, value)
                        kind = "currency_alias" if self.registry.predicates[predicate_id].get("value", {}).get("type") == "currency" else "unit_alias"
                        suggest(kind, concept_id, predicate_id, text, sample, raw_values={value.get("raw", "")})
                    elif code == "ENUM_NOT_ALLOWED":
                        suggest("enum_alias", concept_id, predicate_id, nfkc(value.get("raw", "")).strip(), sample)
                    else:
                        suggest("value_format", concept_id, predicate_id, nfkc(value.get("raw", "")).strip(), sample)
            for concept_id in seen_concepts:
                concepts[concept_id]["submissions"] += 1
            for fact in submission.get("unmapped_facts") or []:
                concept_id = fact.get("suggested_concept") if fact.get("suggested_concept") in self.engine.concepts else None
                if concept and concept_id != concept:
                    continue
                version["unmapped_facts"] += 1
                label = (fact.get("suggested_property_label") or "").strip()
                kind = "property_term" if concept_id and label and fact.get("reason") == "no_matching_predicate" else "unmapped_fact"
                suggest(kind, concept_id, None, label if kind == "property_term" else fact["text"][:200], sample_for(fact.get("evidence") or []),
                        reasons={fact.get("reason", "other")}, facts={fact["text"][:200]})
            for block in (report.get("coverage") or {}).get("uncovered_cue_blocks") or []:
                for cue in block.get("cues") or []:
                    concept_id = cue.get("concept")
                    if concept and concept_id != concept:
                        continue
                    if cue["type"] not in ("term", "recipe", "value_pattern", "unit"):
                        continue
                    version["uncovered_cues"] += 1
                    text = blocks.get(block["block_id"], "")
                    suggest("uncovered_cue", concept_id, cue.get("predicate"), cue.get("match", ""),
                            {**base, "block_id": block["block_id"], "text": text[:400]}, cue_types={cue["type"]})

        output = []
        for item in suggestions.values():
            item["documents"] = len(item["documents"])
            for key in ("raw_values", "reasons", "facts", "cue_types"):
                if key in item:
                    item[key] = sorted(item[key])[:5]
            item["status"], item["action"], item["advice"] = self._resolve(item)
            output.append(item)
        output.sort(key=lambda item: (item["status"] != "open", -item["count"], -item["documents"], item["kind"], item["text"]))
        version_list = []
        for index, item in enumerate(sorted(versions.values(), key=lambda value: value["first_seen"])):
            item["documents"] = len(item["documents"])
            item["field_acceptance_rate"] = round(item["fields_ok"] / item["fields"], 4) if item["fields"] else None
            item["label"] = f"配置版本 {index + 1}"
            item["current"] = item["registry_fingerprint"] == current
            version_list.append(item)
        return {"registry_fingerprint": current, "scope": {"concept": concept, "since": since, "until": until},
                "source": "proposal submissions (prechecks are not recorded)",
                "totals": {"submissions": len(rows), "documents": len(documents),
                           "open_suggestions": sum(item["status"] == "open" for item in output)},
                "concepts": sorted(concepts.values(), key=lambda item: item["concept"]),
                "versions": version_list, "suggestions": output[:300]}

    def _resolve(self, item):
        """(status, action, advice) against the current configuration."""
        kind, concept_id, predicate_id, text = item["kind"], item["concept"], item["predicate"], item["text"]
        definition = self.registry.predicates.get(predicate_id, {}).get("value", {}) if predicate_id else {}
        bound = predicate_id and concept_id in self.engine.concepts and any(binding["predicate"] == predicate_id for binding in self.engine.concepts[concept_id].get("properties", []))
        if kind in ("unit_alias", "currency_alias", "enum_alias"):
            if not bound:
                return "obsolete", None, "该属性已不再绑定到此概念。"
            raw_values = item.get("raw_values") or [text]
            if all(self._normalizes(concept_id, predicate_id, {"raw": raw}) for raw in raw_values if raw):
                return "resolved", None, "当前配置已能规范化这些写法。"
            if kind == "unit_alias":
                targets = definition.get("units") or ([definition["default_unit"]] if definition.get("default_unit") else [])
                if not targets and definition.get("dimension"):
                    targets = [unit for unit, value in self.registry.units.items() if value.get("dimension") == definition["dimension"]]
                return "open", {"target": "card", "field": "unit_aliases", "choices": targets}, f"原文单位写法「{text}」无法识别。如果它对应已注册单位，在说明卡中加入单位写法；否则说明原文使用了未注册单位，需要先在控制面注册。"
            if kind == "currency_alias":
                return "open", {"target": "profile", "field": "currency_aliases", "choices": sorted(self.registry.currencies)}, f"金额写法「{text}」没有可识别的币种。若其中包含某种币种写法，在全局配置中加入币种写法。"
            return "open", {"target": "card", "field": "enum_map", "choices": list(definition.get("allowed") or [])}, f"原文说法「{text}」不在可选值中。若它等同某个可选值，在说明卡中加入枚举写法。"
        if kind == "value_format":
            return ("obsolete", None, "该属性已不再绑定到此概念。") if not bound else (
                "open", {"target": "card", "field": "card", "tab": "properties"}, f"取值「{text}」无法解析。检查属性说明与值模式，提示 Agent 只抄取值本身；若原文确实是区间或描述，考虑改写为断言或未映射事实。")
        if kind == "property_term":
            if concept_id and _fold(text) in self._known_terms(concept_id):
                return "resolved", None, "该叫法已出现在当前说明卡或术语中。"
            choices = [binding["predicate"] for binding in self.engine.concepts.get(concept_id, {}).get("properties", [])]
            return "open", {"target": "card", "field": "terms", "choices": choices}, f"Agent 报告「{text}」没有对应属性。若它是某个已有属性的另一种叫法，把它加入该属性的叫法；否则需要在本体白板注册新属性并绑定到该概念。"
        if kind == "identity_rule":
            return "open", {"target": "card", "field": "identity", "tab": "identity"}, "身份无法唯一确定。为该概念配置身份键与编码模式，或在说明卡中提示 Agent 记录原文中的编码。"
        if kind == "uncovered_cue":
            return "open", {"target": "card", "field": "card", "tab": "overview" if not predicate_id else "properties"}, (
                f"线索「{text}」多次出现却没有被抽取。若这是泛指或不应抽取的场景，把它写进反例或边界说明；若叫法过于宽泛，考虑从叫法中移除；若确实是漏抽，补充识别说明与正例。")
        return "open", None, "Agent 报告了无法映射的事实。若反复出现，考虑在本体中补充概念或属性。"
