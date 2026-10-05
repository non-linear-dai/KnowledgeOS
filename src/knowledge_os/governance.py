"""ChangeSet expected-result planning and source publication verification."""
from __future__ import annotations

import copy
import hashlib
import hmac
import json
import os
from pathlib import Path
import tempfile

import yaml

from .core import ConflictError, ValidationError, check_schema, digest, frontmatter, json_text, load_yaml


class ChangePlan:
    def __init__(self, service):
        self.service = service
        self.root = service.config.root

    def paths(self, target_source):
        values = [value.strip() for value in str(target_source).split(",") if value.strip()]
        result = {}
        for value in values:
            relative = Path(value)
            if relative.is_absolute() or ".." in relative.parts or not relative.parts or relative.parts[0] not in ("knowledge", "control", "connectors"):
                raise ValidationError("target_source must be under knowledge/, control/, or connectors/")
            absolute = (self.root / relative).resolve()
            allowed_root = (self.root / relative.parts[0]).resolve()
            if not absolute.is_relative_to(allowed_root):
                raise ValidationError("target_source escapes authored roots")
            if absolute.exists() and not absolute.is_file():
                raise ValidationError("target_source must be a file")
            result[value] = absolute if absolute.exists() else None
        if not result:
            raise ValidationError("target_source is required")
        return result

    @staticmethod
    def is_git_source(target_source):
        values = [value.strip() for value in str(target_source).split(",") if value.strip()]
        return bool(values) and all(value.split("/", 1)[0] in ("knowledge", "control", "connectors") for value in values)

    @staticmethod
    def _read(path):
        if path is None:
            return None
        if path.suffix == ".md":
            data, body = frontmatter(path)
            return {"data": data, "body": body}
        if path.suffix in (".yaml", ".yml"):
            return {"data": load_yaml(path), "body": ""}
        if path.suffix == ".json":
            return {"data": json.loads(path.read_text()), "body": ""}
        return {"data": path.read_text(), "body": ""}

    def revision(self, target_source):
        if not self.is_git_source(target_source):
            source = self.service.db.first("SELECT source_version,source_hash FROM source_ref WHERE id=? OR locator=?", (target_source, target_source))
            if not source:
                raise ValidationError(f"target_source cannot be verified: {target_source}")
            return source["source_version"] or source["source_hash"]
        paths = self.paths(target_source)
        manifest = {name: hashlib.sha256(path.read_bytes()).hexdigest() if path else "__missing__" for name, path in sorted(paths.items())}
        if len(manifest) == 1 and next(iter(manifest.values())) != "__missing__":
            return "sha256:" + next(iter(manifest.values()))
        return "sha256:" + digest(manifest)

    def expectation(self, target_source, patch, operations):
        if not self.is_git_source(target_source):
            if not isinstance(patch, dict) or not patch.get("expected_source_hash"):
                raise ValidationError("upstream ChangeSet requires expected_source_hash")
            return {"upstream_hash": patch["expected_source_hash"]}
        documents = self.staged_documents(target_source, patch, operations)
        return {name: digest(document) if document is not None else None for name, document in documents.items()}

    def staged_documents(self, target_source, patch, operations):
        documents = {name: self._read(path) for name, path in self.paths(target_source).items()}
        if isinstance(patch, dict) and patch.get("op") == "template_package":
            if operations:
                raise ValidationError("template package cannot include Studio operations")
            from .templates import TemplateWorkbench
            rendered = TemplateWorkbench(self.service).render(patch.get("template"), patch.get("actor"))
            if set(rendered) != set(documents):
                raise ValidationError("template package paths do not match the ChangeSet targets")
            if any(document is not None for document in documents.values()):
                raise ConflictError("template package versions are immutable")
            documents = rendered
        elif isinstance(patch, dict) and patch.get("op") == "studio_batch":
            if not operations:
                raise ValidationError("studio batch requires operations")
            for operation in operations:
                self._studio(documents, operation)
        elif isinstance(patch, dict) and patch.get("op") == "delete":
            documents = {name: None for name in documents}
        else:
            changes = patch if isinstance(patch, list) else [patch]
            if len(documents) != 1 or not all(isinstance(item, dict) and item.get("op") in ("add", "replace", "remove") and "path" in item for item in changes):
                raise ValidationError("patch must be a verifiable JSON patch or studio batch")
            name = next(iter(documents))
            document = documents[name] or {"data": None, "body": ""}
            if documents[name] is None and changes[0]["path"] != "":
                raise ValidationError("new documents require a root add patch")
            for change in changes:
                document["data"] = self._pointer(document["data"], change)
            documents[name] = document
        return documents

    def apply_approved_template(self, target_source, patch, operations, expected):
        """Atomically materialize an approved, immutable expert template package."""
        if not isinstance(patch, dict) or patch.get("op") != "template_package":
            raise ValidationError("direct template application requires a template package ChangeSet")
        from .templates import serialize_document
        documents = self.staged_documents(target_source, patch, operations)
        if {name: digest(document) for name, document in documents.items()} != expected:
            raise ConflictError("approved template content no longer matches its proposal")
        paths = self.paths(target_source)
        staged, applied = [], []
        try:
            for name, document in documents.items():
                if not name.startswith(("knowledge/templates/", "knowledge/operations/", "knowledge/decisions/")) or not name.endswith(".md"):
                    raise ValidationError("template package path is outside its authored roots")
                target = paths[name] or self.root / name
                target.parent.mkdir(parents=True, exist_ok=True)
                with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=target.parent,
                                                 prefix=".knowledgeos-template-", suffix=".md", delete=False) as stream:
                    stream.write(serialize_document(document))
                    staged.append((Path(stream.name), target))
            if any(target.exists() for _, target in staged):
                raise ConflictError("template source appeared while applying approved package")
            for source, target in staged:
                os.replace(source, target)
                applied.append(target)
            self.verify(target_source, expected)
        except Exception:
            for target in reversed(applied):
                target.unlink(missing_ok=True)
            raise
        finally:
            for source, _ in staged:
                source.unlink(missing_ok=True)
        return self.revision(target_source)

    def apply_approved_control(self, target_source, patch, operations, expected):
        """Materialize reviewed, standalone control definitions into the Git checkout."""
        if not isinstance(patch, dict) or patch.get("op") != "studio_batch" or not operations:
            raise ValidationError("direct source application requires a Studio ChangeSet")
        allowed = {"model": "models", "unit": "units", "currency": "currencies",
                   "business_constraint": "constraints/business", "business_rule": "rules/business"}
        for operation in operations:
            kind = operation.get("targetKind") or operation.get("target_kind")
            definition = operation.get("after") or operation.get("before") or {}
            path = definition.get("sourcePath") or definition.get("source_path")
            if kind not in allowed or operation.get("type") == "delete" or not path or not path.startswith(f"control/{allowed[kind]}/") or not path.endswith(".yaml"):
                raise ValidationError("direct source application supports standalone control YAML only")
        documents = self.staged_documents(target_source, patch, operations)
        actual_expected = {name: digest(document) if document is not None else None for name, document in documents.items()}
        if actual_expected != expected:
            raise ConflictError("approved content no longer matches the staged source")
        paths = self.paths(target_source)
        if any(document is None for document in documents.values()):
            raise ValidationError("direct source application cannot delete control definitions")
        staged = []
        originals = {}
        applied = []
        try:
            for name, document in documents.items():
                target = paths[name] or self.root / name
                if not target.parent.is_dir():
                    raise ValidationError(f"control directory does not exist: {target.parent}")
                originals[target] = target.read_bytes() if target.exists() else None
                with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=target.parent, prefix=".knowledgeos-", suffix=".yaml", delete=False) as stream:
                    yaml.safe_dump(document["data"], stream, allow_unicode=True, sort_keys=False)
                    staged.append((Path(stream.name), target))
            for source, target in staged:
                os.replace(source, target)
                applied.append(target)
        except Exception:
            for target in reversed(applied):
                original = originals[target]
                if original is None:
                    target.unlink(missing_ok=True)
                else:
                    with tempfile.NamedTemporaryFile("wb", dir=target.parent, prefix=".knowledgeos-rollback-", delete=False) as stream:
                        stream.write(original)
                        recovery = Path(stream.name)
                    os.replace(recovery, target)
            raise
        finally:
            for source, _ in staged:
                source.unlink(missing_ok=True)
        self.verify(target_source, expected)
        return self.revision(target_source)

    @staticmethod
    def _pointer(document, operation):
        path = operation["path"]
        if path == "":
            return None if operation["op"] == "remove" else operation["value"]
        if not path.startswith("/"):
            raise ValidationError("JSON pointer must start with /")
        keys = [key.replace("~1", "/").replace("~0", "~") for key in path[1:].split("/")]
        parent = document
        try:
            for key in keys[:-1]:
                parent = parent[int(key)] if isinstance(parent, list) else parent[key]
            key = int(keys[-1]) if isinstance(parent, list) and keys[-1] != "-" else keys[-1]
            if operation["op"] != "add" and (key not in parent if isinstance(parent, dict) else not 0 <= key < len(parent)):
                raise ValidationError("patch target does not exist")
            if operation["op"] == "remove":
                parent.pop(key)
            elif isinstance(parent, list) and operation["op"] == "add":
                parent.insert(len(parent) if key == "-" else key, operation["value"])
            else:
                parent[key] = operation["value"]
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise ValidationError(f"invalid JSON patch: {exc}") from exc
        return document

    def _studio(self, documents, operation):
        after, before = operation.get("after"), operation.get("before")
        definition = after or before
        if not definition:
            raise ValidationError("studio operation requires before/after definition")
        path = definition.get("sourcePath") or definition.get("source_path")
        if path not in documents:
            raise ValidationError("operation source is outside ChangeSet targets")
        kind = operation.get("targetKind") or operation.get("target_kind") or definition.get("kind")
        identifier = operation.get("targetId") or operation.get("target_id") or definition.get("id")
        catalog = next((item for item in self.service.studio()["data"]["definitions"]
                        if item["id"] == identifier and item["kind"] == kind), None)
        if operation.get("type") == "create":
            if catalog:
                raise ConflictError("definition already exists")
        elif not catalog:
            raise ConflictError("definition no longer exists")
        elif catalog["source_path"] != path:
            raise ConflictError("definition source changed")
        if kind == "schema":
            if operation["type"] in ("create", "delete", "deprecate"):
                raise ValidationError("canonical schemas can only be updated")
            original = documents[path]["data"]
            config = after.get("config", {})
            if not isinstance(config, dict):
                raise ValidationError("schema config must be an object")
            locations = {
                "required_root": original,
                "required_base": original.get("properties", {}).get("base", {}),
                "required_node": original.get("properties", {}).get("base", {}).get("properties", {}).get("node", {}),
                "required_knowledge": original.get("properties", {}).get("knowledge", {}),
            }
            mandatory = {
                "required_root": ("base", "knowledge"),
                "required_base": ("schema", "node", "lifecycle", "version"),
                "required_node": ("id", "kind", "type", "key", "label"),
                "required_knowledge": ("attrs", "assertions", "relations", "logic_refs"),
            }
            updated = copy.deepcopy(original)
            destinations = {
                "required_root": updated,
                "required_base": updated["properties"]["base"],
                "required_node": updated["properties"]["base"]["properties"]["node"],
                "required_knowledge": updated["properties"]["knowledge"],
            }
            for field, location in locations.items():
                values = config.get(field)
                if not isinstance(values, list) or any(not isinstance(value, str) for value in values) or len(values) != len(set(values)):
                    raise ValidationError(f"{field} must contain unique field names")
                if not set(mandatory[field]).issubset(values):
                    raise ValidationError(f"{field} cannot remove canonical envelope fields")
                if not set(values).issubset(location.get("properties", {})):
                    raise ValidationError(f"{field} refers to an undefined schema property")
                destinations[field]["required"] = values
            if config.get("version") != catalog["config"].get("version"):
                raise ValidationError("schema version requires a separate contract migration")
            updated["title"] = after.get("label", "")
            updated["description"] = after.get("description", "")
            check_schema(updated)
            documents[path] = {"data": updated, "body": ""}
        elif kind in ("concept", "relation"):
            key = "concept_types" if kind == "concept" else "relation_types"
            documents[path] = documents[path] or {"data": {key: []}, "body": ""}
            entries = documents[path]["data"].setdefault(key, [])
            original = next((item for item in entries if item.get("id") == identifier), {})
            index = next((i for i, item in enumerate(entries) if item.get("id") == identifier), len(entries))
            entries[:] = [item for item in entries if item.get("id") != identifier]
            if operation["type"] != "delete":
                value = {**original, "id": identifier, "label": after.get("label"), "description": after.get("description"),
                         "status": {**original.get("status", {}), "lifecycle": after.get("lifecycle")}}
                if kind == "concept":
                    value["properties"] = [{"predicate": b.get("predicateId") or b.get("predicate_id"), "required": b.get("required", False),
                                            "cardinality": b.get("cardinality", "inherit"), "group": b.get("group", "other")}
                                           for b in after.get("bindings", [])]
                else:
                    value["mode"] = after.get("relationMode") or after.get("relation_mode")
                    value["connections"] = [{"source_type": e.get("sourceConceptId") or e.get("source_concept_id"),
                                             "target_type": e.get("targetConceptId") or e.get("target_concept_id"),
                                             "source_cardinality": e.get("sourceCardinality") or e.get("source_cardinality"),
                                             "target_cardinality": e.get("targetCardinality") or e.get("target_cardinality")}
                                            for e in after.get("endpoints", [])]
                entries.insert(index, value)
        elif kind == "predicate":
            if operation["type"] == "delete":
                documents[path] = None
            else:
                original = documents[path]["data"] if documents[path] else {}
                config = after["config"]
                value_keys = ("dimension", "units", "default_unit", "minimum", "maximum")
                policy = {k: v for k, v in config.items() if k not in ("value_type", "cardinality", "storage_mode", "equivalent_to", *value_keys)}
                value = {**original.get("value", {}), "type": config.get("value_type"), "cardinality": config.get("cardinality")}
                for key in value_keys:
                    if config.get(key) is not None:
                        value[key] = config[key]
                    else:
                        value.pop(key, None)
                documents[path] = {"data": {**original, "id": identifier, "label": after.get("label"),
                                             "semantics": {**original.get("semantics", {}), "description": after.get("description")},
                                             "value": value,
                                             "storage": {"mode": config.get("storage_mode")}, "policy": policy,
                                             "status": {"lifecycle": after.get("lifecycle"), "equivalent_to": config.get("equivalent_to")}}, "body": ""}
        elif kind == "model":
            if operation["type"] == "delete":
                raise ValidationError("published models must be deprecated, not deleted")
            else:
                from .model_contract import validate_model
                model_id = identifier.removeprefix("model:")
                config = {k: v for k, v in after["config"].items() if k != "model_id"}
                config.update({"id": model_id, "description": after.get("description"),
                               "status": {"lifecycle": after.get("lifecycle")}})
                validate_model(config, self.service.registry.predicates, self.service.registry.ontology, self.service.registry.units)
                if operation["type"] != "create":
                    original = documents[path]["data"]
                    if config != original and config.get("version") == original.get("version"):
                        raise ValidationError("model changes require a new semantic version")
                    if tuple(map(int, str(config["version"]).split("."))) <= tuple(map(int, str(original["version"]).split("."))):
                        raise ValidationError("model version must increase")
                documents[path] = {"data": config, "body": ""}
        elif kind == "unit":
            if operation["type"] == "delete":
                raise ValidationError("published physical units must be deprecated, not deleted")
            unit_id = identifier.removeprefix("unit:")
            config = {**after["config"], "id": unit_id, "label": after.get("label"),
                      "status": {"lifecycle": after.get("lifecycle")}}
            from .units import validate_units
            proposed = {**self.service.registry.units, unit_id: config}
            validate_units(proposed)
            original = documents[path]["data"] if documents[path] else None
            if original and config != original:
                old_version = tuple(map(int, str(original.get("version", "1.0.0")).split(".")))
                new_version = tuple(map(int, str(config.get("version", "1.0.0")).split(".")))
                if new_version <= old_version:
                    raise ValidationError("unit changes require a higher semantic version")
            documents[path] = {"data": config, "body": ""}
        elif kind == "currency":
            if operation["type"] == "delete":
                raise ValidationError("published currencies must be deprecated, not deleted")
            currency_id = identifier.removeprefix("currency:")
            config = {**after["config"], "id": currency_id, "label": after.get("label"),
                      "status": {"lifecycle": after.get("lifecycle")}}
            from .units import validate_currencies
            validate_currencies({**self.service.registry.currencies, currency_id: config})
            original = documents[path]["data"] if documents[path] else None
            if original and config != original:
                old_version = tuple(map(int, str(original.get("version", "1.0.0")).split(".")))
                new_version = tuple(map(int, str(config.get("version", "1.0.0")).split(".")))
                if new_version <= old_version:
                    raise ValidationError("currency changes require a higher semantic version")
            documents[path] = {"data": config, "body": ""}
        elif kind in ("business_constraint", "business_rule"):
            from .business_logic import _version, validate_business
            directory = "constraints" if kind == "business_constraint" else "rules"
            business_id = identifier.removeprefix(f"{kind}:")
            expected_path = f"control/{directory}/business/{business_id}.yaml"
            if path != expected_path:
                raise ValidationError("business definition source path does not match its id")
            if operation["type"] == "delete":
                raise ValidationError("published business definitions must be deprecated, not deleted")
            config = {k: v for k, v in after["config"].items() if k not in ("label", "description", "status", "id")}
            config.update({"id": business_id, "label": after.get("label"), "description": after.get("description"),
                           "status": {"lifecycle": after.get("lifecycle")}})
            validate_business(config, kind, self.service.registry.predicates, self.service.registry.ontology, self.service.registry.units)
            original = documents[path]["data"] if documents[path] else None
            if original and digest(config) != digest(original) and _version(config["version"]) <= _version(original["version"]):
                raise ValidationError("business definition changes require a higher semantic version")
            documents[path] = {"data": config, "body": ""}
        else:
            raise ValidationError(f"unsupported editable definition kind {kind}")

    def verify(self, target_source, expected):
        if not self.is_git_source(target_source):
            source = self.service.db.first("SELECT source_hash FROM source_ref WHERE id=? OR locator=?", (target_source, target_source))
            if not source or expected.get("upstream_hash") != source["source_hash"]:
                raise ConflictError("upstream result does not match approved content hash")
            return True
        actual = {name: digest(self._read(path)) if path else None for name, path in self.paths(target_source).items()}
        if actual != expected:
            raise ConflictError("source differs from approved ChangeSet result")
        return True
