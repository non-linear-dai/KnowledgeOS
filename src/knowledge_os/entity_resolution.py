"""Deterministic, evidence-bound resolution of extracted instance mentions."""
from __future__ import annotations

import json
import re
import unicodedata

from .core import ValidationError


def norm(value):
    return " ".join(unicodedata.normalize("NFKC", str(value or "")).casefold().split())


def mentioned(value, text):
    value, text = norm(value), norm(text)
    if len(value) < 2:
        return False
    if any("\u3400" <= char <= "\u9fff" for char in value):
        return value in text
    return bool(re.search(r"(?<!\w)" + re.escape(value) + r"(?!\w)", text))


class EntityResolver:
    def __init__(self, db):
        self.db = db

    def _rows(self, typ):
        return self.db.execute("""SELECT id,type,natural_key,key_namespace,label,aliases_json,lifecycle,
          source_class,source_path,revision FROM node WHERE type=? AND lifecycle NOT IN ('merged','retired') ORDER BY id""", (typ,))

    @staticmethod
    def _public(row):
        return {key: row[key] for key in ("id", "type", "natural_key", "key_namespace", "label", "lifecycle",
                                            "source_class", "source_path", "revision")}

    def hints(self, source, limit=500):
        text = " ".join(segment["text"] for segment in source["segments"])
        result = []
        for row in self.db.execute("""SELECT id,type,natural_key,key_namespace,label,aliases_json,lifecycle,
          source_class,source_path,revision FROM node WHERE lifecycle NOT IN ('merged','retired') ORDER BY id"""):
            names = [row["id"], row["natural_key"], row["label"], *json.loads(row["aliases_json"])]
            if any(mentioned(name, text) for name in names):
                result.append({**self._public(row), "aliases": json.loads(row["aliases_json"])})
                if len(result) >= limit:
                    break
        return result

    def resolve(self, entity, source):
        typ = entity["type"]
        quotes = " ".join(item["quote"] for item in entity["evidence"])
        query_names = [entity["label"], *entity.get("aliases", [])]
        key, namespace, explicit = norm(entity.get("key")), entity.get("key_namespace"), entity.get("existing_id")
        context_ids = entity.get("context_ids") or []
        if not isinstance(context_ids, list) or any(not isinstance(value, str) for value in context_ids):
            raise ValidationError("context_ids must be an array of entity IDs")
        if context_ids:
            for context_id in context_ids:
                context = self.db.first("SELECT id,natural_key,label,aliases_json FROM node WHERE id=?", (context_id,))
                if not context:
                    raise ValidationError(f"context entity not found: {context_id}")
                identities = [context["id"], context["natural_key"], context["label"], *json.loads(context["aliases_json"])]
                if not any(mentioned(value, quotes) for value in identities):
                    raise ValidationError(f"context entity lacks cited evidence: {context_id}")
        binding_context = json.dumps(sorted(context_ids), separators=(",", ":"))
        bound = []
        for name in [*query_names, entity.get("key")]:
            if not mentioned(name, quotes):
                continue
            binding = self.db.first("""SELECT node_id FROM durable.source_entity_binding
              WHERE source_id=? AND entity_type=? AND mention=? AND context_json=?""",
              (source["id"], typ, norm(name), binding_context))
            if binding and binding["node_id"] not in bound:
                bound.append(binding["node_id"])
        if bound:
            rows = [self.db.first("""SELECT id,type,natural_key,key_namespace,label,aliases_json,lifecycle,
              source_class,source_path,revision FROM node WHERE id=? AND type=? AND lifecycle NOT IN ('merged','retired')""",
              (identifier, typ)) for identifier in bound]
            choices = [{**self._public(row), "aliases": json.loads(row["aliases_json"]), "rank": 110,
                        "reasons": ["confirmed_source_binding"]} for row in rows if row]
            bound_context_valid = len(choices) == 1 and all(self.db.first(
                "SELECT 1 FROM edge WHERE (src=? AND dst=?) OR (src=? AND dst=?) LIMIT 1",
                (choices[0]["id"], context_id, context_id, choices[0]["id"])) for context_id in context_ids)
            if bound_context_valid and (not explicit or explicit == choices[0]["id"]) and (
                not key or key == norm(choices[0]["natural_key"])):
                return {"status": "matched", "target": choices[0], "reason": "confirmed_source_binding", "candidates": choices}
            return {"status": "needs_resolution", "reason": "conflicting or stale source binding", "candidates": choices}
        candidates = []
        for row in self._rows(typ):
            aliases = json.loads(row["aliases_json"])
            identities = [row["id"], row["natural_key"], row["label"], *aliases]
            if not any(mentioned(value, quotes) for value in identities):
                continue
            reasons = []
            rank = 0
            if explicit == row["id"] and mentioned(row["id"], quotes):
                rank, reasons = 100, ["cited_canonical_id"]
            if key and key == norm(row["natural_key"]) and (namespace is None or namespace == row["key_namespace"]):
                rank, reasons = max(rank, 90 if namespace is not None else 80), reasons + ["scoped_business_key" if namespace is not None else "business_key"]
            if any(norm(name) == norm(row["label"]) for name in query_names):
                rank, reasons = max(rank, 70), reasons + ["exact_label"]
            if any(norm(name) == norm(alias) for name in query_names for alias in aliases):
                rank, reasons = max(rank, 70), reasons + ["exact_alias"]
            if any(norm(name) == norm(row["natural_key"]) for name in query_names):
                rank, reasons = max(rank, 80), reasons + ["business_key"]
            if rank == 0 and any(norm(name) in norm(row["label"]) or norm(row["label"]) in norm(name)
                                 for name in query_names if len(norm(name)) >= 3):
                rank, reasons = 30, ["partial_label"]
            if not rank:
                continue
            if context_ids:
                connected = all(self.db.first("SELECT 1 FROM edge WHERE (src=? AND dst=?) OR (src=? AND dst=?) LIMIT 1",
                                              (row["id"], context_id, context_id, row["id"])) for context_id in context_ids)
                if connected:
                    rank += 20
                    reasons.append("linked_context")
                else:
                    reasons.append("context_not_linked")
            candidates.append({**self._public(row), "aliases": aliases, "rank": rank, "reasons": reasons})
        candidates.sort(key=lambda item: (-item["rank"], item["id"]))
        if explicit and not self.db.first("SELECT id FROM node WHERE id=? AND type=?", (explicit, typ)):
            raise ValidationError("existing_id not found or type mismatch")
        if explicit and not any(item["id"] == explicit for item in candidates):
            return {"status": "needs_resolution", "reason": "existing_id lacks matching source evidence", "candidates": candidates[:20]}
        if candidates:
            best = candidates[0]
            ties = [item for item in candidates if item["rank"] == best["rank"]]
            key_matches = [item for item in candidates if "business_key" in item["reasons"] or "scoped_business_key" in item["reasons"]]
            name_matches = [item for item in candidates if "exact_label" in item["reasons"] or "exact_alias" in item["reasons"]]
            if (key and key_matches and (best not in key_matches or
                any(item["id"] not in {key_item["id"] for key_item in key_matches} for item in name_matches))) or (
                context_ids and "context_not_linked" in best["reasons"]):
                return {"status": "needs_resolution", "reason": "identity anchors conflict", "candidates": candidates[:20]}
            if best["rank"] >= 70 and len(ties) == 1 and (not explicit or explicit == best["id"]):
                return {"status": "matched", "target": best, "reason": ",".join(best["reasons"]), "candidates": candidates[:20]}
            return {"status": "needs_resolution", "reason": "ambiguous or weak identity evidence", "candidates": candidates[:20]}
        if explicit:
            return {"status": "needs_resolution", "reason": "existing_id has no supported match", "candidates": []}
        if not any(mentioned(name, quotes) for name in [entity["label"], entity.get("key")]):
            return {"status": "needs_resolution", "reason": "new identity is absent from cited evidence", "candidates": []}
        return {"status": "new", "reason": "no existing candidate found", "candidates": []}
