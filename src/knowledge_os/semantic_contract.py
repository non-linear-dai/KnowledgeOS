"""Versioned, read-only standards projection of the canonical control plane.

RDF and SHACL are generated from Git truth. No remote contexts, imports,
user-authored queries, JS or inference are executed by this application profile.
"""
from __future__ import annotations

import copy
import json
import re
from decimal import Decimal
from urllib.parse import quote, unquote, urlsplit

from rdflib import Graph, Literal, Namespace, RDF, RDFS, URIRef, XSD
from rdflib.collection import Collection
from rdflib.namespace import SH, SKOS, PROV

from .core import ValidationError, digest
from .values import validate_value

FORMAT = "knowledgeos.semantic-profile.v1"
DEFAULT_PROFILE = {
    "format": FORMAT, "version": "1.0.0", "base_iri": "urn:knowledgeos:",
    "validation": "enforce", "max_document_values": 10000,
    "standards": {"rdf": "1.1", "rdfs": "1.1", "json_ld": "1.1", "shacl": "2017", "skos": "2009", "prov_o": "2013"},
}
KINDS = {"concept", "predicate", "relation", "entity", "assertion", "source", "activity", "agent", "shape", "term", "scheme", "value", "extension"}
DATATYPES = {"string": XSD.string, "text": XSD.string, "enum": XSD.string,
             "number": XSD.double, "decimal": XSD.decimal, "boolean": XSD.boolean,
             "date": XSD.date, "datetime": XSD.dateTime, "json": RDF.JSON}


def public(value):
    if isinstance(value, dict):
        return {k: public(v) for k, v in value.items() if not k.startswith("_")}
    if isinstance(value, list):
        return [public(v) for v in value]
    return value


def validate_profile(profile):
    if not isinstance(profile, dict) or set(profile) - set(DEFAULT_PROFILE) or profile.get("format") != FORMAT:
        raise ValidationError("unsupported semantic profile fields or format")
    if not re.fullmatch(r"\d+\.\d+\.\d+", str(profile.get("version", ""))):
        raise ValidationError("semantic profile requires a semantic version")
    base = profile.get("base_iri", "")
    if not isinstance(base, str) or urlsplit(base).scheme not in ("urn", "https") or (base.startswith("https:") and not urlsplit(base).netloc) or any(c.isspace() or c in '<>"{}|\\^`' for c in base) or not base.endswith((":", "/", "#")):
        raise ValidationError("base_iri must be an absolute urn/https namespace")
    if profile.get("validation") not in ("enforce", "off"):
        raise ValidationError("semantic validation must be enforce or off")
    if type(profile.get("max_document_values")) is not int or not 1 <= profile["max_document_values"] <= 10000:
        raise ValidationError("invalid semantic document budget")
    if profile.get("standards") != DEFAULT_PROFILE["standards"]:
        raise ValidationError("unsupported semantic standards configuration")


def validate_vocabularies(vocabularies, ontology):
    concepts = {c["id"] for c in ontology.get("concept_types", [])}
    for identifier, scheme in vocabularies.items():
        if not isinstance(scheme, dict) or not isinstance(identifier, str) or scheme.get("format") != "knowledgeos.vocabulary.v1" or scheme.get("id") != identifier or not re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", identifier):
            raise ValidationError("invalid vocabulary format or id")
        if set(public(scheme)) - {"format", "id", "version", "label", "terms"}:
            raise ValidationError("unsupported vocabulary fields")
        if not re.fullmatch(r"\d+\.\d+\.\d+", str(scheme.get("version", ""))) or not isinstance(scheme.get("label"), str) or not scheme["label"].strip():
            raise ValidationError("vocabulary requires label and version")
        terms = scheme.get("terms")
        if not isinstance(terms, list) or len(terms) > 500:
            raise ValidationError("vocabulary supports at most 500 terms")
        ids, labels = set(), set()
        for term in terms:
            if not isinstance(term, dict) or set(term) - {"id", "pref_labels", "alt_labels", "definition", "scope_note", "concept_ref", "broader", "close_matches"}:
                raise ValidationError("unsupported vocabulary term fields")
            if not re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", str(term.get("id", ""))) or term["id"] in ids:
                raise ValidationError("term ids must be unique stable identifiers")
            ids.add(term["id"])
            preferred = term.get("pref_labels")
            if not isinstance(preferred, dict) or not preferred:
                raise ValidationError("term requires preferred labels by language")
            languages = set()
            for lang, label in preferred.items():
                if not isinstance(lang, str) or not re.fullmatch(r"[A-Za-z]{2,8}(?:-[A-Za-z0-9]{1,8})*", lang) or not isinstance(label, str) or not label.strip() or lang.lower() in languages:
                    raise ValidationError("invalid vocabulary language or preferred label")
                languages.add(lang.lower())
                key = (lang.lower(), label.casefold().strip())
                if key in labels:
                    raise ValidationError("ambiguous preferred labels in vocabulary")
                labels.add(key)
            if not isinstance(term.get("definition", ""), str) or not term.get("definition", "").strip():
                raise ValidationError("term requires a definition")
            if term.get("concept_ref") and (not isinstance(term["concept_ref"], str) or term["concept_ref"] not in concepts):
                raise ValidationError("term refers to an unregistered concept")
            if not isinstance(term.get("alt_labels", {}), dict):
                raise ValidationError("alternative labels must be grouped by language")
            if not isinstance(term.get("broader", []), list) or any(not isinstance(v, str) for v in term.get("broader", [])):
                raise ValidationError("broader must be a list of term ids")
            if not isinstance(term.get("close_matches", []), list):
                raise ValidationError("close_matches must be a list of IRIs")
            if "scope_note" in term and not isinstance(term["scope_note"], str):
                raise ValidationError("scope_note must be text")
            for lang, values in term.get("alt_labels", {}).items():
                if not isinstance(lang, str) or not re.fullmatch(r"[A-Za-z]{2,8}(?:-[A-Za-z0-9]{1,8})*", lang) or not isinstance(values, list) or any(not isinstance(v, str) or not v.strip() for v in values):
                    raise ValidationError("invalid alternative labels")
                if any(p.strip().casefold() in {v.strip().casefold() for v in values} for language, p in preferred.items() if language.lower() == lang.lower()):
                    raise ValidationError("preferred and alternative labels must differ")
            for external in term.get("close_matches", []):
                if not isinstance(external, str) or not urlsplit(external).scheme or any(c.isspace() or c in '<>"{}|\\^`' for c in external):
                    raise ValidationError("vocabulary mappings require absolute IRIs")
        by_id = {t["id"]: t for t in terms}
        visited = set()
        def visit(key, trail):
            if key in trail:
                raise ValidationError("vocabulary hierarchy must be acyclic in this profile")
            if key in visited:
                return
            for parent in by_id[key].get("broader", []):
                if parent not in by_id:
                    raise ValidationError("unknown broader term")
                visit(parent, trail | {key})
            visited.add(key)
        for key in by_id:
            visit(key, set())


def validate_skill_success(criteria):
    allowed = {"requires_claims", "requires_citations", "requires_model_trace"}
    if not isinstance(criteria, dict) or set(criteria) - allowed or any(type(v) is not bool for v in criteria.values()):
        raise ValidationError("skill success criteria support boolean requires_claims, requires_citations, requires_model_trace only")


def validate_ontology(ontology, predicates):
    for key in ("concept_types", "relation_types"):
        items = ontology.get(key, [])
        ids = [item.get("id") for item in items]
        if any(not isinstance(i, str) or not i for i in ids) or len(ids) != len(set(ids)):
            raise ValidationError(f"{key} require unique stable ids")
    concepts = {c["id"] for c in ontology.get("concept_types", [])}
    for concept in ontology.get("concept_types", []):
        seen = set()
        for binding in concept.get("properties", []):
            identifier = binding.get("predicate")
            if identifier not in predicates or identifier in seen:
                raise ValidationError(f"{concept['id']}: binding requires a unique registered predicate: {identifier}")
            seen.add(identifier)
            if type(binding.get("required", False)) is not bool or binding.get("cardinality", "inherit") not in ("inherit", "one", "many", "temporal_many"):
                raise ValidationError("invalid concept property binding")
    for relation in ontology.get("relation_types", []):
        if relation.get("mode", "simple") not in ("simple", "reified", "reifiable"):
            raise ValidationError("invalid relation mode")
        for endpoint in relation.get("connections", []):
            if endpoint.get("source_type") not in concepts or endpoint.get("target_type") not in concepts:
                raise ValidationError("relation endpoints require registered concept types")
    for predicate in predicates.values():
        value = predicate.get("value", {})
        if value.get("type") not in (*DATATYPES, "node_ref", "quantity", "currency") or value.get("cardinality") not in ("one", "many", "temporal_many") or predicate.get("storage", {}).get("mode") not in ("attr", "assertion", "external"):
            raise ValidationError(f"{predicate['id']}: invalid predicate value or storage contract")
        if value.get("type") == "enum" and (not isinstance(value.get("allowed"), list) or not value["allowed"] or any(not isinstance(v, str) for v in value["allowed"])):
            raise ValidationError("enum predicate requires allowed strings")


class SemanticContract:
    def __init__(self, registry):
        self.registry = registry
        self.profile = registry.semantic_profile
        self.k = Namespace(self.profile["base_iri"] + "extension/")

    def iri(self, kind, identifier):
        if kind not in KINDS or not isinstance(identifier, str) or not identifier:
            raise ValidationError("IRI requires a supported kind and nonempty id")
        return URIRef(self.profile["base_iri"] + kind + "/" + quote(identifier, safe=""))

    def decode_iri(self, value):
        prefix = self.profile["base_iri"]
        if not str(value).startswith(prefix):
            raise ValidationError("IRI is outside the configured namespace")
        kind, separator, identifier = str(value)[len(prefix):].partition("/")
        result = unquote(identifier)
        if not separator or str(self.iri(kind, result)) != str(value):
            raise ValidationError("noncanonical IRI")
        return {"kind": kind, "id": result}

    def intermediate(self):
        return {"format": "knowledgeos.semantic-ir.v1", "profile": public(self.profile),
                "concepts": public(self.registry.ontology.get("concept_types", [])),
                "relations": public(self.registry.ontology.get("relation_types", [])),
                "predicates": self.registry._public(self.registry.predicates),
                "vocabularies": self.registry._public(self.registry.vocabularies)}

    def summary(self):
        ir = self.intermediate()
        return {**ir, "fingerprint": digest(ir), "registry_fingerprint": self.registry.fingerprint,
                "capabilities": {"control_export": ["turtle", "json-ld"], "shacl": "generated-core-subset",
                                 "validation": self.profile["validation"], "durable_writes": "changeset_only",
                                 "inference": False, "remote_imports": False},
                "native_extensions": ["temporal_cardinality", "provenance_tiers", "authority", "units", "reification_identity", "business_rules"]}

    def graph(self):
        graph = Graph()
        for prefix, namespace in (("kos", self.k), ("sh", SH), ("skos", SKOS), ("prov", PROV), ("rdfs", RDFS)):
            graph.bind(prefix, namespace)
        return graph

    def _list(self, graph, identifier, values):
        head = self.iri("value", identifier)
        Collection(graph, head, values)
        return head

    def shapes(self):
        graph = self.graph()
        for concept in self.registry.ontology.get("concept_types", []):
            shape = self.iri("shape", concept["id"])
            graph.add((shape, RDF.type, SH.NodeShape))
            graph.add((shape, SH.targetClass, self.iri("concept", concept["id"])))
            for binding in concept.get("properties", []):
                predicate = self.registry.predicates[binding["predicate"]]
                prop = self.iri("shape", concept["id"] + "/" + predicate["id"])
                graph.add((shape, SH.property, prop))
                graph.add((prop, SH.path, self.iri("predicate", predicate["id"])))
                graph.add((prop, SH.name, Literal(predicate.get("label", predicate["id"]))))
                graph.add((prop, self.k.sourcePredicate, Literal(predicate["id"])))
                if binding.get("required"):
                    graph.add((prop, SH.minCount, Literal(1)))
                cardinality = binding.get("cardinality", "inherit")
                if cardinality == "inherit":
                    cardinality = predicate.get("value", {}).get("cardinality")
                if cardinality == "one":
                    graph.add((prop, SH.maxCount, Literal(1)))
                elif cardinality == "temporal_many":
                    graph.add((prop, self.k.nativeConstraint, Literal("temporal_cardinality")))
                value_shape = prop
                if predicate.get("storage", {}).get("mode") != "attr":
                    value_shape = self.iri("shape", concept["id"] + "/" + predicate["id"] + "/value")
                    graph.add((prop, SH.node, value_shape))
                    graph.add((value_shape, SH.property, self.iri("shape", str(value_shape) + "/literal")))
                    value_shape = self.iri("shape", str(value_shape) + "/literal")
                    graph.add((value_shape, SH.path, self.k.value))
                    graph.add((value_shape, SH.minCount, Literal(1)))
                    graph.add((value_shape, SH.maxCount, Literal(1)))
                definition = predicate.get("value", {})
                typ = definition.get("type")
                if typ in DATATYPES:
                    graph.add((value_shape, SH.datatype, DATATYPES[typ]))
                elif typ == "node_ref":
                    graph.add((value_shape, SH.nodeKind, SH.IRI))
                else:
                    graph.add((value_shape, self.k.nativeConstraint, Literal("typed_quantity")))
                if definition.get("allowed"):
                    graph.add((value_shape, SH["in"], self._list(graph, str(prop) + "/enum", [Literal(v, datatype=DATATYPES.get(typ), normalize=False) for v in definition["allowed"]])))
                if typ in ("number", "decimal"):
                    for field, constraint in (("minimum", SH.minInclusive), ("maximum", SH.maxInclusive)):
                        if definition.get(field) is not None:
                            graph.add((value_shape, constraint, Literal(Decimal(str(definition[field])))))
        return graph

    def control_graph(self):
        graph = self.graph()
        for concept in self.registry.ontology.get("concept_types", []):
            subject = self.iri("concept", concept["id"])
            graph.add((subject, RDF.type, RDFS.Class))
            graph.add((subject, RDFS.label, Literal(concept.get("label", concept["id"]))))
            graph.add((subject, RDFS.comment, Literal(concept.get("description", ""))))
        for kind, items in (("predicate", self.registry.predicates.values()), ("relation", self.registry.ontology.get("relation_types", []))):
            for item in items:
                subject = self.iri(kind, item["id"])
                graph.add((subject, RDF.type, RDF.Property))
                graph.add((subject, RDFS.label, Literal(item.get("label", item["id"]))))
                graph.add((subject, RDFS.comment, Literal(item.get("description") or item.get("semantics", {}).get("description", ""))))
                if kind == "relation":
                    for index, connection in enumerate(item.get("connections", [])):
                        endpoint = self.iri("value", item["id"] + "/endpoint/" + str(index))
                        graph.add((subject, self.k.allowedConnection, endpoint))
                        graph.add((endpoint, self.k.sourceType, self.iri("concept", connection["source_type"])))
                        graph.add((endpoint, self.k.targetType, self.iri("concept", connection["target_type"])))
        graph += self.shapes()
        for identifier, vocabulary in self.registry.vocabularies.items():
            scheme = self.iri("scheme", identifier)
            graph.add((scheme, RDF.type, SKOS.ConceptScheme))
            graph.add((scheme, RDFS.label, Literal(vocabulary["label"])))
            for term in vocabulary["terms"]:
                subject = self.iri("term", identifier + "/" + term["id"])
                graph.add((subject, RDF.type, SKOS.Concept))
                graph.add((subject, SKOS.inScheme, scheme))
                for lang, label in term["pref_labels"].items():
                    graph.add((subject, SKOS.prefLabel, Literal(label, lang=lang)))
                for lang, labels in term.get("alt_labels", {}).items():
                    for label in labels:
                        graph.add((subject, SKOS.altLabel, Literal(label, lang=lang)))
                graph.add((subject, SKOS.definition, Literal(term["definition"])))
                if term.get("scope_note"):
                    graph.add((subject, SKOS.scopeNote, Literal(term["scope_note"])))
                if term.get("concept_ref"):
                    graph.add((subject, self.k.describesClass, self.iri("concept", term["concept_ref"])))
                for parent in term.get("broader", []):
                    graph.add((subject, SKOS.broader, self.iri("term", identifier + "/" + parent)))
                for match in term.get("close_matches", []):
                    graph.add((subject, SKOS.closeMatch, URIRef(match)))
        return graph

    def export(self, format="turtle"):
        if format not in ("turtle", "json-ld"):
            raise ValidationError("supported exports are turtle and json-ld")
        graph = self.control_graph()
        return {"format": format, "content": graph.serialize(format=format), "triples": len(graph),
                "registry_fingerprint": self.registry.fingerprint, "write_performed": False}

    def export_provenance(self, service, format, identifier=None):
        if format not in ("turtle", "json-ld"):
            raise ValidationError("supported exports are turtle and json-ld")
        graph = self.graph()
        nodes = service.db.execute("SELECT * FROM node" + (" WHERE id=?" if identifier else ""), (identifier,) if identifier else ())
        if identifier and not nodes:
            from .core import NotFoundError
            raise NotFoundError(f"node not found: {identifier}")
        for node in nodes:
            entity = self.iri("entity", node["id"])
            graph.add((entity, RDF.type, PROV.Entity))
            graph.add((entity, RDF.type, self.iri("concept", node["type"])))
            graph.add((entity, RDFS.label, Literal(node["label"])))
            graph.add((entity, self.k.sourceHash, Literal(node["source_hash"])))
            if node["source_path"]:
                source = self.iri("source", node["source_path"])
                graph.add((source, RDF.type, PROV.Entity))
                graph.add((source, self.k.locator, Literal(node["source_path"])))
                graph.add((entity, PROV.wasDerivedFrom, source))
            for assertion in service.db.execute("SELECT * FROM assertion WHERE node_id=?", (node["id"],)):
                subject = self.iri("assertion", assertion["id"])
                graph.add((subject, RDF.type, PROV.Entity))
                graph.add((subject, self.k.subject, entity))
                graph.add((subject, self.k.predicate, self.iri("predicate", assertion["predicate"])))
                graph.add((subject, self.k.status, Literal(assertion["status"])))
                graph.add((subject, self.k.provenanceTier, Literal(assertion["provenance_tier"])))
                graph.add((subject, self.k.value, self._value(graph, self.registry.predicates[assertion["predicate"]], json.loads(assertion["value_json"]), assertion["id"])))
                for field in ("observed_at", "valid_from", "valid_to"):
                    if assertion[field]:
                        graph.add((subject, self.k[field], Literal(assertion[field], datatype=XSD.dateTime)))
                for reference in json.loads(assertion["source_refs_json"]):
                    source = self.iri("source", reference)
                    graph.add((source, RDF.type, PROV.Entity))
                    record = service.db.first("SELECT locator,source_hash,source_version FROM source_ref WHERE id=?", (reference,))
                    graph.add((source, self.k.locator, Literal(record["locator"] if record else reference)))
                    if record:
                        graph.add((source, self.k.sourceHash, Literal(record["source_hash"])))
                        if record["source_version"]:
                            graph.add((source, self.k.sourceVersion, Literal(record["source_version"])))
                    graph.add((subject, PROV.wasDerivedFrom, source))
                for reference in json.loads(assertion["evidence_refs_json"]):
                    source = self.iri("source", reference)
                    graph.add((source, RDF.type, PROV.Entity))
                    graph.add((source, self.k.locator, Literal(reference)))
                    graph.add((subject, self.k.evidence, source))
                if assertion["supersedes"]:
                    graph.add((subject, PROV.wasRevisionOf, self.iri("assertion", assertion["supersedes"])))
        if not identifier:
            for row in service.db.execute("SELECT * FROM changeset"):
                activity = self.iri("activity", row["id"])
                actor = self.iri("agent", row["actor"])
                graph.add((activity, RDF.type, PROV.Activity))
                graph.add((actor, RDF.type, PROV.Agent))
                graph.add((activity, PROV.wasAssociatedWith, actor))
                graph.add((activity, PROV.startedAtTime, Literal(row["created_at"], datatype=XSD.dateTime)))
                graph.add((activity, self.k.status, Literal(row["status"])))
                if row["published_at"]:
                    graph.add((activity, PROV.endedAtTime, Literal(row["published_at"], datatype=XSD.dateTime)))
        return {"format": format, "scope": "provenance", "content": graph.serialize(format=format),
                "triples": len(graph), "registry_fingerprint": self.registry.fingerprint,
                "native_metadata_preserved": True, "write_performed": False}

    def document_graph(self, document, assertions):
        graph = self.graph()
        node = document["base"]["node"]
        subject = self.iri("entity", node["id"])
        graph.add((subject, RDF.type, self.iri("concept", node["type"])))
        count = 0
        for identifier, raw in document.get("knowledge", {}).get("attrs", {}).items():
            predicate = self.registry.predicates[identifier]
            values = raw if isinstance(raw, list) and predicate["value"]["cardinality"] == "many" else [raw]
            for index, value in enumerate(values):
                graph.add((subject, self.iri("predicate", identifier), self._value(graph, predicate, value, node["id"] + "/" + identifier + "/" + str(index))))
                count += 1
        for assertion in assertions:
            identifier = assertion["predicate"]
            value_node = self.iri("assertion", assertion["id"])
            graph.add((subject, self.iri("predicate", identifier), value_node))
            graph.add((value_node, self.k.value, self._value(graph, self.registry.predicates[identifier], assertion["value"], assertion["id"])))
            count += 1
        if count > self.profile["max_document_values"]:
            raise ValidationError("semantic document exceeds value budget")
        return graph

    def _value(self, graph, predicate, raw, key):
        # Native validation owns units and JSON contracts; SHACL checks its
        # declared scalar subset on this normalized validation view.
        definition = {**predicate, "value": {**predicate["value"], "cardinality": "one"}}
        value = validate_value(definition, raw, self.registry.units, self.registry.currencies)
        typ = predicate["value"]["type"]
        literal = value.get("literal", value.get("ref", value)) if isinstance(value, dict) else value
        if typ == "node_ref":
            return self.iri("entity", literal)
        if typ in ("quantity", "currency"):
            subject = self.iri("value", key)
            graph.add((subject, self.k.amount, Literal(Decimal(str(literal)))))
            graph.add((subject, self.k.unit, Literal(value["unit"])))
            return subject
        if typ == "json":
            literal = json.dumps(value, ensure_ascii=False, sort_keys=True)
        return Literal(literal, datatype=DATATYPES[typ], normalize=False)

    def validate_document(self, document, assertions):
        from pyshacl import validate
        graph = self.document_graph(document, assertions)
        conforms, report, _ = validate(graph, shacl_graph=self.shapes(), inference="none",
                                      advanced=False, js=False, do_owl_imports=False, inplace=False)
        issues = []
        if not isinstance(report, Graph):
            raise ValidationError("SHACL validation did not return a report")
        for result in report.subjects(RDF.type, SH.ValidationResult):
            issues.append({"focus": str(report.value(result, SH.focusNode)),
                           "path": str(report.value(result, SH.resultPath) or ""),
                           "message": str(report.value(result, SH.resultMessage) or "Constraint failed")})
        return {"conforms": bool(conforms), "issues": sorted(issues, key=lambda x: (x["focus"], x["path"], x["message"])),
                "scope": "canonical-property-subset", "native_checks_required": True, "write_performed": False}

    def terminology(self, concept_id):
        return [{"scheme": identifier, **copy.deepcopy(term)} for identifier, vocabulary in self.registry.vocabularies.items()
                for term in vocabulary["terms"] if term.get("concept_ref") == concept_id]
