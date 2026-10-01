"""Shared contracts and control-plane loader."""
from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import yaml
import jsonschema


class KnowledgeError(Exception):
    pass


class ValidationError(KnowledgeError):
    pass


class NotFoundError(KnowledgeError):
    pass


class ConflictError(KnowledgeError):
    pass


class AuthenticationError(KnowledgeError):
    pass


class AuthorizationError(KnowledgeError):
    pass


class IntegrityError(KnowledgeError):
    pass


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def canonical(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): canonical(value[key]) for key in sorted(value, key=str)}
    if isinstance(value, (list, tuple)):
        return [canonical(item) for item in value]
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return value


def json_text(value: Any, *, sorted_keys: bool = False) -> str:
    return json.dumps(canonical(value) if sorted_keys else value, ensure_ascii=False, separators=(",", ":"))


def digest(value: Any) -> str:
    return hashlib.sha256(json_text(value, sorted_keys=True).encode()).hexdigest()


def load_yaml(path: Path) -> dict:
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8-sig")) or {}
    except (OSError, yaml.YAMLError) as exc:
        raise ValidationError(f"{path}: invalid YAML: {exc}") from exc
    if not isinstance(data, dict):
        raise ValidationError(f"{path}: YAML root must be an object")
    return canonical(data)


def frontmatter(path: Path) -> tuple[dict, str]:
    text = path.read_text(encoding="utf-8-sig")
    if not text.startswith("---\n"):
        raise ValidationError(f"{path}: missing YAML frontmatter")
    end = text.find("\n---", 4)
    if end < 0:
        raise ValidationError(f"{path}: unterminated YAML frontmatter")
    try:
        data = yaml.safe_load(text[4:end]) or {}
    except yaml.YAMLError as exc:
        raise ValidationError(f"{path}: invalid YAML: {exc}") from exc
    if not isinstance(data, dict):
        raise ValidationError(f"{path}: frontmatter must be an object")
    return canonical(data), text[end + 4:].lstrip("\n")


SCHEMA_KEYWORDS = {"$schema", "$id", "$ref", "$defs", "title", "description", "default", "examples", "type", "const", "enum",
                   "properties", "required", "additionalProperties", "items", "minItems", "maxItems", "uniqueItems",
                   "minLength", "maxLength", "pattern", "format", "minimum", "maximum", "exclusiveMinimum",
                   "exclusiveMaximum", "multipleOf", "oneOf", "anyOf", "allOf", "not"}


def check_schema(schema):
    if isinstance(schema, bool):
        return
    if not isinstance(schema, dict):
        raise ValidationError("schema must be an object or boolean")
    unknown = set(schema) - SCHEMA_KEYWORDS
    if unknown:
        raise ValidationError("unsupported schema keywords: " + ", ".join(sorted(unknown)))
    types = schema.get("type", [])
    if isinstance(types, str):
        types = [types]
    if set(types) - {"object", "array", "string", "integer", "number", "boolean", "null"}:
        raise ValidationError("unsupported schema type")
    if schema.get("format") and schema["format"] not in ("date", "date-time", "uri"):
        raise ValidationError(f"unsupported format {schema['format']}")
    if schema.get("$ref") and not schema["$ref"].startswith("#/"):
        raise ValidationError("only local JSON pointers are supported")
    for key in ("properties", "$defs"):
        for child in schema.get(key, {}).values():
            check_schema(child)
    for key in ("items", "additionalProperties", "not"):
        if key in schema:
            check_schema(schema[key])
    for key in ("oneOf", "anyOf", "allOf"):
        for child in schema.get(key, []):
            check_schema(child)
    jsonschema.Draft202012Validator.check_schema(schema)


@dataclass(frozen=True)
class Config:
    root: Path

    def __init__(self, root: str | Path | None = None):
        object.__setattr__(self, "root", Path(root or os.environ.get("KNOWLEDGEOS_ROOT", os.getcwd())).resolve())

    @property
    def control(self) -> Path:
        return self.root / "control"

    @property
    def knowledge(self) -> Path:
        return self.root / "knowledge"

    @property
    def runtime(self) -> Path:
        return self.root / "runtime"

    def ensure_runtime(self) -> None:
        self.runtime.mkdir(parents=True, exist_ok=True)


class Registry:
    def __init__(self, config: Config):
        self.config = config
        self.reload()

    def _keyed(self, pattern: str, key: str = "id") -> dict:
        result = {}
        for path in sorted(self.config.control.glob(pattern)):
            item = load_yaml(path)
            identifier = item.get(key)
            if not identifier or identifier in result:
                raise ValidationError(f"{path}: missing or duplicate {key}: {identifier}")
            item["_path"] = str(path)
            result[identifier] = item
        return result

    def reload(self) -> None:
        snapshot = self.__dict__.copy()
        try:
            self._reload()
        except Exception:
            self.__dict__.clear()
            self.__dict__.update(snapshot)
            raise

    def _reload(self) -> None:
        before = self.source_fingerprint()
        self.predicates = self._keyed("predicates/**/*.yaml")
        self.models = self._keyed("models/**/*.yaml")
        self.units = self._keyed("units/**/*.yaml")
        self.currencies = self._keyed("currencies/**/*.yaml")
        self.domains = self._keyed("domains/*/pack.yaml")
        self.retrieval_profiles = {p.parent.name: load_yaml(p) for p in sorted(self.config.control.glob("domains/*/retrieval_profile.yaml"))}
        self.schemas = {p.stem.replace(".schema", ""): json.loads(p.read_text()) for p in sorted(self.config.control.glob("schemas/*.json"))}
        for schema in self.schemas.values():
            check_schema(schema)
        self.policies = {p.stem: load_yaml(p) for p in sorted(self.config.control.glob("policies/*.yaml"))}
        self.constraints = self._keyed("constraints/**/*.yaml")
        self.rules = self._keyed("rules/**/*.yaml")
        self.connectors = {}
        for path in sorted((self.config.root / "connectors").glob("**/*.mapping.yaml")):
            name = path.name.removesuffix(".mapping.yaml")
            self.connectors[name] = {**load_yaml(path), "_name": name, "_path": str(path)}
        self.extraction_profiles = self._keyed("extraction/**/*.yaml")
        self.skills = {}
        for path in sorted(self.config.control.glob("skills/*/contract.yaml")):
            contract = load_yaml(path)
            skill_path = path.parent / "SKILL.md"
            metadata, instructions = frontmatter(skill_path)
            identifier = contract.get("id")
            if identifier != path.parent.name or metadata.get("name") != identifier:
                raise ValidationError(f"{path}: skill identifiers must match")
            contract.update({"description": metadata.get("description", ""),
                             "package": {"format": "portable-skill-directory", "path": str(path.parent.relative_to(self.config.root)), "entrypoint": "SKILL.md", "contract": "contract.yaml"},
                             "agent": {"name": metadata["name"], "metadata": metadata.get("metadata", {}), "instructions": instructions.strip()}})
            self.skills[identifier] = contract
        ontology = {}
        for path in sorted(self.config.control.glob("ontology/*.yaml")):
            for key, value in load_yaml(path).items():
                if isinstance(value, list):
                    if key in ("concept_types", "relation_types"):
                        value = [{**item, "_source_path": str(path.relative_to(self.config.root))} if isinstance(item, dict) else item for item in value]
                    ontology.setdefault(key, []).extend(value)
                elif isinstance(value, dict):
                    ontology.setdefault(key, {}).update(value)
                else:
                    ontology[key] = value
        self.ontology = ontology
        from .model_contract import validate_model
        from .units import validate_currencies, validate_units
        validate_units(self.units)
        validate_currencies(self.currencies)
        for identifier, predicate in self.predicates.items():
            value = predicate.get("value", {})
            dimension = value.get("dimension")
            if value.get("type") == "currency":
                declared = list(value.get("units") or [])
                if value.get("default_unit"):
                    declared.append(value["default_unit"])
                if any(unit not in self.currencies for unit in declared):
                    raise ValidationError(f"predicate {identifier} has an unregistered currency unit")
            if dimension:
                if value.get("type") != "quantity":
                    raise ValidationError(f"predicate {identifier} dimension requires quantity type")
                if not any(unit["dimension"] == dimension for unit in self.units.values()):
                    raise ValidationError(f"predicate {identifier} has unknown dimension {dimension}")
                allowed = value.get("units") or []
                if not allowed or any(unit not in self.units or self.units[unit]["dimension"] != dimension for unit in allowed):
                    raise ValidationError(f"predicate {identifier} has invalid units for {dimension}")
                if value.get("default_unit") and value["default_unit"] not in allowed:
                    raise ValidationError(f"predicate {identifier} default unit is not allowed")
        for model in self.models.values():
            validate_model(model, self.predicates, self.ontology, self.units)
        after = self.source_fingerprint()
        if before != after:
            raise ConflictError("control sources changed while loading; retry")
        self.fingerprint = after

    def source_fingerprint(self):
        paths = sorted([path for path in self.config.control.rglob("*") if path.is_file() and all(not part.startswith(".") for part in path.relative_to(self.config.control).parts)] +
                       [path for suffix in ("yaml", "yml") for path in (self.config.root / "connectors").rglob(f"*.mapping.{suffix}") if path.is_file()])
        return digest([[str(path.relative_to(self.config.root)), hashlib.sha256(path.read_bytes()).hexdigest()] for path in paths])

    @staticmethod
    def _public(values: dict) -> dict:
        return {key: {k: v for k, v in item.items() if not k.startswith("_")} for key, item in values.items()}

    def domain(self, identifier: str) -> dict:
        try:
            return self.domains[identifier]
        except KeyError as exc:
            raise NotFoundError(f"domain pack not found: {identifier}") from exc

    def skill(self, identifier: str) -> dict:
        try:
            return self.skills[identifier]
        except KeyError as exc:
            raise NotFoundError(f"skill not found: {identifier}") from exc

    def skill_bundle(self, identifier: str) -> dict:
        skill = self.skill(identifier)
        directory = self.config.root / skill["package"]["path"]
        return {"id": skill["id"], "package": skill["package"], "definition": skill,
                "files": {name: (directory / name).read_text() for name in ("SKILL.md", "contract.yaml")}}

    def freshness_days(self, predicate_id: str) -> int | None:
        predicate = self.predicates.get(predicate_id, {})
        policy_id = predicate.get("policy", {}).get("freshness")
        policies = self.policies.get("freshness", {})
        for value in policies.values():
            if isinstance(value, list):
                for item in value:
                    if item.get("id") == policy_id:
                        return int(item["stale_after_days"]) if item.get("stale_after_days") is not None else None
        return None

    def temperature(self, assertion: dict, now: datetime | None = None) -> str:
        now = now or datetime.now(timezone.utc)
        if assertion.get("status") != "confirmed":
            return "warm"
        def parse(value):
            if not value:
                return None
            return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        observed, start, end = (parse(assertion.get(k)) for k in ("observed_at", "valid_from", "valid_to"))
        if observed and observed > now or start and start > now or end and end <= now:
            return "warm"
        if observed:
            age = (now - observed).days
            retention = self.policies.get("maintenance", {}).get("hot_warm_cold", {}).get("warm_retention_days")
            if retention and age > int(retention):
                return "cold"
            stale = self.freshness_days(assertion["predicate"])
            if stale is not None and age > stale:
                return "warm"
        return "hot"

    def validate_predicate(self, identifier: str) -> dict:
        if identifier not in self.predicates:
            raise ValidationError(f"unregistered predicate: {identifier}")
        return self.predicates[identifier]
