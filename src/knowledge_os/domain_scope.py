"""Read-only workspace scopes derived from registered domain packs.

Scopes are a browsing projection, never membership facts or an access policy.
Follow declared dependencies outward; shared predicates must not pull other
domains' concepts or models into the selected workspace.
"""
from .core import ValidationError


def domain_scope(registry, identifier):
    pack = registry.domains.get(identifier)
    if pack is None:
        raise ValidationError(f"unknown work domain: {identifier}")
    concepts = {item["id"]: item for item in registry.ontology.get("concept_types", [])}
    relations = {item["id"]: item for item in registry.ontology.get("relation_types", [])}
    retrieval = pack.get("retrieval", {})
    scope = {
        "concept": set(pack.get("concept_scopes", [])) & concepts.keys(),
        "relation": set(retrieval.get("relation_types", [])) & relations.keys(),
        "predicate": set(retrieval.get("predicate_priority", [])) & registry.predicates.keys(),
        "model": set(pack.get("required_models", [])) & registry.models.keys(),
        "business_constraint": set(), "business_rule": set(),
    }

    def model_dependencies(item):
        if item.get("applies_to") in concepts:
            scope["concept"].add(item["applies_to"])
        scope["predicate"].update(value for value in [item.get("output_predicate"),
            *(entry.get("predicate") for entry in item.get("inputs", []))] if value in registry.predicates)

    for identifier in scope["model"]:
        model_dependencies(registry.models[identifier])
    for identifier in scope["concept"]:
        scope["predicate"].update(binding["predicate"] for binding in concepts[identifier].get("properties", [])
                                  if binding["predicate"] in registry.predicates)
    for identifier in scope["relation"]:
        scope["predicate"].update(binding["predicate"] for binding in relations[identifier].get("reification", {}).get("properties", [])
                                  if binding["predicate"] in registry.predicates)
    for kind, definitions in (("business_constraint", registry.constraints), ("business_rule", registry.rules)):
        for identifier, item in definitions.items():
            subjects = {value for key, value in item.get("scope", {}).items() if key in ("subject_concept", "candidate_concept") and value}
            subjects.update(item.get("scope", {}).get("context_concepts", {}).values())
            relation = item.get("scope", {}).get("relation_type")
            if subjects and subjects <= scope["concept"] and (not relation or relation in scope["relation"]):
                scope[kind].add(identifier)
                scope["predicate"].update(entry["predicate"] for entry in item.get("inputs", [])
                                          if entry.get("predicate") in registry.predicates)
    return {kind: sorted(identifiers) for kind, identifiers in scope.items()}


def domain_definition_ids(scope):
    prefixes = {"model": "model:", "business_constraint": "business_constraint:", "business_rule": "business_rule:"}
    return sorted(prefixes.get(kind, "") + identifier for kind, identifiers in scope.items() for identifier in identifiers)
