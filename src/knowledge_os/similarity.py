"""Deterministic near-duplicate detection for ontology definitions.

Before a new predicate or concept is registered, compare it with what already
exists so that people (and Agents) reuse a definition instead of creating a
second name for the same thing. Names are gathered from every place the
control plane records them: labels, stable ids, extraction-card terms
(preferred and alternative names per concept property) and vocabulary terms.

Scores are character-bigram Dice coefficients over NFKC-folded names, which
works for Chinese and Latin text alike. Matching value type and dimension
raises confidence; a close name with a different dimension is reported as a
conflict rather than a reuse candidate, because reusing it would mix units.
"""
from __future__ import annotations

import re
import unicodedata

LIKELY = 0.85
SIMILAR = 0.6
LIMIT = 5


def fold(text) -> str:
    value = unicodedata.normalize("NFKC", str(text or "")).casefold()
    return re.sub(r"[\s_\-·.,，。:：;；/\\()（）\[\]【】\"'“”‘’]+", "", value)


def _grams(text):
    value = fold(text)
    if len(value) < 2:
        return {value} if value else set()
    return {value[index:index + 2] for index in range(len(value) - 1)}


def dice(left, right) -> float:
    a, b = fold(left), fold(right)
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    ga, gb = _grams(a), _grams(b)
    if not ga or not gb:
        return 0.0
    score = 2 * len(ga & gb) / (len(ga) + len(gb))
    if len(a) >= 2 and len(b) >= 2 and (a in b or b in a):
        score = max(score, 0.75 * min(len(a), len(b)) / max(len(a), len(b)) + 0.25)
    return round(score, 4)


def _id_words(identifier):
    return " ".join(part for part in re.split(r"[_\-]+", str(identifier or "")) if part)


def predicate_names(registry, cards=None):
    """predicate id -> {name: origin}."""
    names = {}
    for identifier, predicate in registry.predicates.items():
        entry = names.setdefault(identifier, {})
        entry[_id_words(identifier)] = "标识"
        if predicate.get("label"):
            entry[predicate["label"]] = "名称"
    for card in (cards or {}).values():
        for identifier, prop in ((card or {}).get("properties") or {}).items():
            if identifier not in names:
                continue
            terms = (prop or {}).get("terms") or {}
            for value in ((terms.get("pref") or {}).values()):
                names[identifier].setdefault(value, "抽取说明卡常用名")
            for value in terms.get("alt") or []:
                names[identifier].setdefault(value, "抽取说明卡其他叫法")
    return names


def concept_names(registry, cards=None):
    names = {}
    for concept in registry.ontology.get("concept_types", []):
        entry = names.setdefault(concept["id"], {})
        entry[_id_words(concept["id"])] = "标识"
        if concept.get("label"):
            entry[concept["label"]] = "名称"
    for vocabulary in registry.vocabularies.values():
        for term in vocabulary.get("terms", []):
            ref = term.get("concept_ref")
            if ref not in names:
                continue
            for value in (term.get("pref_labels") or {}).values():
                names[ref].setdefault(value, "业务术语")
            for values in (term.get("alt_labels") or {}).values():
                for value in values:
                    names[ref].setdefault(value, "业务术语其他叫法")
    for concept_id, card in (cards or {}).items():
        if concept_id not in names:
            continue
        terms = (card or {}).get("terms") or {}
        for value in (terms.get("pref") or {}).values():
            names[concept_id].setdefault(value, "抽取说明卡常用名")
        for value in terms.get("alt") or []:
            names[concept_id].setdefault(value, "抽取说明卡其他叫法")
    return names


def _best(candidate_names, existing_names):
    best = (0.0, None, None)
    for proposed in candidate_names:
        for name, origin in existing_names.items():
            score = dice(proposed, name)
            if score > best[0]:
                best = (score, proposed, {"name": name, "origin": origin})
    return best


def similar_predicates(registry, proposal, cards=None, exclude=None):
    """Existing predicates resembling a proposed one, strongest first.

    proposal: {"id", "label", "aliases": [...], "value_type", "dimension"}
    """
    proposed = [value for value in [proposal.get("label"), _id_words(proposal.get("id")), *(proposal.get("aliases") or [])] if value]
    results = []
    for identifier, names in predicate_names(registry, cards).items():
        if identifier == exclude or identifier == proposal.get("id"):
            continue
        score, matched, existing = _best(proposed, names)
        if score < SIMILAR:
            continue
        value = registry.predicates[identifier].get("value", {})
        same_type = not proposal.get("value_type") or proposal.get("value_type") == value.get("type")
        same_dimension = (proposal.get("dimension") or None) == (value.get("dimension") or None)
        if same_type and same_dimension:
            verdict = "reuse" if score >= LIKELY else "similar"
        elif value.get("dimension") and proposal.get("dimension") and not same_dimension:
            verdict = "dimension_conflict"
        else:
            verdict = "similar"
        results.append({"id": identifier, "label": registry.predicates[identifier].get("label", identifier),
                        "score": score, "verdict": verdict, "matched_name": matched, "existing_name": existing,
                        "value_type": value.get("type"), "dimension": value.get("dimension"), "units": value.get("units")})
    results.sort(key=lambda item: (-item["score"], item["id"]))
    return results[:LIMIT]


def similar_concepts(registry, proposal, cards=None, exclude=None):
    proposed = [value for value in [proposal.get("label"), _id_words(proposal.get("id")), *(proposal.get("aliases") or [])] if value]
    results = []
    for identifier, names in concept_names(registry, cards).items():
        if identifier == exclude or identifier == proposal.get("id"):
            continue
        score, matched, existing = _best(proposed, names)
        if score < SIMILAR:
            continue
        label = next((item.get("label", identifier) for item in registry.ontology.get("concept_types", []) if item["id"] == identifier), identifier)
        results.append({"id": identifier, "label": label, "score": score, "verdict": "reuse" if score >= LIKELY else "similar",
                        "matched_name": matched, "existing_name": existing})
    results.sort(key=lambda item: (-item["score"], item["id"]))
    return results[:LIMIT]
