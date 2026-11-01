"""Standard prompt rendering for extraction protocol v2.

Renders an ``extraction_contract`` into Markdown instructions organized by the stages of
the portable ``document-instance-extraction`` Skill. Every Agent platform then puts the
same business rules into its prompts instead of writing its own contract-to-prompt code.

The renderer reads only the contract it is given (nothing from the registry), so the text
always matches exactly what the Agent received, and it is deterministic: the same contract
yields the same Markdown and the same ``content_hash``.
"""
from __future__ import annotations

import hashlib

FORMAT = "knowledgeos.extraction-instructions.v1"
RENDERER_VERSION = "1.0.0"
STAGES = ("all", "route", "mentions", "consolidate", "fill_fields", "relations", "rule_signals", "precheck_and_repair")

TYPE_LABELS = {"string": "短文本", "text": "文本", "enum": "枚举", "number": "数值", "decimal": "十进制数", "boolean": "是/否",
               "date": "日期", "datetime": "日期时间", "quantity": "数量", "currency": "金额", "node_ref": "实例引用", "object": "结构化对象"}
SELECTOR_LABELS = {"heading": "章节标题", "section_path": "章节路径", "table_header": "表头或行标签", "key_value_label": "键值标签", "speaker": "发言人"}
ROLE_LABELS = {"instance_label": "实例名称", "instance_key": "实例编码", "field": "属性取值", "relation": "关系"}
SLOT_LABELS = {"business_constraint": "业务约束", "business_rule": "匹配规则", "decision_table": "决策表", "model": "计算模型"}


def _label(labels, fallback=""):
    labels = labels or {}
    return labels.get("zh") or labels.get("en") or next(iter(labels.values()), None) or fallback


def _name(item):
    return f"{_label(item.get('labels'), item.get('id') or item.get('predicate'))}（`{item.get('id') or item.get('predicate')}`）"


def _quote(text):
    return "「" + " ".join(str(text).split()) + "」"


def _join(values):
    return "、".join(str(value) for value in values if str(value).strip())


def _card_lines(card, indent=""):
    lines = []
    definition = card.get("definition") or {}
    if definition.get("zh") or definition.get("en"):
        lines.append(f"{indent}- 定义：{definition.get('zh') or definition.get('en')}")
    if card.get("recognition_cues"):
        lines.append(f"{indent}- 识别线索：{_join(card['recognition_cues'])}")
    for key, title in (("positive_examples", "正例（应抽取）"), ("negative_examples", "反例（不应抽取）")):
        if card.get(key):
            lines.append(f"{indent}- {title}：")
            for example in card[key]:
                note = f" —— {example['note']}" if example.get("note") else ""
                lines.append(f"{indent}  - {_quote(example['text'])}{note}")
    if card.get("boundary_notes"):
        lines.append(f"{indent}- 边界说明：")
        lines.extend(f"{indent}  - {note}" for note in card["boundary_notes"])
    if card.get("typical_locations"):
        lines.append(f"{indent}- 常见位置：{_join(card['typical_locations'])}")
    return lines


def _mentions(concept):
    lines = [f"### {_name(concept)}"]
    terms = concept.get("terms") or {}
    names = list(dict.fromkeys([*((terms.get("pref") or {}).values()), *(terms.get("alt") or [])]))
    if names:
        lines.append(f"- 文档中的叫法：{_join(names)}")
    lines.extend(_card_lines(concept.get("card") or {}))
    if len(lines) == 1:
        lines.append("- 尚未配置抽取说明卡；只在原文明确指向具体对象时才抽取。")
    return "\n".join(lines)


def _consolidate(concept, lexicon_counts):
    lexicon_count = lexicon_counts.get(concept["id"], 0)
    identity = concept.get("identity") or {}
    lines = [f"### {_name(concept)}"]
    for key in identity.get("keys") or []:
        if key.get("source") == "predicate":
            source = f"属性 `{key.get('predicate')}` 的取值"
        else:
            source = "业务编码（填入 `identity.key`）" + (f"，命名空间 `{key['namespace']}`" if key.get("namespace") else "")
        patterns = "；".join(f"`{item['pattern']}`" + (f"（{item['description']}）" if item.get("description") else "")
                            for item in key.get("patterns") or [])
        lines.append(f"- 身份键：{source}" + (f"，编码模式 {patterns}" if patterns else ""))
    if identity.get("minimum_fields"):
        lines.append(f"- 新建实例至少需要字段：{_join(f'`{item}`' for item in identity['minimum_fields'])}；缺少时不要提交该新实例。")
    if lexicon_count:
        lines.append(f"- 契约词典中有 {lexicon_count} 个已有实例（`lexicon`）；`node_hint` 只能取自词典或 `resolve` 结果。")
    if len(lines) == 1:
        lines.append("- 未配置身份键：只用原文中的名称和编码作为身份线索，不要自行编造实例 ID。")
    return "\n".join(lines)


def _value_rule(prop):
    value = prop.get("value") or {}
    typ = value.get("type")
    parts = [TYPE_LABELS.get(typ, typ or "未知类型")]
    if value.get("cardinality") in ("many", "temporal_many"):
        parts.append("可多值" if value["cardinality"] == "many" else "可随时间有多条")
    if value.get("units"):
        parts.append("允许单位 " + "/".join(value["units"]))
    elif value.get("default_unit"):
        parts.append(f"默认单位 {value['default_unit']}")
    if value.get("allowed"):
        parts.append("可选值 " + "/".join(value["allowed"]))
    return "，".join(parts)


def _fill_fields(concept):
    lines = [f"### {_name(concept)}"]
    for prop in concept.get("properties") or []:
        terms = prop.get("terms") or {}
        names = list(dict.fromkeys([*((terms.get("pref") or {}).values()), *(terms.get("alt") or [])]))
        title = _label(prop.get("labels"), prop["predicate"])
        lines.append(f"- **{title}**（`{prop['predicate']}`）")
        if names:
            lines.append(f"  - 文档中的叫法：{_join(names)}")
        lines.append(f"  - 取值：{_value_rule(prop)}")
        value = prop.get("value") or {}
        if value.get("unit_aliases"):
            lines.append("  - 单位写法：" + "；".join(f"{alias} → {unit}" for alias, unit in value["unit_aliases"].items())
                         + "（只抄原文写法，换算由 KnowledgeOS 完成）")
        if value.get("enum_map"):
            lines.append("  - 原文说法：" + "；".join(f"{alias} → {target}" for alias, target in value["enum_map"].items()))
        if prop.get("value_patterns"):
            lines.append("  - 值模式：" + "；".join(f"`{item['pattern']}`" + (f"（{item['description']}）" if item.get("description") else "")
                                                   for item in prop["value_patterns"]))
        requirements = []
        if prop.get("required_for_publish"):
            requirements.append("发布必填，缺失时把实例标为 draft")
        if prop.get("temporal_required"):
            requirements.append("需要时间：有原文时间就填 `temporal`，没有则由 KnowledgeOS 按文档日期补齐")
        if prop.get("evidence_required"):
            requirements.append("必须给出取值证据")
        if requirements:
            lines.append("  - 要求：" + "；".join(requirements))
        if prop.get("publication_route") == "upstream_source_system":
            lines.append("  - 写入方式：由业务系统维护。照常抽取并提交，KnowledgeOS 只登记为待核对事项，不会写入知识库。")
        card_lines = _card_lines(prop.get("card") or {}, "  ")
        if card_lines:
            lines.append("  - 属性说明：")
            lines.extend("  " + line for line in card_lines)
    if len(lines) == 1:
        lines.append("- 该概念没有绑定属性；只抽取实例本身。")
    return "\n".join(lines)


def render(contract):
    """Render a contract (dict) into the standard instructions object."""
    concepts = contract.get("concepts") or []
    names = {item["id"]: _name(item) for item in concepts}
    lexicon_counts = {}
    for entry in (contract.get("lexicon") or {}).get("entries") or []:
        lexicon_counts[entry["concept"]] = lexicon_counts.get(entry["concept"], 0) + 1
    acceptance, limits = contract.get("acceptance") or {}, contract.get("limits") or {}
    sections = []

    def add(identifier, stage, title, body):
        sections.append({"id": identifier, "stage": stage, "title": title, "markdown": f"## {title}\n\n{body.strip()}\n"})

    scope = [f"- 只抽取以下概念：{_join(names.values())}。",
             "- 只使用这些概念绑定的属性和下文列出的关系；其他事实写入 `unmapped_facts` 并选择合适的 `reason`。",
             "- `value.raw` 必须逐字复制原文，不做换算、改写、翻译或补全；单位、币种、日期、枚举的规范化由 KnowledgeOS 完成。",
             "- 每个字段、关系和规则信号都要有自己的证据锚点；没有证据的内容不要提交。"]
    add("scope", "all", "抽取范围与总要求", "\n".join(scope))
    recipes = contract.get("recipes") or []
    if recipes:
        body = []
        for recipe in recipes:
            match = recipe.get("match") or {}
            header = f"- **{recipe['id']}** v{recipe.get('version', '')}：适用文档类型 {_join(recipe.get('document_types') or [])}"
            if match.get("title_patterns"):
                header += "；标题匹配 " + "、".join(f"`{item}`" for item in match["title_patterns"])
            if match.get("source_kinds"):
                header += "；来源类型 " + _join(match["source_kinds"])
            body.append(header)
            for mapping in recipe.get("mappings") or []:
                selector = mapping.get("selector") or {}
                target = names.get(mapping["concept"], mapping["concept"])
                detail = f"`{mapping['predicate']}`" if mapping.get("predicate") else f"关系 `{mapping['relation']}`" if mapping.get("relation") else ROLE_LABELS.get(mapping["role"], mapping["role"])
                body.append(f"  - {SELECTOR_LABELS.get(selector.get('kind'), selector.get('kind'))} 匹配 `{selector.get('pattern')}` → {target} 的{detail}")
        add("route", "route", "文档类型与抽取方案", "先用标题与来源类型匹配方案；匹配上时，按下列位置优先查找对应概念和属性。\n\n" + "\n".join(body))
    add("mentions", "mentions", "识别概念", "找出属于以下概念的具体对象。泛指、类别或要求性表述不是实例。\n\n" + "\n\n".join(_mentions(item) for item in concepts))
    add("consolidate", "consolidate", "合并实例与身份", "把指向同一对象的提及（包括“该公司”“上述设备”等指代）合并为一个实例，分配本次提交内唯一的 `local_id`。是否为已有实例由 KnowledgeOS 判定。\n\n"
        + "\n\n".join(_consolidate(item, lexicon_counts) for item in concepts))
    add("fill_fields", "fill_fields", "填写字段", "逐个属性在实例提及附近、方案映射的位置和属性叫法命中的位置查找取值。表格取值同时给出表头锚点（role=header 或 label）和取值锚点（role=value）。\n\n"
        + "\n\n".join(_fill_fields(item) for item in concepts))
    relations = contract.get("relations") or []
    if relations:
        lines = []
        for relation in relations:
            ends = "；".join(f"{names.get(edge['source_type'], edge['source_type'])} → {names.get(edge['target_type'], edge['target_type'])}" for edge in relation.get("endpoints") or [])
            lines.append(f"- {_name(relation)}：{ends}")
        add("relations", "relations", "关系", "只使用以下关系并遵守方向；两端可以是本次的新实例（`local_id`）或已有实例（`node_id`）。证据要能看出关系本身，并用 subject、object、context 角色标注。\n\n" + "\n".join(lines))
    slots = contract.get("rule_slots") or []
    if slots:
        lines = [f"- `{slot['logic_ref']}` {_label(slot.get('label'))}（{SLOT_LABELS.get(slot['kind'], slot['kind'])}；主体 {names.get(slot['subject_concept'], slot['subject_concept'])}；属性 {_join(f'`{item}`' for item in slot['predicates'])}）"
                 + (f"：{slot['summary']}" if slot.get("summary") else "") for slot in slots]
        add("rule_signals", "rule_signals", "规则信号", "遇到“不得低于”“必须”“超过……则……”等规范性条款，记录为 `rule_signals`（条款原文、主体概念、属性、算子原文、取值原文、适用范围原文）。能对应时填 `logic_ref_hint`。不要起草或修改逻辑。\n\n已发布逻辑：\n" + "\n".join(lines))
    checks = [f"- 引文匹配：{'精确' if acceptance.get('quote_match') == 'exact' else '允许全角与空白差异，但偏移仍按原文计算'}；`quote` 必须等于块文本 `text[start:end]`，偏移按 Unicode 码点计算，用代码计算，不要让模型生成。"]
    if acceptance.get("min_confidence"):
        checks.append(f"- 置信度低于 {acceptance['min_confidence']} 的项不会进入提案。")
    checks.append("- 缺少发布必填字段时把实例标为 `completeness: draft`。" if acceptance.get("draft_instances") else "- 缺少发布必填字段的实例会被拒绝。")
    if acceptance.get("relations_between_new_instances") is False:
        checks.append("- 不允许在两个新实例之间建立关系。")
    if limits:
        checks.append("- 上限：" + "；".join(f"{key} {value}" for key, value in limits.items()))
    checks.append("- 先以 `mode: precheck` 提交，按验收报告修复后再提案；`CONTRACT_STALE` 表示规则已更新，需要重新获取契约。")
    add("acceptance", "precheck_and_repair", "验收要求", "\n".join(checks))

    header = (f"# KnowledgeOS 抽取说明\n\n> 由 KnowledgeOS 根据已发布的本体与抽取说明卡生成（契约 `{contract.get('contract_id', '')}`）。"
              "本说明与结构化契约一致；标识符请原样使用，不要翻译或改写。\n")
    markdown = header + "\n" + "\n".join(section["markdown"] for section in sections)
    per_concept = {item["id"]: {"mentions": _mentions(item), "consolidate": _consolidate(item, lexicon_counts), "fill_fields": _fill_fields(item)}
                   for item in concepts}
    return {"format": FORMAT, "renderer_version": RENDERER_VERSION, "language": "zh",
            "content_hash": hashlib.sha256(markdown.encode("utf-8")).hexdigest(),
            "markdown": markdown, "sections": sections, "concepts": per_concept}
