"""Extraction protocol v2: contract generation, source registration and server-side acceptance.

KnowledgeOS owns the contract, the acceptance rules and the durable result; external
Agents own parsing and extraction. See docs/extraction-v2.md for the protocol and
control/schemas/extraction-v2.schema.json for the message structure.

Nothing here calls a model. Acceptance is deterministic: anchors must be exact slices of
registered block text, every field carries its own evidence, values are normalized
here, identities are resolved by the evidence-bound resolver, and durable changes are
only ever ChangeSets.
"""
from __future__ import annotations

import base64
import binascii
import copy
import hashlib
import json
import re
import unicodedata
from decimal import Decimal, InvalidOperation
from pathlib import Path

import jsonschema

from .core import ConflictError, NotFoundError, ValidationError, digest, frontmatter, json_text, load_yaml, utcnow
from .entity_resolution import EntityResolver, mentioned, norm
from .normalization import NormalizationError, ValueNormalizer, nfkc, parse_time, reference_date, timestamp
from .values import validate_value

PROTOCOL = "knowledgeos.extraction.v2"
PROFILE_FORMAT = "knowledgeos.extraction-v2-profile.v1"
CARD_FORMAT = "knowledgeos.extraction-card.v1"
RECIPE_FORMAT = "knowledgeos.extraction-recipe.v1"

DEFAULT_PROFILE = {
    "format": PROFILE_FORMAT, "id": "extraction_v2", "version": "1.0.0", "protocol_version": PROTOCOL,
    "default_utc_offset": "+00:00", "excluded_concepts": [],
    "limits": {"max_blocks": 20000, "max_instances": 500, "max_fields_per_instance": 200,
               "max_anchors_per_field": 12, "max_quote_chars": 2000},
    "acceptance": {"quote_match": "exact", "evidence_level": "field", "draft_instances": True,
                   "relations_between_new_instances": True, "min_confidence": 0.0},
    "lexicon": {"default_limit": 1000, "max_limit": 10000},
    "identity_prefixes": {}, "source_authority": {"default": "research", "systems": {}},
    "unit_aliases": {}, "currency_aliases": {},
}
MESSAGE_DEFS = {"contract_request": "ContractRequest", "extraction_contract": "ExtractionContract",
                "source_document": "SourceDocument", "source_receipt": "SourceReceipt",
                "extraction_submission": "ExtractionSubmission", "validation_report": "ValidationReport"}
OPERATORS = [
    (("不得低于", "不应低于", "不低于", "不少于", "不小于", "至少", "最少", "≥", ">="), "gte"),
    (("不得高于", "不应高于", "不得超过", "不应超过", "不超过", "不高于", "不大于", "至多", "最多", "≤", "<="), "lte"),
    (("不等于", "不得为", "≠", "!="), "ne"),
    (("超过", "高于", "大于", "多于", ">"), "gt"),
    (("低于", "少于", "小于", "<"), "lt"),
    (("等于", "为", "是", "="), "eq"),
]
BOUND_OPERATORS = {"lower": "gte", "upper": "lte", "lower_exclusive": "gt", "upper_exclusive": "lt"}
OPERATOR_SYMBOLS = {"gte": "≥", "lte": "≤", "gt": ">", "lt": "<", "eq": "=", "ne": "≠"}


# ---------------------------------------------------------------------------
# Control assets


def _protocol_validator(registry, definition):
    schema = registry.schemas.get("extraction-v2")
    if not schema:
        raise ValidationError("control/schemas/extraction-v2.schema.json is required for extraction protocol v2")
    return jsonschema.Draft202012Validator({"$ref": f"#/$defs/{definition}", "$defs": schema["$defs"]},
                                           format_checker=jsonschema.FormatChecker())


def _schema_errors(validator, value):
    errors = sorted(validator.iter_errors(value), key=lambda error: [str(part) for part in error.absolute_path])
    return ["/" + "/".join(str(part) for part in error.absolute_path) + ": " + error.message for error in errors[:5]]


def _pattern(text, where):
    try:
        re.compile(text)
    except re.error as exc:
        raise ValidationError(f"{where}: invalid pattern {text!r}: {exc}") from exc


class _Drift:
    """Collects reference drift; strict mode (new proposals) turns drift into errors."""

    def __init__(self, registry, strict):
        self.registry, self.strict, self.warnings = registry, strict, []

    def __call__(self, location, message):
        location = str(location)
        path = Path(location)
        if path.is_absolute():
            try:
                location = path.relative_to(self.registry.config.root).as_posix()
            except ValueError:
                pass
        if self.strict:
            raise ValidationError(f"{location}: {message.split('; ')[0]}")
        self.warnings.append(f"{location}: {message}")


def _validators(registry):
    defs = registry.schemas["extraction-v2"]["$defs"]
    return {"card": _protocol_validator(registry, "ExtractionCard"), "pattern": _protocol_validator(registry, "ValuePattern"),
            "recipe": _protocol_validator(registry, "Recipe"),
            "identity": jsonschema.Draft202012Validator({"$ref": "#/$defs/ConceptContract/properties/identity", "$defs": defs}),
            "terms": jsonschema.Draft202012Validator({"$ref": "#/$defs/PropertyContract/properties/terms", "$defs": defs})}


def _check_profile(registry, loaded, where, drift):
    profile = copy.deepcopy(DEFAULT_PROFILE)
    for key, value in (loaded or {}).items():
        if isinstance(value, dict) and isinstance(profile.get(key), dict):
            profile[key] = {**profile[key], **value}
        else:
            profile[key] = value
    concepts = {item["id"] for item in registry.ontology.get("concept_types", [])}
    allowed = set(DEFAULT_PROFILE) | {"_path"}
    if set(profile) - allowed:
        raise ValidationError(f"{where}: unsupported profile keys {sorted(set(profile) - allowed)}")
    if profile.get("format") != PROFILE_FORMAT or profile.get("protocol_version") != PROTOCOL:
        raise ValidationError(f"{where}: extraction v2 profile must use {PROFILE_FORMAT} and {PROTOCOL}")
    if not re.fullmatch(r"\d+\.\d+\.\d+", str(profile.get("version", ""))):
        raise ValidationError(f"{where}: version must be a semantic version")
    if not re.fullmatch(r"[+-]\d{2}:\d{2}", str(profile.get("default_utc_offset", ""))):
        raise ValidationError(f"{where}: default_utc_offset must look like +08:00")
    excluded = []
    for concept in profile.get("excluded_concepts") or []:
        if concept in concepts:
            excluded.append(concept)
        else:
            drift(where, f"excluded concept {concept} is not registered; ignored")
    profile["excluded_concepts"] = excluded
    for key, value in profile["limits"].items():
        if key not in DEFAULT_PROFILE["limits"] or not isinstance(value, int) or isinstance(value, bool) or value < 1:
            raise ValidationError(f"{where}: invalid limit {key}")
    acceptance = profile["acceptance"]
    if set(acceptance) - set(DEFAULT_PROFILE["acceptance"]):
        raise ValidationError(f"{where}: unsupported acceptance keys")
    if acceptance.get("quote_match") not in ("exact", "normalized_exact") or acceptance.get("evidence_level") != "field":
        raise ValidationError(f"{where}: acceptance.quote_match must be exact or normalized_exact and evidence_level field")
    for key in ("draft_instances", "relations_between_new_instances"):
        if not isinstance(acceptance.get(key), bool):
            raise ValidationError(f"{where}: acceptance.{key} must be true or false")
    minimum = acceptance.get("min_confidence", 0)
    if isinstance(minimum, bool) or not isinstance(minimum, (int, float)) or not 0 <= minimum <= 1:
        raise ValidationError(f"{where}: acceptance.min_confidence must be between 0 and 1")
    prefixes = {}
    for concept, prefix in (profile.get("identity_prefixes") or {}).items():
        if not re.fullmatch(r"[a-z][a-z0-9_-]*", str(prefix)):
            raise ValidationError(f"{where}: invalid identity prefix for {concept}")
        if concept in concepts:
            prefixes[concept] = prefix
        else:
            drift(where, f"identity prefix for unregistered concept {concept} ignored")
    profile["identity_prefixes"] = prefixes
    for key, known in (("unit_aliases", registry.units), ("currency_aliases", registry.currencies)):
        if not isinstance(profile.get(key) or {}, dict):
            raise ValidationError(f"{where}: {key} must be a mapping")
        kept = {}
        for alias, target in (profile.get(key) or {}).items():
            if not str(alias).strip():
                raise ValidationError(f"{where}: {key} contains an empty alias")
            if target in known:
                kept[str(alias)] = target
            else:
                drift(where, f"{key} {alias} -> {target} is not registered; ignored")
        profile[key] = kept
    authority = profile.get("source_authority") or {}
    if not isinstance(authority.get("systems", {}), dict) or not str(authority.get("default") or ""):
        raise ValidationError(f"{where}: source_authority requires default and systems")
    return profile


def _check_card(registry, item, where, drift, profile, validators):
    concepts = {entry["id"]: entry for entry in registry.ontology.get("concept_types", [])}
    concept = item.get("concept")
    if item.get("format") != CARD_FORMAT or not isinstance(concept, str):
        raise ValidationError(f"{where}: extraction card needs {CARD_FORMAT} and a concept")
    allowed_keys = {"format", "concept", "card_version", "card", "terms", "identity", "properties"}
    if set(item) - allowed_keys:
        raise ValidationError(f"{where}: unsupported keys {sorted(set(item) - allowed_keys)}")
    if not re.fullmatch(r"\d+\.\d+\.\d+", str(item.get("card_version", ""))):
        raise ValidationError(f"{where}: card_version must be a semantic version")
    card = dict(item.get("card") or {})
    card["card_version"] = str(item["card_version"])
    for message in _schema_errors(validators["card"], card) + _schema_errors(validators["terms"], item.get("terms") or {}):
        raise ValidationError(f"{where}: {message}")
    identity = copy.deepcopy(item.get("identity") or {})
    for message in _schema_errors(validators["identity"], identity):
        raise ValidationError(f"{where}: identity{message}")
    properties = item.get("properties") or {}
    if not isinstance(properties, dict):
        raise ValidationError(f"{where}: properties must be keyed by predicate")
    for key in identity.get("keys", []):
        for pattern in key.get("patterns", []):
            _pattern(pattern["pattern"], where)
    for predicate_id, entry in properties.items():
        if not isinstance(entry, dict) or set(entry) - {"terms", "unit_aliases", "enum_map", "value_patterns", "card"}:
            raise ValidationError(f"{where}: invalid property card {predicate_id}")
        for message in _schema_errors(validators["terms"], entry.get("terms") or {}) + _schema_errors(validators["card"], entry.get("card") or {}):
            raise ValidationError(f"{where}: {predicate_id}{message}")
        for pattern in entry.get("value_patterns") or []:
            for message in _schema_errors(validators["pattern"], pattern):
                raise ValidationError(f"{where}: {predicate_id} value_patterns{message}")
            _pattern(pattern["pattern"], where)
        for key in ("unit_aliases", "enum_map"):
            if not isinstance(entry.get(key) or {}, dict) or any(not str(alias).strip() or not isinstance(value, str)
                                                                 for alias, value in (entry.get(key) or {}).items()):
                raise ValidationError(f"{where}: {predicate_id}.{key} must map non-empty text to identifiers")
    if concept not in concepts:
        drift(where, f"concept {concept} is not registered; card ignored")
        return None
    if concept in profile["excluded_concepts"]:
        drift(where, f"concept {concept} is excluded from extraction; card ignored")
        return None
    bindings = {binding["predicate"] for binding in concepts[concept].get("properties", [])}
    keys = []
    for key in identity.get("keys", []):
        if key["source"] == "predicate" and key.get("predicate") not in bindings:
            drift(where, f"identity key predicate {key.get('predicate')} is not bound to {concept}; ignored")
        else:
            keys.append(key)
    if "keys" in identity:
        identity["keys"] = keys
    if "minimum_fields" in identity:
        stale = [value for value in identity["minimum_fields"] if value not in bindings]
        if stale:
            drift(where, f"minimum_fields {stale} are not bound to {concept}; ignored")
        identity["minimum_fields"] = [value for value in identity["minimum_fields"] if value in bindings]
    sanitized = {}
    for predicate_id, entry in properties.items():
        if predicate_id not in bindings:
            drift(where, f"{predicate_id} is not bound to {concept}; property card ignored")
            continue
        entry = copy.deepcopy(entry)
        definition = registry.predicates[predicate_id].get("value", {})
        units = definition.get("units") or []
        aliases = {}
        for alias, unit in (entry.get("unit_aliases") or {}).items():
            dimension = definition.get("dimension")
            valid = definition.get("type") == "quantity" and (
                unit in units or unit == definition.get("default_unit") or not units and not dimension or (
                    dimension and unit in registry.units and registry.units[unit]["dimension"] == dimension))
            if valid:
                aliases[alias] = unit
            else:
                drift(where, f"unit alias {alias} -> {unit} is not allowed for {predicate_id}; ignored")
        if "unit_aliases" in entry:
            entry["unit_aliases"] = aliases
        mapping = {}
        for alias, value in (entry.get("enum_map") or {}).items():
            if definition.get("type") == "enum" and value in (definition.get("allowed") or []):
                mapping[alias] = value
            else:
                drift(where, f"enum_map {alias} -> {value} is not allowed for {predicate_id}; ignored")
        if "enum_map" in entry:
            entry["enum_map"] = mapping
        sanitized[predicate_id] = entry
    return {**item, "identity": identity, "properties": sanitized}


def _check_recipe(registry, item, where, drift, validators):
    concepts = {entry["id"]: entry for entry in registry.ontology.get("concept_types", [])}
    relations = {entry["id"] for entry in registry.ontology.get("relation_types", [])}
    body = {key: value for key, value in item.items() if key != "format"}
    if item.get("format") != RECIPE_FORMAT:
        raise ValidationError(f"{where}: recipe needs {RECIPE_FORMAT}")
    for message in _schema_errors(validators["recipe"], body):
        raise ValidationError(f"{where}: {message}")
    if not re.fullmatch(r"\d+\.\d+\.\d+", str(body.get("version", ""))):
        raise ValidationError(f"{where}: version must be a semantic version")
    for pattern in (body.get("match") or {}).get("title_patterns", []):
        _pattern(pattern, where)
    mappings = []
    for mapping in body["mappings"]:
        _pattern(mapping["selector"]["pattern"], where)
        concept = mapping["concept"]
        if concept not in concepts:
            drift(where, f"mapping concept {concept} is not registered; mapping ignored")
            continue
        bindings = {binding["predicate"] for binding in concepts[concept].get("properties", [])}
        if mapping["role"] == "field" and mapping.get("predicate") not in bindings:
            drift(where, f"field mapping predicate {mapping.get('predicate')} is not bound to {concept}; mapping ignored")
            continue
        if mapping["role"] == "relation" and mapping.get("relation") not in relations:
            drift(where, f"relation mapping {mapping.get('relation')} is not registered; mapping ignored")
            continue
        mappings.append(mapping)
    if not mappings:
        drift(where, "recipe has no valid mappings; ignored")
        return None
    return {**body, "mappings": mappings}


def load_assets(registry):
    """Load and validate control/extraction/v2 (profile, extraction cards, recipes).

    Structural mistakes (wrong format, schema violations, invalid patterns) fail the
    registry load. References that drift when the ontology, units or currencies change
    (an unbound predicate, a removed concept, a narrowed unit list) never block control
    plane edits: the stale entry is dropped and reported in ``warnings``.
    """
    root = registry.config.control / "extraction" / "v2"
    drift = _Drift(registry, strict=False)
    profile_path = root / "profile.yaml"
    profile = _check_profile(registry, load_yaml(profile_path) if profile_path.exists() else None, str(profile_path), drift)
    if profile_path.exists():
        profile["_path"] = str(profile_path)
    validators = _validators(registry)
    cards, recipes = {}, {}
    for path in sorted(root.glob("cards/*.yaml")):
        item = load_yaml(path)
        if item.get("concept") in cards or path.stem != item.get("concept"):
            raise ValidationError(f"{path}: card file name must equal its unique concept id")
        checked = _check_card(registry, item, str(path), drift, profile, validators)
        if checked:
            cards[checked["concept"]] = {**checked, "_path": str(path)}
    for path in sorted(root.glob("recipes/*.yaml")):
        item = load_yaml(path)
        if item.get("id") in recipes or path.stem != item.get("id"):
            raise ValidationError(f"{path}: recipe file name must equal its unique id")
        checked = _check_recipe(registry, item, str(path), drift, validators)
        if checked:
            recipes[checked["id"]] = {**checked, "_path": str(path)}
    return {"profile": profile, "cards": cards, "recipes": recipes, "warnings": drift.warnings}


ASSET_KINDS = ("profile", "card", "recipe")


def asset_path(kind, document):
    """Git-authored path of an extraction asset document."""
    if kind == "profile":
        return "control/extraction/v2/profile.yaml"
    key = document.get("concept") if kind == "card" else document.get("id") if kind == "recipe" else None
    if kind not in ASSET_KINDS or not isinstance(key, str) or not re.fullmatch(r"[a-z][a-z0-9_]*", key):
        raise ValidationError("extraction asset requires kind profile, card or recipe and a stable lowercase id")
    return f"control/extraction/v2/{'cards' if kind == 'card' else 'recipes'}/{key}.yaml"


def validate_asset(registry, kind, document):
    """Strictly validate one proposed asset: structural errors and reference drift both fail."""
    if not isinstance(document, dict):
        raise ValidationError("extraction asset document must be an object")
    path = asset_path(kind, document)
    drift = _Drift(registry, strict=True)
    if kind == "profile":
        return _check_profile(registry, document, path, drift)
    profile = registry.extraction_v2["profile"]
    validators = _validators(registry)
    if kind == "card":
        return _check_card(registry, document, path, drift, profile, validators)
    return _check_recipe(registry, document, path, drift, validators)


def _version_key(value):
    return tuple(int(part) for part in str(value).split("."))


def asset_version(kind, document):
    return str(document.get("card_version") if kind == "card" else document.get("version"))


def public(value):
    if isinstance(value, dict):
        return {key: public(item) for key, item in value.items() if not str(key).startswith("_")}
    if isinstance(value, list):
        return [public(item) for item in value]
    return value


def _issue(code, severity, message, path=None, block_id=None, details=None):
    item = {"code": code, "severity": severity, "message": str(message)[:2000] or code}
    if path is not None:
        item["path"] = path
    if block_id:
        item["block_id"] = block_id
    if details:
        item["details"] = details
    return item


def _errors(issues):
    return any(item["severity"] == "error" for item in issues)


def _collapse(text):
    """NFKC + whitespace folding, returning (folded_text, folded_index -> original_index)."""
    folded, mapping, in_space = [], [], False
    for index, char in enumerate(str(text)):
        for piece in unicodedata.normalize("NFKC", char):
            if piece.isspace():
                if in_space or not folded:
                    in_space = True
                    continue
                folded.append(" ")
                mapping.append(index)
                in_space = True
            else:
                folded.append(piece)
                mapping.append(index)
                in_space = False
    while folded and folded[-1] == " ":
        folded.pop()
        mapping.pop()
    return "".join(folded), mapping


def _fold(text):
    return _collapse(text)[0]


# ---------------------------------------------------------------------------
# Service


class ExtractionV2:
    def __init__(self, service):
        self.service = service
        self.registry = service.registry
        self.db = service.db
        assets = self.registry.extraction_v2
        self.profile, self.cards, self.recipes = assets["profile"], assets["cards"], assets["recipes"]
        self.normalizer = ValueNormalizer(self.registry, self.profile)
        self.concepts = {item["id"]: item for item in self.registry.ontology.get("concept_types", [])}
        self.relations = {item["id"]: item for item in self.registry.ontology.get("relation_types", [])}

    # -- helpers -------------------------------------------------------------

    def check_message(self, message, message_type):
        if not isinstance(message, dict):
            raise ValidationError("extraction v2 message must be a JSON object")
        if message.get("protocol_version") != PROTOCOL:
            raise ValidationError(f"PROTOCOL_UNSUPPORTED: protocol_version must be {PROTOCOL}")
        if message.get("message_type") != message_type:
            raise ValidationError(f"expected message_type {message_type}")
        errors = _schema_errors(_protocol_validator(self.registry, MESSAGE_DEFS[message_type]), message)
        if errors:
            raise ValidationError(f"{message_type} does not match the extraction v2 schema: " + "; ".join(errors))

    def extractable(self):
        excluded = set(self.profile.get("excluded_concepts") or [])
        return [identifier for identifier in self.concepts if identifier not in excluded]

    def scope_concepts(self, scope):
        from .domain_scope import domain_scope
        concepts = set(self.extractable())
        if scope.get("domains"):
            selected = set()
            for domain in scope["domains"]:
                selected |= set(domain_scope(self.registry, domain)["concept"])
            concepts &= selected
        if scope.get("concepts"):
            unknown = [item for item in scope["concepts"] if item not in self.concepts]
            if unknown:
                raise ValidationError(f"unknown concepts in contract scope: {', '.join(unknown)}")
            excluded = [item for item in scope["concepts"] if item not in concepts and item in self.profile.get("excluded_concepts", [])]
            if excluded:
                raise ValidationError(f"concepts are not open to document extraction: {', '.join(excluded)}")
            concepts &= set(scope["concepts"])
        return sorted(concepts, key=list(self.concepts).index)

    def connector_only(self, predicate_id):
        policy = self.registry.predicates[predicate_id].get("policy", {})
        authority = self.authority_policy(predicate_id)
        return "git_authored" not in authority.get("primary", []) and policy.get("history") != "git_only"

    def authority_policy(self, predicate_id):
        policy = self.registry.predicates[predicate_id].get("policy", {})
        return next((item for item in self.registry.policies.get("authority", {}).get("authority_policies", [])
                     if item["id"] == policy.get("authority")), {})

    def source_authority(self, document):
        authority = self.profile.get("source_authority") or {}
        system = (document.get("origin") or {}).get("system")
        if not system:
            return None
        return (authority.get("systems") or {}).get(system, authority.get("default", "research"))

    def _terms(self, concept_id):
        from .semantic_contract import SemanticContract
        pref, alt = {}, []
        for term in SemanticContract(self.registry).terminology(concept_id):
            pref.update({key: value for key, value in (term.get("pref_labels") or {}).items() if value})
            for values in (term.get("alt_labels") or {}).values():
                alt.extend(values if isinstance(values, list) else [values])
        card_terms = (self.cards.get(concept_id) or {}).get("terms") or {}
        pref.update(card_terms.get("pref") or {})
        alt.extend(card_terms.get("alt") or [])
        return pref, list(dict.fromkeys(item for item in alt if item))

    @staticmethod
    def _languages(text, languages):
        if not languages:
            return text
        allowed = {item.split("-")[0] for item in languages}
        return {key: value for key, value in text.items() if key.split("-")[0] in allowed} or text

    # -- contract ------------------------------------------------------------

    def property_contract(self, concept_id, binding, languages=None):
        predicate_id = binding["predicate"]
        predicate = self.registry.predicates[predicate_id]
        definition = predicate.get("value", {})
        entry = ((self.cards.get(concept_id) or {}).get("properties") or {}).get(predicate_id) or {}
        typ = definition.get("type")
        value = {"type": "object" if typ == "json" else typ}
        for key in ("cardinality", "dimension", "units", "default_unit", "allowed"):
            if definition.get(key) not in (None, [], ""):
                value[key] = copy.deepcopy(definition[key])
        if typ == "quantity":
            allowed = set(definition.get("units") or []) | ({definition["default_unit"]} if definition.get("default_unit") else set())
            dimension = definition.get("dimension")
            aliases = {alias: unit for alias, unit in (self.profile.get("unit_aliases") or {}).items()
                       if unit in allowed or dimension and self.registry.units.get(unit, {}).get("dimension") == dimension}
            aliases.update(entry.get("unit_aliases") or {})
            if aliases:
                value["unit_aliases"] = aliases
        if typ == "currency":
            allowed = set(definition.get("units") or [])
            aliases = {alias: code for alias, code in (self.profile.get("currency_aliases") or {}).items() if not allowed or code in allowed}
            if aliases:
                value["unit_aliases"] = aliases
        if entry.get("enum_map"):
            value["enum_map"] = dict(entry["enum_map"])
        policy = predicate.get("policy", {})
        result = {"predicate": predicate_id, "labels": self._languages({"en": predicate.get("label") or predicate_id}, languages),
                  "value": value, "storage": predicate.get("storage", {}).get("mode", "attr"),
                  "required_for_publish": bool(binding.get("required")),
                  "temporal_required": policy.get("temporal") == "required",
                  "evidence_required": policy.get("evidence") == "required",
                  "publication_route": "upstream_source_system" if self.connector_only(predicate_id) else "governed_changeset"}
        terms = entry.get("terms") or {}
        if terms:
            result["terms"] = {key: (self._languages(item, languages) if key == "pref" else item) for key, item in terms.items()}
            result["labels"] = self._languages({**result["labels"], **(terms.get("pref") or {})}, languages)
        if entry.get("value_patterns"):
            result["value_patterns"] = copy.deepcopy(entry["value_patterns"])
        if entry.get("card"):
            result["card"] = copy.deepcopy(entry["card"])
        return result

    def concept_contract(self, concept_id, languages=None):
        from .semantic_contract import SemanticContract
        concept = self.concepts[concept_id]
        card = self.cards.get(concept_id) or {}
        pref, alt = self._terms(concept_id)
        labels = self._languages({"en": concept.get("label") or concept_id, **pref}, languages)
        result = {"id": concept_id, "iri": str(SemanticContract(self.registry).iri("concept", concept_id)),
                  "labels": labels, "terms": {"pref": labels, "alt": alt},
                  "identity": copy.deepcopy(card.get("identity") or {"keys": [{"source": "natural_key"}], "minimum_fields": []}),
                  "properties": [self.property_contract(concept_id, binding, languages) for binding in concept.get("properties", [])],
                  "relation_ids": sorted(relation["id"] for relation in self.relations.values()
                                         if any(concept_id in (edge["source_type"], edge["target_type"]) for edge in relation.get("connections", [])))}
        if not result["terms"]["alt"]:
            del result["terms"]["alt"]
        if card.get("card"):
            result["card"] = {**copy.deepcopy(card["card"]), **({"card_version": str(card["card_version"])} if card.get("card_version") else {})}
        return result

    def rule_slots(self, concepts):
        slots = []
        for kind, prefix, definitions in (("business_constraint", "constraint", self.registry.constraints),
                                          ("business_rule", "rule", self.registry.rules)):
            for identifier, definition in sorted(definitions.items()):
                expected = "knowledgeos.business-constraint.v1" if kind == "business_constraint" else "knowledgeos.business-rule.v1"
                lifecycle = definition.get("status", {}).get("lifecycle")
                subject = definition.get("scope", {}).get("subject_concept")
                if definition.get("format") != expected or lifecycle not in ("active", "draft") or subject not in concepts:
                    continue
                predicates = list(dict.fromkeys(item["predicate"] for item in definition.get("inputs", [])))
                checks = []
                inputs = {item["id"]: item for item in definition.get("inputs", [])}
                for check in definition.get("checks", []):
                    right = check.get("right", {})
                    rhs = right.get("input") if "input" in right else json_text(right.get("value"))
                    checks.append(f"{inputs.get(check['left'], {}).get('predicate', check['left'])} {OPERATOR_SYMBOLS.get(check['operator'], check['operator'])} {rhs}")
                slots.append({"logic_ref": f"{prefix}:{identifier}@{definition.get('version')}", "kind": kind,
                              "label": {"zh": definition.get("label") or identifier}, "subject_concept": subject,
                              "predicates": predicates,
                              "summary": f"状态：{lifecycle}；" + "；".join(checks)})
        return slots

    def contract(self, request):
        self.check_message(request, "contract_request")
        scope = request.get("scope") or {}
        concepts = self.scope_concepts(scope)
        if not concepts:
            raise ValidationError("contract scope contains no extractable concepts")
        include = set(request.get("include") or ["concepts", "relations", "recipes", "rule_slots"]) | {"concepts"}
        languages = request.get("languages")
        lexicon_request = request.get("lexicon") or {}
        scope_key = {"concepts": concepts, "domains": sorted(scope.get("domains") or []),
                     "document_type": scope.get("document_type"), "source_kind": scope.get("source_kind"),
                     "include": sorted(include), "languages": languages or []}
        fingerprint = self.registry.fingerprint
        contract_id = "contract:" + digest([PROTOCOL, fingerprint, scope_key])[:24]
        message = {"protocol_version": PROTOCOL, "message_type": "extraction_contract", "contract_id": contract_id,
                   "registry_fingerprint": fingerprint, "issued_at": utcnow(),
                   "scope": {key: value for key, value in (("domains", scope_key["domains"]), ("concepts", concepts)) if value},
                   "limits": dict(self.profile["limits"]),
                   "acceptance": dict(self.profile["acceptance"]),
                   "concepts": [self.concept_contract(identifier, languages) for identifier in concepts]}
        if "relations" in include:
            relations = []
            for relation in self.relations.values():
                endpoints = [{"source_type": edge["source_type"], "target_type": edge["target_type"]}
                             for edge in relation.get("connections", []) if edge["source_type"] in concepts and edge["target_type"] in concepts]
                if endpoints:
                    relations.append({"id": relation["id"], "labels": self._languages({"en": relation.get("label") or relation["id"]}, languages),
                                      "mode": "reified" if relation.get("mode") == "reified" else "simple", "endpoints": endpoints})
            message["relations"] = relations
        if "recipes" in include:
            message["recipes"] = self.select_recipes(scope.get("document_type"), scope.get("source_kind"), concepts)
        if "rule_slots" in include:
            message["rule_slots"] = self.rule_slots(concepts)
        if "lexicon" in include:
            message["lexicon"] = self.lexicon(concepts, lexicon_request.get("limit"), lexicon_request.get("cursor"))
        with self.db.transaction():
            self.db.write("""INSERT OR IGNORE INTO durable.extraction_contract(id,registry_fingerprint,scope_json,issued_at)
              VALUES(?,?,?,?)""", (contract_id, fingerprint, json_text(scope_key), message["issued_at"]))
        return message

    def select_recipes(self, document_type, source_kind, concepts):
        result = []
        for recipe in self.recipes.values():
            if document_type and document_type not in recipe["document_types"]:
                continue
            kinds = (recipe.get("match") or {}).get("source_kinds")
            if source_kind and kinds and source_kind not in kinds:
                continue
            mappings = [item for item in recipe["mappings"] if item["concept"] in concepts]
            if mappings:
                result.append({**public({key: value for key, value in recipe.items()}), "mappings": mappings})
        return result

    def lexicon(self, concepts, limit=None, cursor=None):
        settings = self.profile.get("lexicon") or {}
        limit = min(int(limit or settings.get("default_limit", 1000)), int(settings.get("max_limit", 10000)))
        offset = 0
        if cursor:
            match = re.fullmatch(r"o:(\d+)", str(cursor))
            if not match:
                raise ValidationError("invalid lexicon cursor")
            offset = int(match.group(1))
        placeholders = ",".join("?" for _ in concepts)
        rows = self.db.execute(f"""SELECT id,type,natural_key,label,aliases_json FROM node
          WHERE type IN ({placeholders}) AND lifecycle NOT IN ('merged','retired') ORDER BY id LIMIT ? OFFSET ?""",
                               (*concepts, limit + 1, offset))
        entries = []
        for row in rows[:limit]:
            if not re.fullmatch(r"[a-z][a-z0-9_-]*:[^\s]+", row["id"]) or len(row["id"]) > 256:
                continue
            names = list(dict.fromkeys(name for name in [row["label"], *json.loads(row["aliases_json"])] if name))
            entry = {"node_id": row["id"], "concept": row["type"], "names": names or [row["id"]]}
            if row["natural_key"]:
                entry["keys"] = [row["natural_key"]]
            entries.append(entry)
        return {"entries": entries, "next_cursor": f"o:{offset + limit}" if len(rows) > limit else None}

    # -- sources -------------------------------------------------------------

    def _block_issues(self, message):
        blocks = message["blocks"]
        issues = []
        limit = self.profile["limits"]["max_blocks"]
        if len(blocks) > limit:
            issues.append(_issue("LIMIT_EXCEEDED", "error", f"块数量 {len(blocks)} 超过契约上限 {limit}", "/blocks"))
        ids, orders = {}, {}
        for index, block in enumerate(blocks):
            if block["id"] in ids:
                issues.append(_issue("BLOCK_INVALID", "error", f"块 ID {block['id']} 重复", f"/blocks/{index}/id", block["id"]))
            ids.setdefault(block["id"], block)
            if block["order"] in orders:
                issues.append(_issue("BLOCK_INVALID", "error", f"阅读顺序 {block['order']} 重复", f"/blocks/{index}/order", block["id"]))
            orders.setdefault(block["order"], block["id"])
        pages = {page["number"] for page in message.get("pages") or []}
        for index, block in enumerate(blocks):
            path = f"/blocks/{index}"
            parent = block.get("parent_id")
            if parent is not None and (parent not in ids or parent == block["id"]):
                issues.append(_issue("BLOCK_INVALID", "error", f"parent_id {parent} 不存在", path + "/parent_id", block["id"]))
            seen, cursor = {block["id"]}, parent
            while cursor in ids:
                if cursor in seen:
                    issues.append(_issue("BLOCK_INVALID", "error", "parent_id 形成循环", path + "/parent_id", block["id"]))
                    break
                seen.add(cursor)
                cursor = ids[cursor].get("parent_id")
            if block["type"] == "table_cell":
                table = block.get("table")
                if not table or ids.get(table["table_id"], {}).get("type") != "table":
                    issues.append(_issue("BLOCK_INVALID", "error", "table_cell 必须通过 table.table_id 指向 table 块", path + "/table", block["id"]))
                elif parent is not None and parent != table["table_id"]:
                    issues.append(_issue("BLOCK_INVALID", "error", "table_cell 的 parent_id 必须等于 table.table_id", path + "/parent_id", block["id"]))
            elif block.get("table"):
                issues.append(_issue("BLOCK_INVALID", "error", "只有 table_cell 可以带 table", path + "/table", block["id"]))
            if block.get("time") and block["time"]["end_ms"] < block["time"]["start_ms"]:
                issues.append(_issue("BLOCK_INVALID", "error", "time.end_ms 早于 start_ms", path + "/time", block["id"]))
            if pages and block.get("page") is not None and block["page"] not in pages:
                issues.append(_issue("BLOCK_INVALID", "error", f"page {block['page']} 不在 pages 中", path + "/page", block["id"]))
        return issues

    @staticmethod
    def _diff(old_blocks, new_blocks):
        def key(block):
            return (hashlib.sha256(block["text"].encode()).hexdigest(), tuple(block.get("heading_path") or []), block["type"])
        old_pool = {}
        for block in old_blocks:
            old_pool.setdefault(key(block), []).append(block)
        unmatched_new, matched_old = [], set()
        for block in sorted(new_blocks, key=lambda item: item["order"]):
            candidates = [item for item in old_pool.get(key(block), []) if item["id"] not in matched_old]
            if candidates:
                matched_old.add(candidates[0]["id"])
            else:
                unmatched_new.append(block)
        remaining_old = [block for block in sorted(old_blocks, key=lambda item: item["order"]) if block["id"] not in matched_old]
        changed, added = [], []
        by_id = {block["id"]: block for block in remaining_old}
        used = set()
        for block in unmatched_new:
            partner = by_id.get(block["id"]) if block["id"] not in used and block["id"] in by_id and by_id[block["id"]]["type"] == block["type"] else None
            if partner is None:
                partner = next((item for item in remaining_old if item["id"] not in used and item["type"] == block["type"]
                                and (item.get("heading_path") or []) == (block.get("heading_path") or [])
                                and item.get("parent_id") == block.get("parent_id")
                                and item.get("table", {}).get("row") == block.get("table", {}).get("row")
                                and item.get("table", {}).get("col") == block.get("table", {}).get("col")), None)
            if partner is not None:
                used.add(partner["id"])
                changed.append((partner["id"], block["id"]))
            else:
                added.append(block["id"])
        removed = [block["id"] for block in remaining_old if block["id"] not in used]
        return {"added": added, "removed": removed, "changed": changed, "unchanged_count": len(matched_old)}

    def register_source(self, message, actor):
        self.check_message(message, "source_document")
        document, original = message["document"], message["original"]
        issues = []
        if document["content_hash"] != original["sha256"]:
            issues.append(_issue("SOURCE_HASH_MISMATCH", "error", "document.content_hash 必须等于 original.sha256", "/document/content_hash"))
        content = None
        if original.get("inline_base64") is not None:
            try:
                content = base64.b64decode(original["inline_base64"], validate=True)
            except (binascii.Error, ValueError):
                issues.append(_issue("OTHER", "error", "original.inline_base64 不是有效的 Base64", "/original/inline_base64"))
            else:
                if hashlib.sha256(content).hexdigest() != original["sha256"]:
                    issues.append(_issue("SOURCE_HASH_MISMATCH", "error", "原件字节的 SHA-256 与 original.sha256 不一致", "/original/inline_base64"))
                if original.get("bytes") is not None and original["bytes"] != len(content):
                    issues.append(_issue("OTHER", "warning", "original.bytes 与实际字节数不一致", "/original/bytes"))
        else:
            issues.append(_issue("OTHER", "info", "原件未内联提交，只登记 uri；KnowledgeOS 未归档原件字节", "/original"))
        issues.extend(self._block_issues(message))
        blocks = {block["id"]: block["text"] for block in message["blocks"]}
        for index, anchor in enumerate((document.get("document_dates") or {}).get("evidence") or []):
            _, anchor_issues = self._anchor(anchor, f"/document/document_dates/evidence/{index}", blocks)
            issues.extend({**item, "severity": "warning"} if item["severity"] == "error" else item for item in anchor_issues)
        receipt = {"protocol_version": PROTOCOL, "message_type": "source_receipt", "document_id": document["id"],
                   "document_version": document["version"], "content_hash": document["content_hash"], "stored": False,
                   "block_count": len(message["blocks"])}
        if _errors(issues):
            receipt["issues"] = issues
            return receipt
        snapshot_hash = digest(message["blocks"])
        existing = self.db.first("SELECT * FROM durable.extraction_source WHERE document_id=? AND document_version=?",
                                 (document["id"], document["version"]))
        if existing:
            if existing["content_hash"] == document["content_hash"] and existing["snapshot_hash"] == snapshot_hash:
                return json.loads(existing["receipt_json"])
            raise ConflictError("document version is already registered with different content; register a new version")
        previous = None
        if document.get("previous_version"):
            previous = self.db.first("SELECT * FROM durable.extraction_source WHERE document_id=? AND document_version=?",
                                     (document["id"], document["previous_version"]))
            if not previous:
                issues.append(_issue("OTHER", "warning", f"上一版本 {document['previous_version']} 未登记，无法计算差异", "/document/previous_version"))
        else:
            previous = self.db.first("""SELECT * FROM durable.extraction_source WHERE document_id=?
              ORDER BY registered_at DESC, rowid DESC LIMIT 1""", (document["id"],))
        at_risk = []
        receipt.update({"stored": True, "evidence_ref": f"evidence:sha256:{original['sha256']}"})
        if previous:
            diff = self._diff(json.loads(previous["blocks_json"]), message["blocks"])
            receipt["diff"] = {"previous_version": previous["document_version"], "added": diff["added"],
                               "removed": diff["removed"], "changed": [new for _, new in diff["changed"]],
                               "unchanged_count": diff["unchanged_count"]}
            affected = {old: "removed" for old in diff["removed"]}
            affected.update({old: "changed" for old, _ in diff["changed"]})
            if affected:
                placeholders = ",".join("?" for _ in affected)
                for row in self.db.execute(f"""SELECT DISTINCT target,block_id FROM durable.extraction_fact_evidence
                  WHERE document_id=? AND document_version=? AND block_id IN ({placeholders}) ORDER BY target,block_id""",
                                           (document["id"], previous["document_version"], *affected)):
                    at_risk.append({"target": row["target"], "block_id": row["block_id"], "change": affected[row["block_id"]]})
            receipt["facts_at_risk"] = at_risk
        receipt["issues"] = issues
        now = utcnow()
        with self.db.transaction():
            if content is not None:
                self.db.write("""INSERT OR IGNORE INTO durable.extraction_original(sha256,media_type,size,content,uri,stored_at)
                  VALUES(?,?,?,?,?,?)""", (original["sha256"], original["media_type"], len(content), content, original.get("uri"), now))
            self.db.write("""INSERT INTO durable.extraction_source(document_id,document_version,content_hash,snapshot_hash,
              document_json,original_json,parser_json,pages_json,blocks_json,previous_version,registered_by,registered_at,receipt_json)
              VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                          (document["id"], document["version"], document["content_hash"], snapshot_hash, json_text(document),
                           json_text({key: value for key, value in original.items() if key != "inline_base64"}),
                           json_text(message["parser"]), json_text(message.get("pages") or []), json_text(message["blocks"]),
                           previous["document_version"] if previous else None, actor, now, json_text(receipt)))
            for item in at_risk:
                self._review(kind="extraction_fact_at_risk", target=item["target"], default=70,
                             reason=f"来源 {document['id']} 新版本 {document['version']} 中证据块已{('删除' if item['change'] == 'removed' else '修改')}",
                             details={**item, "document_id": document["id"], "document_version": document["version"],
                                      "previous_version": previous["document_version"]})
            self.service.audit.stage(event_type="extraction_source_registered", actor=actor, target_id=document["id"],
                                     source_ref=document["locator"], after_hash=document["content_hash"],
                                     reason="extraction v2 source document registered",
                                     payload={"document_version": document["version"], "snapshot_hash": snapshot_hash,
                                              "block_count": len(message["blocks"]), "facts_at_risk": len(at_risk)})
        self.service.audit.flush()
        return receipt

    def _review(self, *, kind, target, default, reason, details):
        thresholds = self.registry.policies.get("maintenance", {}).get("priority", {})
        severity = int(default)
        priority = next((key for key, item in sorted(thresholds.items(), key=lambda kv: -int(kv[1].get("minimum_score", 0)))
                         if severity >= int(item.get("minimum_score", 0))), "P3")
        identifier = hashlib.sha256(f"{kind}:{target}:{json_text(details, sorted_keys=True)}".encode()).hexdigest()
        now = utcnow()
        self.db.write("""INSERT INTO review_item(id,kind,target_id,severity,priority,reason,details_json,status,created_at,updated_at)
          VALUES(?,?,?,?,?,?,?,'open',?,?) ON CONFLICT(id) DO UPDATE SET updated_at=excluded.updated_at""",
                      (identifier, kind, target, severity, priority, reason, json_text(details), now, now))

    # -- anchors -------------------------------------------------------------

    def _anchor(self, anchor, path, blocks):
        """Validate one anchor against registered block text; returns (anchor_or_None, issues)."""
        issues = []
        text = blocks.get(anchor["block_id"])
        if text is None:
            return None, [_issue("ANCHOR_BLOCK_UNKNOWN", "error", f"块 {anchor['block_id']} 不在登记的来源快照中", path, None)]
        limit = self.profile["limits"]["max_quote_chars"]
        if len(anchor["quote"]) > limit:
            return None, [_issue("LIMIT_EXCEEDED", "error", f"引文长度超过 {limit}", path + "/quote", anchor["block_id"])]
        start, end = anchor["start"], anchor["end"]
        if 0 <= start < end <= len(text) and text[start:end] == anchor["quote"]:
            return dict(anchor), issues
        out_of_range = not 0 <= start < end <= len(text)
        if self.profile["acceptance"]["quote_match"] == "normalized_exact":
            folded, mapping = _collapse(text)
            quote = _fold(anchor["quote"])
            if quote:
                # Only occurrences overlapping the claimed range qualify: normalization may shift
                # offsets, but it never relocates an anchor to another part of the block.
                positions = [match.start() for match in re.finditer(re.escape(quote), folded)
                             if mapping[match.start()] < end and start <= mapping[match.start() + len(quote) - 1]]
                if positions:
                    best = min(positions, key=lambda position: abs(mapping[position] - start))
                    new_start = mapping[best]
                    last = mapping[best + len(quote) - 1]
                    new_end = last + 1
                    while new_end < len(text) and unicodedata.combining(text[new_end]):
                        new_end += 1
                    fixed = {**anchor, "start": new_start, "end": new_end, "quote": text[new_start:new_end]}
                    issues.append(_issue("QUOTE_NORMALIZED_MATCH", "warning", "引文按 NFKC 与空白折叠后匹配，已按服务端重算的偏移保存",
                                         path, anchor["block_id"], {"start": new_start, "end": new_end}))
                    return fixed, issues
        if out_of_range:
            return None, [_issue("ANCHOR_OUT_OF_RANGE", "error", f"偏移 [{start},{end}) 超出块文本长度 {len(text)}", path, anchor["block_id"])]
        return None, [_issue("QUOTE_MISMATCH", "error", "quote 与块文本 text[start:end] 不一致", path, anchor["block_id"])]

    def _anchors(self, anchors, path, blocks):
        valid, issues = [], []
        for index, anchor in enumerate(anchors or []):
            fixed, anchor_issues = self._anchor(anchor, f"{path}/{index}", blocks)
            issues.extend(anchor_issues)
            if fixed:
                valid.append(fixed)
        return valid, issues

    def _raw_in(self, raw, anchors, roles=("value",)):
        quotes = [anchor["quote"] for anchor in anchors if roles is None or anchor.get("role", "value") in roles]
        if any(raw in quote for quote in quotes):
            return True
        if self.profile["acceptance"]["quote_match"] == "normalized_exact":
            folded = _fold(raw)
            return bool(folded) and any(folded in _fold(quote) for quote in quotes)
        return False

    # -- acceptance ----------------------------------------------------------

    def evaluate(self, submission, *, actor=None, propose=False):
        self.check_message(submission, "extraction_submission")
        if propose != (submission["mode"] == "propose"):
            raise ValidationError("mode must be propose for submissions and precheck for prechecks")
        if propose and not str(submission.get("reason") or "").strip():
            raise ValidationError("propose submissions require a reason")
        evaluation = _Evaluation(self, submission)
        report = evaluation.run()
        if not propose:
            return report
        return self._propose(submission, evaluation, report, actor)

    def _propose(self, submission, evaluation, report, actor):
        submission_digest = digest(submission)
        prior = self.db.first("SELECT * FROM durable.extraction_submission WHERE actor=? AND submission_id=?",
                              (actor, submission["submission_id"]))
        if prior:
            if prior["submission_hash"] != submission_digest:
                raise ConflictError("submission_id was already used with different content; use a new submission_id")
            return json.loads(prior["report_json"])
        changesets = []
        if report["status"] != "rejected":
            for candidate in evaluation.candidates:
                binding = {"source_id": evaluation.document["id"], "source_hash": evaluation.document["content_hash"],
                           "locator": evaluation.document["locator"], "entity_type": candidate["concept"],
                           "mention": norm(candidate["label"]), "context_ids": candidate["context_ids"],
                           "target_id": candidate["node_id"], "evidence": candidate["label_evidence"],
                           "protocol_version": PROTOCOL, "submission_id": submission["submission_id"],
                           "document_version": evaluation.document["version"], "local_id": candidate["local_id"],
                           "input_digest": submission_digest}
                title = f"抽取提案：{candidate['label']}（{evaluation.document.get('title') or evaluation.document['id']}）"
                result = self.service.propose(actor=actor, target_source=candidate["target_source"], patch=candidate["operations"],
                                              reason=submission["reason"], risk="high" if candidate["high_risk"] else "normal",
                                              title=title[:200], base_revision=candidate["base_revision"],
                                              idempotency_key=f"extraction-v2:{submission['submission_id']}:{candidate['node_id']}",
                                              extraction=None if candidate["implicit"] else binding)["data"]
                changesets.append({"id": result["id"], "status": result["status"], "target_source": result["target_source"],
                                   "local_ids": [candidate["local_id"]]})
                candidate["changeset_id"] = result["id"]
        report["changesets"] = changesets
        now = utcnow()
        document = evaluation.document
        with self.db.transaction():
            current = self.db.first("SELECT submission_hash,report_json FROM durable.extraction_submission WHERE actor=? AND submission_id=?",
                                    (actor, submission["submission_id"]))
            if current:
                if current["submission_hash"] != submission_digest:
                    raise ConflictError("submission_id was already used with different content; use a new submission_id")
                return json.loads(current["report_json"])
            self.db.write("""INSERT INTO durable.extraction_submission(actor,submission_id,submission_hash,document_id,document_version,
              contract_id,registry_fingerprint,status,run_json,submission_json,report_json,recorded_at)
              VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                          (actor, submission["submission_id"], submission_digest, document["id"], document["version"],
                           submission["contract"]["contract_id"], submission["contract"]["registry_fingerprint"], report["status"],
                           json_text(submission["run"]), json_text(submission), json_text(report), now))
            if report["status"] != "rejected":
                for candidate in evaluation.candidates:
                    for target, block_id in candidate["evidence_targets"]:
                        self.db.write("""INSERT OR IGNORE INTO durable.extraction_fact_evidence(document_id,document_version,block_id,target,
                          submission_id,changeset_id,recorded_at) VALUES(?,?,?,?,?,?,?)""",
                                      (document["id"], document["version"], block_id, target, submission["submission_id"],
                                       candidate.get("changeset_id"), now))
                for item in evaluation.queue:
                    identifier = digest([actor, submission["submission_id"], item["kind"], item["local_id"], item.get("predicate"), item.get("field_index")])[:32]
                    self.db.write("""INSERT OR IGNORE INTO durable.extraction_queue_item(id,kind,status,actor,submission_id,document_id,
                      document_version,local_id,target_id,predicate,payload_json,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                                  (identifier, item["kind"], "open", actor, submission["submission_id"], document["id"], document["version"],
                                   item["local_id"], item.get("target_id"), item.get("predicate"), json_text(item["payload"]), now))
                    self._review(kind="extraction_" + item["kind"], target=item.get("target_id") or item["local_id"],
                                 default={"upstream_required": 40, "draft_instance": 50, "rule_signal": 60, "unmapped_fact": 20}[item["kind"]],
                                 reason=item["reason"], details={"queue_item": identifier, "document_id": document["id"],
                                                                 "document_version": document["version"],
                                                                 "submission_id": submission["submission_id"]})
            self.service.audit.stage(event_type="extraction_v2_submission", actor=actor, target_id=submission["submission_id"],
                                     source_ref=document["id"], after_hash=submission_digest, reason=submission["reason"],
                                     payload={"status": report["status"], "document_version": document["version"],
                                              "changesets": [item["id"] for item in changesets], "run_id": submission["run"]["run_id"],
                                              "queued": len(evaluation.queue)})
        self.service.audit.flush()
        return report

    # -- read models ---------------------------------------------------------

    def queue(self, kind=None, status="open", limit=100):
        sql, binds = "SELECT * FROM durable.extraction_queue_item WHERE status=?", [status]
        if kind:
            sql += " AND kind=?"
            binds.append(kind)
        sql += " ORDER BY created_at DESC LIMIT ?"
        binds.append(min(max(int(limit), 1), 500))
        return [{**{key: value for key, value in row.items() if key != "payload_json"}, "payload": json.loads(row["payload_json"])}
                for row in self.db.execute(sql, binds)]

    def source(self, document_id, version=None):
        if version:
            row = self.db.first("SELECT * FROM durable.extraction_source WHERE document_id=? AND document_version=?", (document_id, version))
        else:
            row = self.db.first("SELECT * FROM durable.extraction_source WHERE document_id=? ORDER BY registered_at DESC, rowid DESC LIMIT 1",
                                (document_id,))
        if not row:
            raise NotFoundError(f"extraction source not found: {document_id}")
        versions = [item["document_version"] for item in self.db.execute(
            "SELECT document_version FROM durable.extraction_source WHERE document_id=? ORDER BY registered_at,rowid", (document_id,))]
        return {"document": json.loads(row["document_json"]), "original": json.loads(row["original_json"]),
                "parser": json.loads(row["parser_json"]), "blocks": json.loads(row["blocks_json"]),
                "receipt": json.loads(row["receipt_json"]), "versions": versions,
                "original_archived": bool(self.db.first("SELECT 1 FROM durable.extraction_original WHERE sha256=? AND content IS NOT NULL",
                                                        (row["content_hash"],)))}


    # -- authoring (Studio extraction workspace) -------------------------------

    def _raw(self, path):
        return {key: value for key, value in load_yaml(path).items()} if path.exists() else None

    def assets(self):
        """Editable view of control/extraction/v2 plus the registry facts the editor needs."""
        from .governance import ChangePlan
        from .semantic_contract import SemanticContract
        plan = ChangePlan(self.service)
        root = self.service.config.root
        directory = self.service.config.control / "extraction" / "v2"
        excluded = set(self.profile.get("excluded_concepts") or [])
        semantics = SemanticContract(self.registry)

        def item(kind, path, key):
            relative = path.relative_to(root).as_posix()
            return {"kind": kind, "id": key, "source_path": relative, "exists": path.exists(),
                    "revision": plan.revision(relative), "document": self._raw(path)}

        concepts = []
        for concept_id, concept in self.concepts.items():
            pref, alt = {}, []
            for term in semantics.terminology(concept_id):
                pref.update({key: value for key, value in (term.get("pref_labels") or {}).items() if value})
                for values in (term.get("alt_labels") or {}).values():
                    alt.extend(values if isinstance(values, list) else [values])
            properties = []
            for binding in concept.get("properties", []):
                predicate = self.registry.predicates[binding["predicate"]]
                value = predicate.get("value", {})
                properties.append({"predicate": binding["predicate"], "label": predicate.get("label") or binding["predicate"],
                                   "description": predicate.get("semantics", {}).get("description", ""),
                                   "type": value.get("type"), "cardinality": value.get("cardinality"),
                                   "dimension": value.get("dimension"), "units": value.get("units") or [],
                                   "default_unit": value.get("default_unit"), "allowed": value.get("allowed") or [],
                                   "storage": predicate.get("storage", {}).get("mode", "attr"), "required": bool(binding.get("required")),
                                   "publication_route": "upstream_source_system" if self.connector_only(binding["predicate"]) else "governed_changeset"})
            concepts.append({"id": concept_id, "label": concept.get("label") or concept_id, "description": concept.get("description", ""),
                             "terms": {"pref": pref, "alt": list(dict.fromkeys(alt))}, "excluded": concept_id in excluded,
                             "properties": properties, "card": item("card", directory / "cards" / f"{concept_id}.yaml", concept_id)})
        recipe_paths = {path.stem: path for path in sorted((directory / "recipes").glob("*.yaml"))}
        pending = [{"id": row["id"], "status": row["status"], "target_source": row["target_source"], "title": row["title"],
                    "actor": row["actor"], "created_at": row["created_at"]}
                   for row in self.db.execute("""SELECT id,status,target_source,title,actor,created_at FROM changeset
                     WHERE target_source LIKE 'control/extraction/v2/%' AND status NOT IN ('published','rejected') ORDER BY created_at DESC""")]
        return {"registry_fingerprint": self.registry.fingerprint,
                "profile": item("profile", directory / "profile.yaml", "profile"),
                "effective_profile": public(self.profile),
                "concepts": concepts,
                "recipes": [item("recipe", path, key) for key, path in recipe_paths.items()],
                "relations": [{"id": relation["id"], "label": relation.get("label") or relation["id"],
                               "endpoints": [{"source_type": edge["source_type"], "target_type": edge["target_type"]}
                                             for edge in relation.get("connections", [])]} for relation in self.relations.values()],
                "units": [{"id": key, "label": value.get("label") or key, "dimension": value.get("dimension")} for key, value in self.registry.units.items()],
                "currencies": sorted(self.registry.currencies),
                "warnings": list(self.registry.extraction_v2.get("warnings") or []),
                "pending": pending}

    def asset_preview(self, kind, document, samples=None, sample_text=None):
        """Strict validation, the resulting contract fragment, value trials and cue hits for a draft asset."""
        from .governance import ChangePlan
        result = {"kind": kind, "valid": False, "errors": [], "warnings": []}
        try:
            path = asset_path(kind, document)
            result["source_path"] = path
            checked = validate_asset(self.registry, kind, document)
        except ValidationError as exc:
            result["errors"].append(str(exc))
            return result
        target = self.service.config.root / path
        current = self._raw(target)
        result["revision"] = ChangePlan(self.service).revision(path)
        result["exists"] = current is not None
        if current is not None:
            try:
                newer = _version_key(asset_version(kind, document)) > _version_key(asset_version(kind, current))
            except ValueError:
                newer = True
            if not newer:
                result["errors"].append(f"版本 {asset_version(kind, document)} 必须高于当前版本 {asset_version(kind, current)}")
        engine = ExtractionV2(self.service)
        if kind == "card":
            engine.cards = {**engine.cards, checked["concept"]: checked}
            result["contract"] = engine.concept_contract(checked["concept"])
            concepts = [checked["concept"]]
        elif kind == "recipe":
            engine.recipes = {**engine.recipes, checked["id"]: checked}
            concepts = sorted({mapping["concept"] for mapping in checked["mappings"]} & set(engine.extractable()))
            result["contract"] = {"recipe": public(checked)}
        else:
            engine.profile = checked
            engine.normalizer = ValueNormalizer(self.registry, checked)
            concepts = engine.extractable()
            result["contract"] = {"limits": checked["limits"], "acceptance": checked["acceptance"],
                                  "extractable_concepts": concepts}
        trials = []
        for sample in (samples or [])[:50]:
            predicate_id, raw = (sample or {}).get("predicate"), str((sample or {}).get("raw") or "")
            concept = (sample or {}).get("concept") or (checked.get("concept") if kind == "card" else None)
            trial = {"predicate": predicate_id, "raw": raw}
            if predicate_id not in self.registry.predicates or not raw.strip():
                trial["error"] = "需要已注册的属性和非空原文"
            else:
                try:
                    value = {"raw": raw}
                    if (sample or {}).get("unit_raw"):
                        value["unit_raw"] = sample["unit_raw"]
                    normalized, issues = engine.normalizer.normalize(predicate_id, value, card=engine.cards.get(concept))
                    trial["normalized"] = normalized
                    if issues:
                        trial["notes"] = [message for _, _, message in issues]
                except NormalizationError as exc:
                    trial.update({"error": exc.message, "code": exc.code})
            trials.append(trial)
        result["trials"] = trials
        if sample_text and str(sample_text).strip() and concepts:
            text = str(sample_text)[:20000]
            evaluation = _Evaluation.__new__(_Evaluation)
            evaluation.engine, evaluation.registry, evaluation.db, evaluation.profile = engine, self.registry, self.db, engine.profile
            evaluation.scope = {"concepts": concepts}
            evaluation.document = {"document_type": (checked.get("document_types") or [None])[0] if kind == "recipe" else None,
                                   "kind": None}
            lines = [line for line in text.splitlines() if line.strip()][:200]
            evaluation.block_rows = []
            for index, line in enumerate(lines):
                heading = bool(re.match(r"^\s*(#+\s|[一二三四五六七八九十]+、)", line))
                evaluation.block_rows.append({"id": f"s{index + 1}", "type": "heading" if heading else "paragraph", "text": line, "order": index})
            hits = evaluation._cues()
            blocks = []
            for block in evaluation.block_rows:
                cues = [{**{key: value for key, value in cue.items() if key != "_span"}, "start": cue["_span"][0], "end": cue["_span"][1]}
                        for cue in hits.get(block["id"], [])]
                blocks.append({"block_id": block["id"], "text": block["text"], "cues": cues})
            result["cue_preview"] = blocks
        result["valid"] = not result["errors"]
        return result


class _Evaluation:
    """One deterministic acceptance pass over an extraction submission (shared by precheck and propose)."""

    def __init__(self, engine: ExtractionV2, submission):
        self.engine = engine
        self.registry = engine.registry
        self.db = engine.db
        self.profile = engine.profile
        self.submission = submission
        self.top_issues = []
        self.candidates = []
        self.queue = []
        self.instances = {}
        self.document = None
        self.blocks = {}
        self.block_rows = []

    # -- framework -----------------------------------------------------------

    def _report(self, status, instances=(), relations=(), rule_signals=(), unmapped=0, coverage=None):
        report = {"protocol_version": PROTOCOL, "message_type": "validation_report", "submission_id": self.submission["submission_id"],
                  "mode": self.submission["mode"], "status": status, "registry_fingerprint": self.registry.fingerprint,
                  "contract_id": self.submission["contract"]["contract_id"], "evaluated_at": utcnow(),
                  "instances": list(instances), "relations": list(relations), "rule_signals": list(rule_signals),
                  "unmapped_recorded": unmapped,
                  "coverage": coverage or {"blocks_total": len(self.block_rows), "blocks_with_evidence": 0,
                                           "uncovered_cue_blocks": [], "missing_required": []},
                  "changesets": [], "issues": self.top_issues}
        return report

    def run(self):
        submission = self.submission
        contract = submission["contract"]
        if contract["registry_fingerprint"] != self.registry.fingerprint:
            self.top_issues.append(_issue("CONTRACT_STALE", "error", "注册表已变化，请重新获取契约并重新抽取", "/contract/registry_fingerprint"))
            return self._report("rejected")
        record = self.db.first("SELECT * FROM durable.extraction_contract WHERE id=?", (contract["contract_id"],))
        if not record or record["registry_fingerprint"] != contract["registry_fingerprint"]:
            self.top_issues.append(_issue("CONTRACT_STALE", "error", "契约未登记或与指纹不符，请重新获取契约", "/contract/contract_id"))
            return self._report("rejected")
        self.scope = json.loads(record["scope_json"])
        source = submission["source"]
        row = self.db.first("SELECT * FROM durable.extraction_source WHERE document_id=? AND document_version=?",
                            (source["document_id"], source["document_version"]))
        if not row:
            self.top_issues.append(_issue("SOURCE_UNKNOWN", "error", "来源文档版本未登记，请先提交 source_document", "/source"))
            return self._report("rejected")
        if row["content_hash"] != source["content_hash"]:
            self.top_issues.append(_issue("SOURCE_HASH_MISMATCH", "error", "content_hash 与登记的来源版本不一致", "/source/content_hash"))
            return self._report("rejected")
        self.document = json.loads(row["document_json"])
        self.block_rows = json.loads(row["blocks_json"])
        self.blocks = {block["id"]: block["text"] for block in self.block_rows}
        limits = self.profile["limits"]
        if len(submission["instances"]) > limits["max_instances"]:
            self.top_issues.append(_issue("LIMIT_EXCEEDED", "error", f"实例数量超过契约上限 {limits['max_instances']}", "/instances"))
            return self._report("rejected")
        self.reference = reference_date(self.document.get("document_dates"), self.document.get("captured_at"))
        self.source_class = self.engine.source_authority(self.document)
        if self.source_class is None:
            self.top_issues.append(_issue("AUTHORITY_UNVERIFIED", "warning",
                                          "来源缺少 document.origin.system，权威等级无法核验，按最低支持来源处理", "/source"))
        self.duplicates = self._duplicate_local_ids()
        self._rejected_relations = {}
        for index, item in enumerate(submission["instances"]):
            self._instance(index, item)
        relations = [self._relation(index, item) for index, item in enumerate(submission.get("relations") or [])]
        for record_ in list(self.instances.values()):
            self._finish_instance(record_)
        relation_results = [self._relation_result(item) for item in relations]
        self._build_all()
        for item, output in zip(relations, relation_results):
            if item["local_id"] in self._rejected_relations and output["outcome"] == "accepted":
                output["outcome"] = "rejected"
                output.setdefault("issues", []).append(_issue("OTHER", "error", self._rejected_relations[item["local_id"]],
                                                              f"/relations/{item['index']}"))
        instance_results = [result for result in (self._instance_result(record_) for record_ in list(self.instances.values())) if result]
        rule_results = [self._rule_signal(index, item) for index, item in enumerate(submission.get("rule_signals") or [])]
        unmapped = self._unmapped()
        coverage = self._coverage()
        coverage["missing_required"] = [{"local_id": item["local_id"], "predicates": item["missing"]}
                                        for item in self.instances.values() if item.get("missing")]
        items = instance_results + relation_results + rule_results
        all_issues = list(self.top_issues)
        for result in instance_results:
            all_issues += result.get("issues", [])
            for field in result.get("fields", []):
                all_issues += field.get("issues", [])
        for result in relation_results + rule_results:
            all_issues += result.get("issues", [])
        acceptable = [item for item in items if item["outcome"] != "rejected"]
        if items and not acceptable:
            status = "rejected"
        elif _errors(all_issues) or any(item["outcome"] == "rejected" for item in items):
            status = "partially_accepted"
        else:
            status = "accepted"
        return self._report(status, instance_results, relation_results, rule_results, unmapped, coverage)

    def _duplicate_local_ids(self):
        seen, duplicates = {}, set()
        for collection in ("instances", "relations", "rule_signals"):
            for index, item in enumerate(self.submission.get(collection) or []):
                if item["local_id"] in seen:
                    duplicates.add(item["local_id"])
                    self.top_issues.append(_issue("DUPLICATE_LOCAL_ID", "error", f"local_id {item['local_id']} 在提交中重复",
                                                  f"/{collection}/{index}/local_id"))
                seen.setdefault(item["local_id"], (collection, index))
        return duplicates

    # -- instances -----------------------------------------------------------

    def _instance(self, index, item):
        path = f"/instances/{index}"
        record = {"index": index, "item": item, "local_id": item["local_id"], "concept": item["concept"], "label": item["label"]["raw"],
                  "issues": [], "fields": [], "outcome": None, "node_id": None, "resolution": None, "relations": [],
                  "aliases": [], "context_ids": [], "anchors": [], "label_evidence": []}
        key = item["local_id"]
        if key in self.duplicates and key in self.instances:
            record["outcome"] = "rejected"
            self.instances[f"{key}#{index}"] = record
            return record
        self.instances[key] = record
        if key in self.duplicates:
            record["issues"].append(_issue("DUPLICATE_LOCAL_ID", "error", "local_id 重复，后续同名项被拒绝", path + "/local_id"))
        concept = self.engine.concepts.get(item["concept"])
        if concept is None or item["concept"] not in self.scope["concepts"]:
            record["issues"].append(_issue("CONCEPT_UNKNOWN", "error", f"概念 {item['concept']} 不在本次契约范围内", path + "/concept"))
            record["outcome"] = "rejected"
            return record
        limits = self.profile["limits"]
        if len(item["fields"]) > limits["max_fields_per_instance"]:
            record["issues"].append(_issue("LIMIT_EXCEEDED", "error", f"字段数量超过上限 {limits['max_fields_per_instance']}", path + "/fields"))
            record["outcome"] = "rejected"
            return record
        label_anchors, issues = self.engine._anchors(item["label"]["evidence"], path + "/label/evidence", self.blocks)
        record["issues"] += issues
        if issues and _errors(issues) or not label_anchors:
            record["outcome"] = "rejected"
            return record
        if not self.engine._raw_in(item["label"]["raw"], label_anchors, roles=None):
            record["issues"].append(_issue("VALUE_NOT_IN_EVIDENCE", "error", "实例名称未出现在其证据引文中", path + "/label/raw"))
            record["outcome"] = "rejected"
            return record
        record["label_evidence"] = label_anchors
        record["anchors"] += label_anchors
        for alias_index, alias in enumerate(item.get("aliases") or []):
            anchors, issues = self.engine._anchors(alias["evidence"], f"{path}/aliases/{alias_index}/evidence", self.blocks)
            if _errors(issues) or not self.engine._raw_in(alias["raw"], anchors, roles=None):
                record["issues"] += [{**issue, "severity": "warning"} for issue in issues]
                record["issues"].append(_issue("VALUE_NOT_IN_EVIDENCE", "warning", "别名缺少有效证据，已忽略", f"{path}/aliases/{alias_index}"))
                continue
            record["issues"] += issues
            record["aliases"].append(alias["raw"].strip())
            record["anchors"] += anchors
        identity = item.get("identity") or {}
        key_raw = None
        if identity.get("key"):
            anchors, issues = self.engine._anchors(identity["key"]["evidence"], path + "/identity/key/evidence", self.blocks)
            if _errors(issues) or not self.engine._raw_in(identity["key"]["raw"], anchors, roles=None):
                record["issues"] += [{**issue, "severity": "warning"} for issue in issues]
                record["issues"].append(_issue("VALUE_NOT_IN_EVIDENCE", "warning", "身份编码缺少有效证据，已忽略", path + "/identity/key"))
            else:
                record["issues"] += issues
                key_raw = identity["key"]["raw"].strip()
                record["anchors"] += anchors
        mentions, issues = self.engine._anchors(item.get("mentions") or [], path + "/mentions", self.blocks)
        record["issues"] += [{**issue, "severity": "warning"} for issue in issues]
        record["anchors"] += mentions
        record["key"] = key_raw
        record["key_namespace"] = identity.get("key_namespace")
        for context_index, reference in enumerate(identity.get("context") or []):
            if "node_id" in reference:
                record["context_ids"].append(reference["node_id"])
            else:
                target = self.instances.get(reference["local_id"])
                if target and target.get("node_id") and target["outcome"] == "matched":
                    record["context_ids"].append(target["node_id"])
                else:
                    record["issues"].append(_issue("UNKNOWN_LOCAL_REF", "warning", "上下文实例未在前序实例中解析，已忽略",
                                                   f"{path}/identity/context/{context_index}"))
        self._resolve(record, identity.get("node_hint"), path)
        for field_index, field in enumerate(item["fields"]):
            record["fields"].append(self._field(record, field_index, field, f"{path}/fields/{field_index}"))
        return record

    def _resolve(self, record, hint, path):
        resolver = EntityResolver(self.db)
        quotes = [{"quote": anchor["quote"]} for anchor in record["anchors"]]
        entity = {"type": record["concept"], "label": record["label"].strip(), "aliases": record["aliases"],
                  "key": record["key"], "key_namespace": record["key_namespace"], "evidence": quotes}
        source = {"id": self.document["id"]}
        context_ids = list(record["context_ids"])
        while True:
            try:
                result = resolver.resolve({**entity, "context_ids": context_ids}, source)
                break
            except ValidationError as exc:
                if not context_ids:
                    raise
                record["issues"].append(_issue("OTHER", "warning", f"上下文实例无法作为身份证据：{exc}", path + "/identity/context"))
                context_ids = []
        record["context_ids"] = context_ids
        candidates = result.get("candidates") or []
        if hint:
            ids = [item["id"] for item in candidates]
            if hint not in ids:
                record["issues"].append(_issue("IDENTITY_NOT_IN_EVIDENCE", "warning", f"node_hint {hint} 不在依据证据得到的候选中，已忽略",
                                               path + "/identity/node_hint"))
            elif result["status"] == "matched" and result["target"]["id"] != hint:
                result = {"status": "needs_resolution", "reason": "node_hint disagrees with evidence-based match", "candidates": candidates}
        record["resolution"] = {"reason": result["reason"], "candidates": [
            {"node_id": item["id"], "label": item["label"], "rank": int(item["rank"]), "reasons": list(item["reasons"])}
            for item in candidates[:20] if re.fullmatch(r"[a-z][a-z0-9_-]*:[^\s]+", item["id"])]}
        if not record["resolution"]["candidates"]:
            del record["resolution"]["candidates"]
        if result["status"] == "matched":
            node = self.db.first("SELECT * FROM node WHERE id=?", (result["target"]["id"],))
            record["node"] = node
            record["node_id"] = node["id"]
            record["outcome"] = "matched" if node["source_class"] == "git_authored" else "upstream_required"
            if record["outcome"] == "upstream_required":
                record["issues"].append(_issue("CONNECTOR_AUTHORITY_REQUIRED", "info",
                                               "该实例由业务系统经连接器维护，抽取结果只登记为待核对事项", path))
        elif result["status"] == "new":
            record["outcome"] = "new"
            record["node_id"] = self._new_identity(record)
        else:
            record["outcome"] = "needs_resolution"
            record["issues"].append(_issue("IDENTITY_AMBIGUOUS", "warning", f"身份无法唯一确定：{result['reason']}，需要人工确认", path))

    def _new_identity(self, record):
        prefix = (self.profile.get("identity_prefixes") or {}).get(record["concept"], record["concept"].replace("_", "-"))
        seed = record["key"] or record["label"]
        slug = re.sub(r"[^a-z0-9]+", "-", nfkc(seed).lower()).strip("-")[:60].strip("-") or digest(seed)[:12]
        identifier = f"{prefix}:{slug}"
        taken = {item.get("node_id") for item in self.instances.values() if item is not record}
        root = self.engine.service.config.root

        def unavailable(candidate):
            return (candidate in taken or self.db.first("SELECT 1 FROM node WHERE id=?", (candidate,))
                    or (root / f"knowledge/entities/{candidate.replace(':', '-')}.md").exists())

        if unavailable(identifier):
            identifier = f"{prefix}:{slug}-{digest([self.document['id'], record['local_id']])[:6]}"
            if unavailable(identifier):
                identifier = f"{prefix}:{digest([self.document['id'], self.document['version'], record['local_id']])[:16]}"
        return identifier

    def _field(self, record, index, field, path):
        engine, registry = self.engine, self.registry
        predicate_id = field["predicate"]
        result = {"predicate": predicate_id, "field_index": index, "issues": [], "outcome": "rejected", "anchors": []}
        bindings = {binding["predicate"]: binding for binding in engine.concepts[record["concept"]].get("properties", [])}
        if predicate_id not in bindings:
            result["issues"].append(_issue("PREDICATE_NOT_BOUND", "error", f"{predicate_id} 未绑定到概念 {record['concept']}", path + "/predicate"))
            return result
        predicate = registry.predicates[predicate_id]
        result["binding"] = bindings[predicate_id]
        result["storage"] = predicate.get("storage", {}).get("mode", "attr")
        limit = self.profile["limits"]["max_anchors_per_field"]
        if len(field["evidence"]) > limit:
            result["issues"].append(_issue("LIMIT_EXCEEDED", "error", f"证据锚点超过上限 {limit}", path + "/evidence"))
            return result
        anchors, issues = engine._anchors(field["evidence"], path + "/evidence", self.blocks)
        result["issues"] += issues
        if _errors(issues):
            return result
        result["anchors"] = anchors
        if not engine._raw_in(field["value"]["raw"], anchors):
            result["issues"].append(_issue("VALUE_NOT_IN_EVIDENCE", "error", "value.raw 必须包含在某个 role=value 的引文中", path + "/value/raw"))
            return result
        minimum = float(self.profile["acceptance"].get("min_confidence") or 0)
        confidence = field.get("confidence", record["item"].get("confidence"))
        if confidence is not None and confidence < minimum:
            result["issues"].append(_issue("CONFIDENCE_BELOW_THRESHOLD", "warning", f"置信度 {confidence} 低于验收门槛 {minimum}，不进入提案", path + "/confidence"))
            return result
        result["confidence"] = confidence
        ref_id = None
        reference = field["value"].get("ref")
        if reference:
            if "node_id" in reference:
                ref_id = reference["node_id"] if self.db.first("SELECT 1 FROM node WHERE id=?", (reference["node_id"],)) else None
            else:
                target = self.instances.get(reference["local_id"])
                ref_id = target.get("node_id") if target and target["outcome"] in ("matched", "new") else None
        card = engine.cards.get(record["concept"])
        try:
            normalized, normalization_issues = engine.normalizer.normalize(predicate_id, field["value"], card=card,
                                                                           storage=result["storage"], ref_id=ref_id)
        except NormalizationError as exc:
            result["issues"].append(_issue(exc.code, "error", exc.message, path + "/value"))
            return result
        result["issues"] += [_issue(code, severity, message, path + "/value") for code, severity, message in normalization_issues]
        canonical = {key: value for key, value in normalized.items() if key != "qualifiers"}
        try:
            validate_value(predicate, canonical, registry.units, registry.currencies)
        except ValidationError as exc:
            result["issues"].append(_issue("VALUE_UNPARSEABLE", "error", str(exc), path + "/value"))
            return result
        result["normalized"] = normalized
        hint = field["value"].get("agent_hint")
        if isinstance(hint, dict) and self._hint_differs(hint, normalized):
            result["issues"].append(_issue("AGENT_HINT_MISMATCH", "warning", "agent_hint 与服务端规范化结果不一致，以服务端结果为准",
                                           path + "/value/agent_hint", details={"agent_hint": hint}))
        if result["storage"] != "attr":
            temporal = self._temporal(field, path)
            if temporal is None:
                result["issues"].append(_issue("VALUE_UNPARSEABLE", "error", "时间表达无法解析或区间无效", path + "/temporal"))
                return result
            result["temporal"], temporal_issues, temporal_anchors = temporal
            result["issues"] += temporal_issues
            result["anchors"] += temporal_anchors
            if _errors(temporal_issues):
                return result
        if field.get("supersedes_hint"):
            result["supersedes_hint"] = field["supersedes_hint"]
        result["qualifiers_raw"] = dict(field.get("qualifiers") or {})
        if record["outcome"] == "upstream_required" or engine.connector_only(predicate_id):
            policy = engine.authority_policy(predicate_id)
            result["outcome"] = "upstream_required"
            if record["outcome"] != "upstream_required":
                result["issues"].append(_issue("CONNECTOR_AUTHORITY_REQUIRED", "info",
                                               f"{predicate_id} 由权威业务系统维护（authority: {policy.get('id')}），已登记为待核对事项，不写入 Git 真源",
                                               path))
            return result
        if predicate.get("policy", {}).get("provenance_tier") == "A" and self.source_class is not None:
            policy = engine.authority_policy(predicate_id)
            listed = set(policy.get("primary", [])) | set(policy.get("secondary", [])) | set(policy.get("supporting", []))
            if self.source_class not in listed:
                result["issues"].append(_issue("AUTHORITY_UNVERIFIED", "info",
                                               f"来源等级 {self.source_class} 不在 {policy.get('id')} 的权威来源中，需审核确认", path))
        result["outcome"] = "accepted"
        if record["outcome"] == "matched":
            self._compare_existing(record, result, path)
        return result

    @staticmethod
    def _hint_differs(hint, normalized):
        if "literal" in hint:
            try:
                if Decimal(str(hint["literal"])) != Decimal(str(normalized.get("literal"))):
                    return True
            except (InvalidOperation, ValueError):
                if str(hint["literal"]) != str(normalized.get("literal")):
                    return True
        return "unit" in hint and hint["unit"] != normalized.get("unit")

    def _temporal(self, field, path):
        temporal = field.get("temporal") or {}
        issues, anchors = [], []
        offset = self.engine.normalizer.offset
        if temporal.get("evidence"):
            anchors, anchor_issues = self.engine._anchors(temporal["evidence"], path + "/temporal/evidence", self.blocks)
            issues += anchor_issues
            if _errors(anchor_issues):
                return {"observed_at": None, "valid_from": None, "valid_to": None}, issues, []
        elif any(temporal.get(key) for key in ("observed_at_raw", "valid_from_raw", "valid_to_raw")):
            issues.append(_issue("OTHER", "warning", "时间原文缺少证据锚点", path + "/temporal"))
        result = {"observed_at": None, "valid_from": None, "valid_to": None}
        for key, raw_key in (("observed_at", "observed_at_raw"), ("valid_from", "valid_from_raw"), ("valid_to", "valid_to_raw")):
            raw = temporal.get(raw_key)
            if not raw:
                continue
            if anchors and not self.engine._raw_in(raw, anchors, roles=None):
                issues.append(_issue("VALUE_NOT_IN_EVIDENCE", "warning", f"{raw_key} 未出现在时间证据中", f"{path}/temporal/{raw_key}"))
            try:
                start, end = self.engine.normalizer.temporal(raw, self.reference)
            except NormalizationError:
                return None
            result[key] = end if key == "valid_to" else start
        if not result["observed_at"]:
            dates = self.document.get("document_dates") or {}
            for key in ("effective_from", "issued_at"):
                parsed = parse_time(dates.get(key), None) if dates.get(key) else None
                if parsed:
                    result["observed_at"] = timestamp(parsed.start, offset)
                    break
            else:
                result["observed_at"] = self.document["captured_at"]
        from datetime import datetime
        if result["valid_from"] and result["valid_to"]:
            if datetime.fromisoformat(result["valid_from"].replace("Z", "+00:00")) >= datetime.fromisoformat(result["valid_to"].replace("Z", "+00:00")):
                return None
        return result, issues, anchors

    @staticmethod
    def _same_value(left, right):
        if isinstance(left, dict) and isinstance(right, dict):
            try:
                same_number = Decimal(str(left.get("literal"))) == Decimal(str(right.get("literal")))
            except (InvalidOperation, ValueError):
                same_number = left.get("literal") == right.get("literal")
            return same_number and left.get("unit") == right.get("unit")
        if isinstance(left, dict):
            left = left.get("literal")
        if isinstance(right, dict):
            right = right.get("literal")
        return left == right

    def _existing_value(self, value):
        if isinstance(value, dict):
            return {key: value[key] for key in ("type", "literal", "unit") if key in value}
        return value

    def _compare_existing(self, record, result, path):
        node = record["node"]
        predicate_id = result["predicate"]
        predicate = self.registry.predicates[predicate_id]
        normalized = {key: value for key, value in result["normalized"].items() if key != "qualifiers"}
        policy = self.engine.authority_policy(predicate_id)
        strategy = policy.get("conflict", {}).get("strategy", "review_equal_authority")
        existing_source = ("git:" + node["source_path"]) if node["source_path"] else node["source_class"]
        if result["storage"] == "attr":
            attrs = json.loads(node["attrs_json"])
            if predicate_id not in attrs:
                return
            current = attrs[predicate_id]
            if predicate.get("value", {}).get("cardinality") == "many" and isinstance(current, list):
                if any(self._same_value(item, normalized) for item in current):
                    result["outcome"] = "already_present"
                return
            if self._same_value(current, normalized):
                result["outcome"] = "already_present"
                return
            existing = current if isinstance(current, dict) else {"type": predicate["value"]["type"], "literal": current}
            result["outcome"] = "conflict"
            result["conflict"] = {"existing": {key: existing[key] for key in ("type", "literal", "unit") if key in existing},
                                  "existing_source": existing_source, "authority_strategy": strategy}
            result["issues"].append(_issue("VALUE_CONFLICT", "warning", "单值属性与现有取值不同，提案时需审核人确认是否替换。", path))
            return
        qualifiers = {**result["qualifiers_raw"], **result["normalized"].get("qualifiers", {})}
        rows = self.db.execute("SELECT * FROM assertion WHERE node_id=? AND predicate=? AND status NOT IN ('rejected')", (node["id"], predicate_id))
        for row in rows:
            if self._same_value(json.loads(row["value_json"]), normalized) and json.loads(row["qualifiers_json"]) == qualifiers:
                result["outcome"] = "already_present"
                return
        cardinality = predicate.get("value", {}).get("cardinality")
        confirmed = [row for row in rows if row["status"] == "confirmed"]
        if cardinality == "one" and confirmed:
            current = json.loads(confirmed[0]["value_json"])
            result["outcome"] = "conflict"
            result["conflict"] = {"existing": self._existing_value(current), "existing_source": existing_source, "authority_strategy": strategy}
            result["issues"].append(_issue("VALUE_CONFLICT", "warning", "单值断言与现有取值不同，提案时需审核人确认是否替代。", path))

    def _finish_instance(self, record):
        """Business constraints, required fields and draft routing after all fields are known."""
        if record["outcome"] not in ("matched", "new", "needs_resolution"):
            return
        concept = self.engine.concepts[record["concept"]]
        accepted = [field for field in record["fields"] if field["outcome"] in ("accepted", "conflict")]
        self._check_constraints(record, accepted)
        accepted = [field for field in record["fields"] if field["outcome"] in ("accepted", "conflict")]
        if record["outcome"] != "new":
            return
        present = {field["predicate"] for field in accepted}
        card = self.engine.cards.get(record["concept"]) or {}
        minimum = [item for item in (card.get("identity") or {}).get("minimum_fields", []) if item not in present]
        path = f"/instances/{record['index']}"
        if minimum:
            record["outcome"] = "rejected"
            record["issues"].append(_issue("REQUIRED_MISSING", "error", f"新实例缺少建档所需的最少字段：{', '.join(minimum)}", path,
                                           details={"predicates": minimum}))
            return
        missing = [binding["predicate"] for binding in concept.get("properties", []) if binding.get("required") and binding["predicate"] not in present]
        if missing:
            record["missing"] = missing
            if self.profile["acceptance"].get("draft_instances"):
                record["outcome"] = "draft"
                record["issues"].append(_issue("REQUIRED_MISSING", "warning",
                                               f"缺少发布必填字段 {', '.join(missing)}，已作为草稿实例进入补全队列，不生成 ChangeSet", path,
                                               details={"predicates": missing}))
                self.queue.append({"kind": "draft_instance", "local_id": record["local_id"], "target_id": record["node_id"],
                                   "reason": f"抽取草稿实例缺少 {', '.join(missing)}",
                                   "payload": {"concept": record["concept"], "label": record["label"], "proposed_id": record["node_id"],
                                               "missing": missing, "aliases": record["aliases"], "key": record["key"],
                                               "fields": [self._queue_field(field) for field in accepted]}})
            else:
                record["outcome"] = "rejected"
                record["issues"].append(_issue("REQUIRED_MISSING", "error", f"缺少发布必填字段：{', '.join(missing)}", path,
                                               details={"predicates": missing}))
        elif record["item"].get("completeness") == "draft":
            record["issues"].append(_issue("OTHER", "info", "实例标为 draft，但发布必填字段已齐备，按完整实例处理", path))

    def _queue_field(self, field):
        return {"predicate": field["predicate"], "field_index": field["field_index"], "normalized": field.get("normalized"),
                "temporal": field.get("temporal"), "evidence": field.get("anchors", [])}

    def _check_constraints(self, record, accepted):
        from .business_logic import evaluate_business
        if not accepted:
            return
        concept = record["concept"]
        facts = {}
        node = record.get("node")
        if node:
            facts.update(json.loads(node["attrs_json"]))
            for row in self.db.execute("""SELECT predicate,value_json FROM assertion WHERE node_id=? AND status='confirmed'
              ORDER BY coalesce(valid_from,''),coalesce(observed_at,'')""", (node["id"],)):
                facts[row["predicate"]] = json.loads(row["value_json"])
        for field in accepted:
            facts[field["predicate"]] = {key: value for key, value in field["normalized"].items() if key != "qualifiers"}
        for definition in self.registry.constraints.values():
            if (definition.get("format") != "knowledgeos.business-constraint.v1" or definition.get("status", {}).get("lifecycle") != "active"
                    or definition.get("scope", {}).get("subject_concept") != concept):
                continue
            try:
                verdict = evaluate_business(definition, "business_constraint", {"subject": facts}, self.registry.predicates,
                                            self.registry.ontology, self.registry.units)["results"][0]["verdict"]
            except ValidationError:
                continue
            if verdict != "invalid":
                continue
            inputs = {item["predicate"] for item in definition.get("inputs", [])}
            for field in accepted:
                if field["predicate"] in inputs:
                    field["outcome"] = "rejected"
                    field["issues"].append(_issue("CONSTRAINT_VIOLATION", "error",
                                                  f"违反已启用的业务约束 {definition['id']}@{definition.get('version')}：{definition.get('label', '')}",
                                                  f"/instances/{record['index']}/fields/{field['field_index']}",
                                                  details={"logic_ref": f"constraint:{definition['id']}@{definition.get('version')}"}))

    # -- relations -----------------------------------------------------------

    def _endpoint(self, reference, path, issues):
        if "local_id" in reference:
            target = self.instances.get(reference["local_id"])
            if target is None:
                issues.append(_issue("UNKNOWN_LOCAL_REF", "error", f"{reference['local_id']} 不是本次提交中的实例", path))
                return None
            return {"local": target, "type": target["concept"]}
        node = self.db.first("SELECT id,type,source_class FROM node WHERE id=?", (reference["node_id"],))
        if not node:
            issues.append(_issue("RELATION_ENDPOINT_INVALID", "error", f"实例 {reference['node_id']} 不存在", path))
            return None
        return {"node": node, "type": node["type"]}

    def _relation(self, index, item):
        path = f"/relations/{index}"
        result = {"item": item, "index": index, "local_id": item["local_id"], "issues": [], "outcome": "rejected"}
        if item["local_id"] in self.duplicates and any(other["local_id"] == item["local_id"] for other in self.submission["instances"]):
            result["issues"].append(_issue("DUPLICATE_LOCAL_ID", "error", "local_id 与实例重复", path + "/local_id"))
            return result
        definition = self.engine.relations.get(item["predicate"])
        if definition is None:
            result["issues"].append(_issue("RELATION_ENDPOINT_INVALID", "error", f"关系类型 {item['predicate']} 未登记", path + "/predicate"))
            return result
        anchors, issues = self.engine._anchors(item["evidence"], path + "/evidence", self.blocks)
        result["issues"] += issues
        if _errors(issues):
            return result
        result["anchors"] = anchors
        minimum = float(self.profile["acceptance"].get("min_confidence") or 0)
        if item.get("confidence") is not None and item["confidence"] < minimum:
            result["issues"].append(_issue("CONFIDENCE_BELOW_THRESHOLD", "warning", f"置信度低于验收门槛 {minimum}", path + "/confidence"))
            return result
        source = self._endpoint(item["source"], path + "/source", result["issues"])
        target = self._endpoint(item["target"], path + "/target", result["issues"])
        if source is None or target is None:
            return result
        if not any(edge["source_type"] == source["type"] and edge["target_type"] == target["type"] for edge in definition.get("connections", [])):
            result["issues"].append(_issue("RELATION_ENDPOINT_INVALID", "error",
                                           f"关系 {item['predicate']} 不允许 {source['type']} → {target['type']}", path))
            return result
        result.update({"source": source, "target": target, "definition": definition, "outcome": "pending"})
        return result

    def _relation_result(self, result):
        path = f"/relations/{result['index']}"
        item = result["item"]
        if result["outcome"] == "pending":
            source, target, definition = result["source"], result["target"], result["definition"]

            def resolved(endpoint):
                if "node" in endpoint:
                    return endpoint["node"]["id"], "git_authored" if endpoint["node"]["source_class"] == "git_authored" else "upstream"
                local = endpoint["local"]
                if local["outcome"] == "matched":
                    return local["node_id"], "git_authored"
                if local["outcome"] == "new":
                    return local["node_id"], "new"
                if local["outcome"] == "upstream_required":
                    return local["node_id"], "upstream"
                return None, local["outcome"]

            source_id, source_state = resolved(source)
            target_id, target_state = resolved(target)
            if source_id:
                result["source_node_id"] = source_id
            if target_id:
                result["target_node_id"] = target_id
            if not source_id or not target_id:
                result["outcome"] = "deferred"
                result["issues"].append(_issue("OTHER", "warning", "关系端点实例未通过验收或待确认身份，关系暂缓", path))
            elif definition.get("mode") == "reified":
                result["outcome"] = "deferred"
                result["issues"].append(_issue("OTHER", "info", "具象化关系需要独立的关系节点，请在白板维护", path))
            elif source_state == "upstream":
                result["outcome"] = "deferred"
                result["issues"].append(_issue("CONNECTOR_AUTHORITY_REQUIRED", "info", "关系起点由业务系统维护，关系需在权威系统中维护", path))
            elif source_state == "new" and target_state == "new" and not self.profile["acceptance"].get("relations_between_new_instances", True):
                result["outcome"] = "rejected"
                result["issues"].append(_issue("OTHER", "error", "契约不允许在两个新实例之间建立关系", path))
            elif self.db.first("SELECT 1 FROM edge WHERE src=? AND predicate=? AND dst=? LIMIT 1", (source_id, item["predicate"], target_id)):
                result["outcome"] = "already_present"
            else:
                result["outcome"] = "accepted"
                relation = {"predicate": item["predicate"], "target": target_id}
                temporal = {}
                for key, raw_key in (("valid_from", "valid_from_raw"), ("valid_to", "valid_to_raw")):
                    if (item.get("temporal") or {}).get(raw_key):
                        try:
                            start, end = self.engine.normalizer.temporal(item["temporal"][raw_key], self.reference)
                        except NormalizationError as exc:
                            result["outcome"] = "rejected"
                            result["issues"].append(_issue("VALUE_UNPARSEABLE", "error", exc.message, f"{path}/temporal/{raw_key}"))
                            break
                        temporal[key] = end if key == "valid_to" else start
                if result["outcome"] == "accepted":
                    if temporal:
                        relation["temporal"] = temporal
                    owner = source["local"] if "local" in source else self._owner_for_node(source["node"])
                    owner["relations"].append({"relation": relation, "local_id": item["local_id"], "anchors": result["anchors"]})
        output = {"local_id": result["local_id"], "outcome": result["outcome"] if result["outcome"] != "pending" else "rejected"}
        for key in ("source_node_id", "target_node_id"):
            if result.get(key):
                output[key] = result[key]
        if result["issues"]:
            output["issues"] = result["issues"]
        result["output"] = output
        return output

    def _owner_for_node(self, node):
        """Relations whose source is an existing authored node not extracted as an instance."""
        key = "node:" + node["id"]
        if key not in self.instances:
            row = self.db.first("SELECT * FROM node WHERE id=?", (node["id"],))
            self.instances[key] = {"index": None, "item": None, "local_id": None, "concept": row["type"], "label": row["label"],
                                   "issues": [], "fields": [], "outcome": "matched", "node_id": row["id"], "node": row,
                                   "relations": [], "aliases": [], "context_ids": [], "anchors": [], "label_evidence": [],
                                   "implicit": True}
        return self.instances[key]

    # -- documents -----------------------------------------------------------

    def _assertion(self, record, field):
        normalized = field["normalized"]
        value = {key: value for key, value in normalized.items() if key != "qualifiers"}
        qualifiers = {**field.get("qualifiers_raw", {}), **normalized.get("qualifiers", {})}
        document = self.document
        identifier = "assertion:extract:" + digest([record["node_id"], field["predicate"], value, qualifiers, document["id"], document["content_hash"]])[:32]
        evidence_refs = list(dict.fromkeys(f"{document['id']}@{document['version']}#{anchor['block_id']}:{anchor['start']}-{anchor['end']}"
                                           for anchor in field["anchors"]))
        confidence = field.get("confidence")
        return {"id": identifier, "predicate": field["predicate"], "value": value, "qualifiers": qualifiers,
                "temporal": dict(field["temporal"]),
                "epistemic": {"assertion_kind": "extracted_claim", "status": "proposed",
                              "confidence": float(confidence) if confidence is not None else None},
                "provenance": {"source_refs": list(dict.fromkeys((document["id"], document["locator"]))), "evidence_refs": evidence_refs},
                "version": {"supersedes": field.get("supersedes")}}

    def _attr_value(self, predicate_id, normalized, current=None):
        predicate = self.registry.predicates[predicate_id]
        typ = predicate.get("value", {}).get("type")
        value = {key: item for key, item in normalized.items() if key != "qualifiers"}
        scalar = value if typ in ("quantity", "currency", "decimal") else value["literal"]
        if predicate.get("value", {}).get("cardinality") == "many":
            return list(current or []) + [scalar]
        return scalar

    def _build(self, record):
        """Assemble the canonical document and patch for an instance, validated exactly as publication will."""
        if record["outcome"] not in ("matched", "new"):
            return
        from .compiler import Compiler
        from .governance import ChangePlan
        service = self.engine.service
        node = record.get("node")
        include = [field for field in record["fields"] if field["outcome"] == "accepted" or
                   field["outcome"] == "conflict" and field["conflict"]["authority_strategy"] != "prefer_primary"]
        for field in record["fields"]:
            if field["outcome"] == "conflict" and field not in include:
                self._note(field, _issue("OTHER", "info", "冲突策略为 prefer_primary，抽取来源不替换现有值"))
        if record["outcome"] == "matched":
            if node["source_class"] != "git_authored":
                return
            authored, _ = frontmatter(service.config.root / node["source_path"])
            base = copy.deepcopy(authored["base"])
            attrs = copy.deepcopy(authored["knowledge"]["attrs"])
            assertions = copy.deepcopy(authored["knowledge"].get("assertions") or [])
            relations = copy.deepcopy(authored["knowledge"].get("relations") or [])
            logic_refs = copy.deepcopy(authored["knowledge"].get("logic_refs") or [])
            external = copy.deepcopy(authored.get("external") or {"assertions_ref": None})
            target_source = node["source_path"]
        else:
            base = {"schema": {"ckm": "3.0"}, "node": {"id": record["node_id"], "kind": "entity", "type": record["concept"],
                    "key": record["key"] or record["node_id"].split(":", 1)[1].upper(), "label": record["label"].strip(),
                    "aliases": [alias for alias in dict.fromkeys(record["aliases"]) if alias != record["label"].strip()]},
                    "classification": {"tags": []}, "lifecycle": {"state": "draft"}, "version": {"entity_revision": 1}}
            if record["key_namespace"]:
                base["node"]["key_namespace"] = record["key_namespace"]
            attrs, assertions, relations, logic_refs, external = {}, [], [], [], {"assertions_ref": None}
            target_source = f"knowledge/entities/{record['node_id'].replace(':', '-')}.md"
        operations, new_assertions, evidence_targets = [], [], []
        high_risk = False
        current_attrs = dict(attrs)
        for field in include:
            predicate_id = field["predicate"]
            if field["storage"] == "attr":
                value = self._attr_value(predicate_id, field["normalized"], current_attrs.get(predicate_id))
                attrs[predicate_id] = value
                if record["outcome"] == "matched":
                    operations.append({"op": "replace" if predicate_id in current_attrs else "add",
                                       "path": "/knowledge/attrs/" + predicate_id.replace("~", "~0").replace("/", "~1"), "value": value})
                evidence_targets += [(f"{record['node_id']}#{predicate_id}", anchor["block_id"]) for anchor in field["anchors"]]
            else:
                supersedes = None
                if field.get("supersedes_hint") and node:
                    prior = self.db.first("SELECT node_id,predicate,source_path,status FROM assertion WHERE id=?", (field["supersedes_hint"],))
                    if prior and prior["node_id"] == node["id"] and prior["predicate"] == predicate_id and prior["status"] == "confirmed" \
                            and prior["source_path"] == node["source_path"]:
                        supersedes = field["supersedes_hint"]
                    else:
                        self._note(field, _issue("OTHER", "warning", "supersedes_hint 不是同一实例同一属性的已确认断言，已忽略"))
                if field["outcome"] == "conflict" and not supersedes and self.registry.predicates[predicate_id].get("value", {}).get("cardinality") == "one":
                    prior = self.db.first("""SELECT id,source_path FROM assertion WHERE node_id=? AND predicate=? AND status='confirmed'
                      ORDER BY coalesce(valid_from,'') DESC LIMIT 1""", (node["id"], predicate_id))
                    supersedes = prior["id"] if prior and prior["source_path"] == node["source_path"] else None
                field["supersedes"] = supersedes
                assertion = self._assertion(record, field)
                if assertion["id"] in {item["id"] for item in assertions + new_assertions}:
                    field["outcome"] = "already_present"
                    continue
                new_assertions.append(assertion)
                evidence_targets += [(assertion["id"], anchor["block_id"]) for anchor in field["anchors"]]
            if field["outcome"] == "conflict":
                high_risk = True
        assertions_all = assertions + new_assertions
        existing_relations = {(item["predicate"], item["target"]) for item in relations}
        new_relations = []
        for entry in record["relations"]:
            relation = entry["relation"]
            if (relation["predicate"], relation["target"]) in existing_relations:
                continue
            existing_relations.add((relation["predicate"], relation["target"]))
            new_relations.append(relation)
            evidence_targets += [(f"{record['node_id']}->{relation['predicate']}->{relation['target']}", anchor["block_id"])
                                 for anchor in entry["anchors"]]
        relations = relations + new_relations
        if record["outcome"] == "matched":
            aliases = list(base["node"].get("aliases") or [])
            added_aliases = [alias for alias in dict.fromkeys(record["aliases"])
                             if alias not in aliases and norm(alias) != norm(base["node"]["label"])]
            if added_aliases:
                base["node"]["aliases"] = aliases + added_aliases
                operations.append({"op": "replace" if "aliases" in authored["base"]["node"] else "add", "path": "/base/node/aliases",
                                   "value": base["node"]["aliases"]})
            for assertion in new_assertions:
                operations.append({"op": "add", "path": "/knowledge/assertions/-", "value": assertion})
            for relation in new_relations:
                operations.append({"op": "add", "path": "/knowledge/relations/-", "value": relation})
            if not operations:
                return
            revision = int(base["version"]["entity_revision"]) + 1
            base["version"]["entity_revision"] = revision
            operations.append({"op": "replace", "path": "/base/version/entity_revision", "value": revision})
        else:
            evidence_targets += [(record["node_id"], anchor["block_id"]) for anchor in record["label_evidence"]]
        document = {"base": base, "knowledge": {"attrs": attrs, "assertions": assertions_all, "relations": relations, "logic_refs": logic_refs},
                    "external": external}
        if record["outcome"] == "new":
            operations = [{"op": "add", "path": "", "value": document}]
        validation_assertions = assertions_all
        if external.get("assertions_ref") and node:
            validation_assertions = Compiler(service.config, self.registry, self.db, service.ledger)._assertions(
                service.config.root / target_source, {"knowledge": {"assertions": assertions}, "external": external}) + new_assertions
        try:
            Compiler(service.config, self.registry, self.db, service.ledger).validate(service.config.root / target_source, document, validation_assertions)
        except ValidationError as exc:
            message = str(exc)
            code = "CONSTRAINT_VIOLATION" if "business constraint" in message else "OTHER"
            path = f"/instances/{record['index']}" if record.get("index") is not None else "/relations"
            for field in include:
                if field["outcome"] in ("accepted", "conflict"):
                    field["outcome"] = "rejected"
            for entry in record["relations"]:
                self._reject_relation(entry["local_id"], message)
            record["issues"].append(_issue(code, "error", f"候选实例未通过规范校验：{message}", path))
            record["build_failed"] = True
            if record["outcome"] == "new" or record.get("implicit"):
                record["outcome"] = "rejected"
            return
        self.candidates.append({"local_id": record["local_id"] or record["relations"][0]["local_id"], "node_id": record["node_id"],
                                "concept": record["concept"], "label": record["label"].strip(), "target_source": target_source,
                                "operations": operations, "base_revision": ChangePlan(service).revision(target_source),
                                "high_risk": high_risk, "context_ids": sorted(record["context_ids"]), "implicit": bool(record.get("implicit")),
                                "label_evidence": record["label_evidence"], "evidence_targets": list(dict.fromkeys(evidence_targets))})

    def _reject_relation(self, local_id, message):
        self._rejected_relations[local_id] = message

    def _build_all(self):
        ordered = sorted(self.instances.values(), key=lambda item: 0 if item["outcome"] == "new" else 1)
        for record in ordered:
            self._build(record)
        for _ in range(len(self.instances) + 1):
            failed = {record["node_id"] for record in self.instances.values() if record.get("build_failed")}
            affected = []
            for record in self.instances.values():
                if record.get("build_failed"):
                    continue
                dropped = [entry for entry in record["relations"] if entry["relation"]["target"] in failed]
                if dropped:
                    for entry in dropped:
                        self._reject_relation(entry["local_id"], "关系目标实例未通过规范校验，关系未进入提案")
                    record["relations"] = [entry for entry in record["relations"] if entry not in dropped]
                    affected.append(record)
            if not affected:
                break
            for record in affected:
                self.candidates = [item for item in self.candidates if item["node_id"] != record["node_id"]]
                self._build(record)

    @staticmethod
    def _note(field, issue):
        if not any(item["code"] == issue["code"] and item["message"] == issue["message"] for item in field["issues"]):
            field["issues"].append(issue)

    def _instance_result(self, record):
        if record.get("implicit"):
            return None
        output = {"local_id": record["local_id"], "outcome": record["outcome"]}
        if record.get("node_id") and record["outcome"] != "rejected":
            output["node_id"] = record["node_id"]
        if record.get("resolution"):
            output["resolution"] = record["resolution"]
        fields = []
        for field in record["fields"]:
            item = {"predicate": field["predicate"], "field_index": field["field_index"], "outcome": field["outcome"]}
            if field.get("normalized"):
                item["normalized"] = field["normalized"]
            if field.get("temporal") and field.get("storage") != "attr":
                item["temporal"] = field["temporal"]
            if field.get("conflict") and field["outcome"] == "conflict":
                item["conflict"] = field["conflict"]
            issues = [issue for issue in field["issues"]]
            for issue in issues:
                if issue.get("path") is None:
                    issue["path"] = f"/instances/{record['index']}/fields/{field['field_index']}"
            if issues:
                item["issues"] = issues
            fields.append(item)
            if field["outcome"] == "upstream_required" and record["outcome"] in ("matched", "new", "upstream_required"):
                self.queue.append({"kind": "upstream_required", "local_id": record["local_id"], "target_id": record.get("node_id"),
                                   "predicate": field["predicate"], "field_index": field["field_index"],
                                   "reason": f"{field['predicate']} 须由权威业务系统维护，抽取值待核对",
                                   "payload": {"concept": record["concept"], "label": record["label"], **self._queue_field(field)}})
        if fields:
            output["fields"] = fields
        if record["issues"]:
            output["issues"] = record["issues"]
        return output

    # -- rule signals --------------------------------------------------------

    def _find_logic(self, signal):
        hint = signal.get("logic_ref_hint")
        sources = {"constraint": self.registry.constraints, "rule": self.registry.rules}
        if hint:
            kind, _, rest = hint.partition(":")
            identifier, _, version = rest.partition("@")
            definition = sources.get(kind, {}).get(identifier)
            if definition and str(definition.get("version")) == version:
                return kind, definition, None
            return None, None, _issue("OTHER", "warning", f"logic_ref_hint {hint} 未登记，改按主体概念和属性查找", None)
        subject, predicate = signal.get("subject_concept"), signal.get("predicate")
        if not subject or not predicate:
            return None, None, None
        matches = []
        for kind, definitions in sources.items():
            for identifier, definition in sorted(definitions.items()):
                lifecycle = definition.get("status", {}).get("lifecycle")
                if (definition.get("scope", {}).get("subject_concept") == subject and lifecycle in ("active", "draft")
                        and any(item.get("predicate") == predicate for item in definition.get("inputs", []))):
                    matches.append((lifecycle != "active", kind, identifier, definition))
        if matches:
            _, kind, _, definition = sorted(matches)[0]
            return kind, definition, None
        return None, None, None

    def _operator(self, signal, qualifiers):
        raw = nfkc(signal.get("operator_raw") or "").strip()
        for words, operator in OPERATORS:
            if raw and any(raw == word or raw.startswith(word) for word in words):
                return operator
        if qualifiers.get("bound") in BOUND_OPERATORS:
            return BOUND_OPERATORS[qualifiers["bound"]]
        statement = nfkc(signal["statement"]["raw"])
        for words, operator in OPERATORS[:5]:
            if any(word in statement for word in words):
                return operator
        return None

    def _base_amount(self, literal, unit):
        units = self.registry.units
        number = Decimal(str(literal))
        if unit in units:
            return number * Decimal(str(units[unit]["factor_to_base"])), units[unit]["dimension"]
        return number, unit

    def _rule_signal(self, index, signal):
        path = f"/rule_signals/{index}"
        result = {"local_id": signal["local_id"], "outcome": "rejected", "issues": []}
        if signal["local_id"] in self.duplicates and any(item["local_id"] == signal["local_id"] for item in
                                                         self.submission["instances"] + (self.submission.get("relations") or [])):
            result["issues"].append(_issue("DUPLICATE_LOCAL_ID", "error", "local_id 与其他项重复", path + "/local_id"))
            return result
        anchors, issues = self.engine._anchors(signal["statement"]["evidence"], path + "/statement/evidence", self.blocks)
        result["issues"] += issues
        if _errors(issues):
            return result
        if not self.engine._raw_in(signal["statement"]["raw"], anchors, roles=None):
            result["issues"].append(_issue("VALUE_NOT_IN_EVIDENCE", "error", "条款原文未出现在证据引文中", path + "/statement/raw"))
            return result
        subject, predicate_id = signal.get("subject_concept"), signal.get("predicate")
        if subject and subject not in self.engine.concepts:
            result["issues"].append(_issue("CONCEPT_UNKNOWN", "error", f"概念 {subject} 未登记", path + "/subject_concept"))
            return result
        if predicate_id and (predicate_id not in self.registry.predicates or subject and predicate_id not in
                             {binding["predicate"] for binding in self.engine.concepts[subject].get("properties", [])}):
            result["issues"].append(_issue("PREDICATE_NOT_BOUND", "error", f"{predicate_id} 未绑定到 {subject or '任何概念'}", path + "/predicate"))
            return result
        kind, definition, hint_issue = self._find_logic(signal)
        if hint_issue:
            hint_issue["path"] = path + "/logic_ref_hint"
            result["issues"].append(hint_issue)
        extracted = {}
        qualifiers = {}
        if signal.get("value") and predicate_id:
            try:
                normalized, _ = self.engine.normalizer.normalize(predicate_id, signal["value"], card=self.engine.cards.get(subject))
                qualifiers = normalized.get("qualifiers", {})
                extracted.update({"value": str(normalized["literal"])})
                if normalized.get("unit"):
                    extracted["unit"] = normalized["unit"]
            except NormalizationError as exc:
                result["issues"].append(_issue(exc.code, "warning", f"规则阈值无法规范化：{exc.message}", path + "/value"))
        operator = self._operator(signal, qualifiers)
        if operator:
            extracted["operator"] = operator
        scope = signal.get("scope_raw") or ("specific instances" if signal.get("applies_to") else None)
        extracted["scope"] = scope or f"all {subject}" if subject else scope
        if definition is None:
            result.update({"outcome": "no_existing_logic", "next_action": "draft_new_logic",
                           "comparison": {"extracted": {key: value for key, value in extracted.items() if value is not None}}})
            self._queue_rule(signal, result, None)
            return result
        prefix = "constraint" if kind == "constraint" else "rule"
        result["logic_ref"] = f"{prefix}:{definition['id']}@{definition.get('version')}"
        inputs = {item["id"]: item for item in definition.get("inputs", [])}
        existing = None
        for check in definition.get("checks", []):
            left = inputs.get(check["left"], {})
            if left.get("predicate") == predicate_id and "value" in check.get("right", {}):
                value = check["right"]["value"]
                if isinstance(value, dict) and "value" in value:
                    existing = {"operator": check["operator"], "value": str(value["value"])}
                    if value.get("unit"):
                        existing["unit"] = value["unit"]
                else:
                    existing = {"operator": check["operator"], "value": str(value)}
                break
        subject_ids = definition.get("scope", {}).get("subject_ids") or []
        if existing is not None:
            existing["scope"] = f"instances {', '.join(subject_ids)}" if subject_ids else f"all {definition.get('scope', {}).get('subject_concept')}"
        differences = []
        if existing is None:
            differences.append("现有逻辑没有该属性的常量阈值，无法直接比对")
        else:
            if extracted.get("operator") and extracted["operator"] != existing["operator"] or extracted.get("value") is not None and self._threshold_differs(existing, extracted):
                differences.append("阈值不同：" + f"{OPERATOR_SYMBOLS.get(existing['operator'], existing['operator'])} {existing['value']} {existing.get('unit', '')}".strip()
                                   + " 与 " + f"{OPERATOR_SYMBOLS.get(extracted.get('operator'), extracted.get('operator') or '?')} {extracted.get('value', '?')} {extracted.get('unit', '')}".strip())
            if signal.get("scope_raw") or signal.get("applies_to"):
                differences.append("适用范围更窄：" + (signal.get("scope_raw") or "限定的具体实例"))
        comparison = {"extracted": {key: value for key, value in extracted.items() if value is not None}}
        if existing is not None:
            comparison["existing"] = existing
        if differences:
            comparison["differences"] = differences
            narrower = bool(signal.get("scope_raw") or signal.get("applies_to"))
            result.update({"outcome": "differs_from_existing", "next_action": "clarify_scope" if narrower else "review_logic_version"})
            self._queue_rule(signal, result, comparison)
        else:
            result.update({"outcome": "matches_existing", "next_action": "none"})
        result["comparison"] = comparison
        if not result["issues"]:
            del result["issues"]
        return result

    def _threshold_differs(self, existing, extracted):
        try:
            left, left_dimension = self._base_amount(existing["value"], existing.get("unit"))
            right, right_dimension = self._base_amount(extracted["value"], extracted.get("unit"))
        except (InvalidOperation, ValueError):
            return existing["value"] != extracted.get("value") or existing.get("unit") != extracted.get("unit")
        return left != right or left_dimension != right_dimension

    def _queue_rule(self, signal, result, comparison):
        self.queue.append({"kind": "rule_signal", "local_id": signal["local_id"], "target_id": result.get("logic_ref"),
                           "reason": "规则条款与已发布逻辑不一致或尚无对应逻辑，请在规则与决策工作台按版本流程处理",
                           "payload": {"statement": signal["statement"], "subject_concept": signal.get("subject_concept"),
                                       "predicate": signal.get("predicate"), "outcome": result["outcome"],
                                       "next_action": result.get("next_action"), "comparison": comparison or result.get("comparison")}})

    # -- unmapped facts and coverage ----------------------------------------

    def _unmapped(self):
        count = 0
        for index, fact in enumerate(self.submission.get("unmapped_facts") or []):
            anchors, issues = self.engine._anchors(fact["evidence"], f"/unmapped_facts/{index}/evidence", self.blocks)
            if _errors(issues):
                self.top_issues.extend({**issue, "severity": "warning"} for issue in issues)
                continue
            count += 1
            self.queue.append({"kind": "unmapped_fact", "local_id": f"unmapped:{index}", "target_id": None,
                               "reason": f"未映射事实（{fact['reason']}）：{fact['text'][:200]}",
                               "payload": {**fact, "evidence": anchors}})
        return count

    def _coverage(self):
        rows = self.block_rows
        evidence_blocks = set()
        covering = {}  # block_id -> list of (start, end, concept, predicate)
        for record in self.instances.values():
            if record.get("implicit"):
                continue
            for anchor in record["anchors"]:
                covering.setdefault(anchor["block_id"], []).append((anchor["start"], anchor["end"], record["concept"], None))
            for field in record["fields"]:
                for anchor in field.get("anchors", []):
                    covering.setdefault(anchor["block_id"], []).append((anchor["start"], anchor["end"], record["concept"], field["predicate"]))
        for collection in ("relations", "rule_signals", "unmapped_facts"):
            for item in self.submission.get(collection) or []:
                anchors = item.get("evidence") or (item.get("statement") or {}).get("evidence") or []
                for anchor in anchors:
                    if anchor["block_id"] in self.blocks:
                        evidence_blocks.add(anchor["block_id"])
                        if collection == "relations":
                            covering.setdefault(anchor["block_id"], []).append((anchor["start"], anchor["end"], None, None))
        evidence_blocks |= set(covering)
        for record in self.instances.values():
            for field in record["fields"]:
                for anchor in field.get("anchors", []):
                    evidence_blocks.add(anchor["block_id"])
        cues = self._cues()
        uncovered = []
        for block in rows:
            pending = []
            spans = covering.get(block["id"], [])
            block_cues = cues.get(block["id"], [])
            terms = [cue["_span"] for cue in block_cues if cue["type"] == "term"]
            block_cues = [cue for cue in block_cues if not (cue["type"] == "term" and any(
                other != cue["_span"] and other[0] <= cue["_span"][0] and cue["_span"][1] <= other[1] for other in terms))]
            for cue in block_cues:
                start, end = cue.pop("_span")
                covered = any(start < span_end and span_start < end for span_start, span_end, _, _ in spans)
                if not covered and cue["type"] == "term":
                    if cue.get("predicate"):
                        covered = any(predicate == cue["predicate"] for _, _, _, predicate in spans)
                    elif cue.get("concept"):
                        covered = any(concept == cue["concept"] for _, _, concept, _ in spans)
                if not covered:
                    pending.append(cue)
            if pending:
                seen, unique = set(), []
                for cue in pending:
                    key = json_text(cue, sorted_keys=True)
                    if key not in seen:
                        seen.add(key)
                        unique.append(cue)
                uncovered.append({"block_id": block["id"], "cues": unique[:50]})
        return {"blocks_total": len(rows), "blocks_with_evidence": len(evidence_blocks & set(self.blocks)),
                "uncovered_cue_blocks": uncovered[:500], "missing_required": []}

    def _cues(self):
        engine = self.engine
        concepts = self.scope["concepts"]
        terms = []  # (text, concept, predicate, is_concept_name)
        patterns = []
        for concept_id in concepts:
            pref, alt = engine._terms(concept_id)
            for text in list(pref.values()) + alt:
                terms.append((text, concept_id, None))
            card = engine.cards.get(concept_id) or {}
            for key in (card.get("identity") or {}).get("keys", []):
                for pattern in key.get("patterns", []):
                    patterns.append((pattern["pattern"], concept_id, None))
            for predicate_id, entry in (card.get("properties") or {}).items():
                term_set = entry.get("terms") or {}
                for text in list((term_set.get("pref") or {}).values()) + list(term_set.get("alt") or []):
                    terms.append((text, concept_id, predicate_id))
                for pattern in entry.get("value_patterns") or []:
                    patterns.append((pattern["pattern"], concept_id, predicate_id))
        seen_terms, unique_terms = set(), []
        for text, concept_id, predicate_id in terms:
            key = (nfkc(text).casefold(), predicate_id or concept_id if predicate_id else concept_id)
            if len(nfkc(text).strip()) >= 2 and key not in seen_terms:
                seen_terms.add(key)
                unique_terms.append((text, concept_id, predicate_id))
        unit_words = [alias for alias in (self.profile.get("unit_aliases") or {}) if not alias.isascii()]
        for concept_id in concepts:
            for predicate_id, entry in ((engine.cards.get(concept_id) or {}).get("properties") or {}).items():
                unit_words += [alias for alias in (entry.get("unit_aliases") or {}) if not alias.isascii()]
        unit_words = sorted(set(unit_words), key=len, reverse=True)
        lexicon = []
        placeholders = ",".join("?" for _ in concepts)
        for row in self.db.execute(f"""SELECT id,type,natural_key,label,aliases_json FROM node WHERE type IN ({placeholders})
          AND lifecycle NOT IN ('merged','retired') ORDER BY id LIMIT 5000""", tuple(concepts)):
            for name in dict.fromkeys([row["label"], row["natural_key"], *json.loads(row["aliases_json"])]):
                if name and len(norm(name)) >= 2:
                    lexicon.append((name, row["type"], row["id"]))
        recipes = engine.select_recipes(self.document.get("document_type"), self.document.get("kind"), concepts)
        cues = {}

        def add(block_id, start, end, cue):
            cues.setdefault(block_id, []).append({**cue, "_span": (start, end)})

        for block in self.block_rows:
            text = block["text"]
            if not text.strip():
                continue
            folded = nfkc(text).casefold()
            for term, concept_id, predicate_id in unique_terms:
                if predicate_id is None and block["type"] in ("title", "heading"):
                    continue
                needle = nfkc(term).casefold()
                position = folded.find(needle)
                if position < 0:
                    continue
                if needle.isascii() and not mentioned(term, text):
                    continue
                cue = {"type": "term", "match": text[position:position + len(needle)] if len(nfkc(text)) == len(text) else term, "concept": concept_id}
                if predicate_id:
                    cue["predicate"] = predicate_id
                add(block["id"], position, position + len(needle), cue)
            for pattern, concept_id, predicate_id in patterns:
                try:
                    for match in re.finditer(pattern, text):
                        if match.group(0):
                            cue = {"type": "value_pattern", "match": match.group(0)[:200], "concept": concept_id}
                            if predicate_id:
                                cue["predicate"] = predicate_id
                            add(block["id"], match.start(), match.end(), cue)
                except re.error:
                    continue
            for word in unit_words:
                for match in re.finditer(r"\d+(?:\.\d+)?\s*" + re.escape(word), text):
                    add(block["id"], match.start(), match.end(), {"type": "unit", "match": match.group(0)})
            for name, concept_id, node_id in lexicon:
                if mentioned(name, text):
                    position = folded.find(nfkc(name).casefold())
                    span = (position, position + len(name)) if position >= 0 else (0, len(text))
                    add(block["id"], span[0], span[1], {"type": "lexicon", "match": name[:200], "concept": concept_id, "node_id": node_id})
            for recipe in recipes:
                for mapping in recipe["mappings"]:
                    selector = mapping["selector"]
                    subject, offset = None, 0
                    if selector["kind"] == "heading" and block["type"] in ("heading", "title"):
                        subject = text
                    elif selector["kind"] == "table_header" and block["type"] == "table_cell" and (
                            (block.get("table") or {}).get("header") or (block.get("table") or {}).get("col") == 0):
                        subject = text.strip()
                        offset = text.find(subject)
                    elif selector["kind"] == "key_value_label" and block["type"] in ("paragraph", "key_value", "list_item", "table_cell"):
                        match = re.match(r"\s*([^：:]{1,40})[：:]", text)
                        if match:
                            subject, offset = match.group(1).strip(), match.start(1)
                    elif selector["kind"] == "section_path" and block.get("heading_path"):
                        subject = " / ".join(block["heading_path"])
                    elif selector["kind"] == "speaker" and block.get("speaker"):
                        subject = block["speaker"]
                    if subject is None:
                        continue
                    try:
                        match = re.search(selector["pattern"], subject)
                    except re.error:
                        continue
                    if not match:
                        continue
                    cue = {"type": "recipe", "match": match.group(0)[:200] or subject[:200], "concept": mapping["concept"]}
                    if mapping.get("predicate"):
                        cue["predicate"] = mapping["predicate"]
                    in_text = selector["kind"] in ("heading", "table_header", "key_value_label")
                    start = offset + match.start() if in_text else 0
                    end = offset + match.end() if in_text else len(text)
                    add(block["id"], start, max(end, start + 1), cue)
        return cues
