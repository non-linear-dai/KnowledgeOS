"""Provider neutral Agent Service and governed tool invocation."""
from __future__ import annotations

import json

from .core import NotFoundError, ValidationError, digest
from .service import envelope


PROTOCOL_VERSION = "knowledgeos.agent.v1"
TOOL_DEFINITIONS = {
    "resolve": ("Resolve an entity or concept by id, key, label, or alias.", ["query"]),
    "get": ("Read a compiled entity card.", ["id"]),
    "query": ("Run a registered query template; arbitrary SQL is never accepted.", ["template"]),
    "neighbors": ("Traverse typed graph relations from one node.", ["id"]),
    "history": ("Read temporal assertions and audit references for a node.", ["id"]),
    "search": ("Search compiled semantic content.", ["query"]),
    "calculate": ("Execute a registered deterministic model and retain its trace.", ["model_id", "inputs"]),
    "calculate_entity": ("Resolve a bound model's inputs from confirmed entity assertions and calculate with source references.", ["model_id", "node_id"]),
    "convert_currency": ("Convert money using a confirmed connector exchange quote at an explicit business time.", ["amount", "quote_node_id", "as_of"]),
    "explain": ("Explain source, provenance and trace.", ["target"]),
    "context": ("Build a domain C-R-L-T-P context packet.", ["id"]),
    "propose": ("Create a governed ChangeSet.", ["actor", "target_source", "patch", "reason"]),
    "review": ("Read the maintenance review queue.", []),
}


class AgentService:
    def __init__(self, service):
        self.service = service
        self.registry = service.registry

    def fingerprint(self):
        return digest({"ontology": self.registry.ontology,
                       "predicates": self.registry._public(self.registry.predicates),
                       "models": self.registry._public(self.registry.models),
                       "units": self.registry._public(self.registry.units),
                       "currencies": self.registry._public(self.registry.currencies),
                       "domains": self.registry._public(self.registry.domains),
                       "skills": list(self.registry.skills.values())})

    def _tools(self, operations):
        result = []
        for operation in dict.fromkeys(operations):
            if operation not in TOOL_DEFINITIONS:
                continue
            description, required = TOOL_DEFINITIONS[operation]
            result.append({"name": operation, "description": description,
                           "input_schema": {"type": "object", "required": required, "properties": {name: {} for name in required}, "additionalProperties": True},
                           "side_effect": "creates_changeset" if operation == "propose" else "read_only"})
        return result

    @staticmethod
    def _schema():
        return {"$schema": "https://json-schema.org/draft/2020-12/schema", "type": "object",
                "required": ["protocol_version", "request_id", "answer", "claims"],
                "properties": {"protocol_version": {"const": PROTOCOL_VERSION}, "request_id": {"type": "string"},
                               "answer": {"type": "string", "minLength": 1},
                               "claims": {"type": "array", "items": {"type": "object", "required": ["statement", "evidence_refs", "confidence"],
                                             "properties": {"statement": {"type": "string"}, "evidence_refs": {"type": "array", "items": {"type": "string"}, "minItems": 1},
                                                            "confidence": {"type": "number", "minimum": 0, "maximum": 1}}, "additionalProperties": False}},
                               "knowledge_gaps": {"type": "array", "items": {"type": "string"}},
                               "recommended_actions": {"type": "array", "items": {"type": "object"}}},
                "additionalProperties": False}

    def capabilities(self, domain=None):
        packs = [self.registry.domain(domain)] if domain else list(self.registry.domains.values())
        allowed = list(dict.fromkeys(operation for pack in packs for operation in pack.get("tool_policy", {}).get("allow", [])))
        domains = []
        for pack in packs:
            domains.append({"id": pack["id"], "label": pack.get("label"), "concept_scopes": pack.get("concept_scopes", []),
                            "default_skill": pack.get("default_skill"), "workflow": pack.get("workflow", []),
                            "retrieval_profile": self.registry.retrieval_profiles.get(pack["id"]),
                            "skills": [skill for skill in self.registry.skills.values() if skill.get("domain") == pack["id"]],
                            "tool_policy": pack.get("tool_policy", {})})
        return {"protocol_version": PROTOCOL_VERSION, "registry_fingerprint": self.fingerprint(),
                "domains": domains, "tools": self._tools(allowed), "response_contract": self._schema(),
                "durable_writes": "changeset_only"}

    def prepare(self, *, question, domain, target=None, query=None, skill=None, as_of=None, max_items=25):
        question = str(question).strip()
        if not question:
            raise ValidationError("agent question is required")
        pack = self.registry.domain(domain)
        selected = self.registry.skill(skill or pack["default_skill"])
        if selected.get("domain") != domain:
            raise ValidationError(f"skill {selected['id']} does not belong to domain {domain}")
        max_items = min(max(int(max_items), 1), 100)
        matches = []
        if target:
            exact = self.service.db.first("SELECT id,type FROM node WHERE id=?", (target,))
            if exact:
                if exact["type"] not in pack.get("concept_scopes", []):
                    raise ValidationError(f"target type {exact['type']} is outside domain {domain} scope")
                target = exact["id"]
            else:
                matches = [row for row in self.service.resolve(target)["data"] if row["type"] in pack.get("concept_scopes", [])]
                if not matches:
                    raise NotFoundError(f"agent target not found: {target}")
                target = matches[0]["id"]
        else:
            profile = self.registry.retrieval_profiles.get(domain, {})
            matches = [row for row in self.service.search(query or question, mode=profile.get("mode", "hybrid"))["data"]
                       if row["type"] in pack.get("concept_scopes", [])][:10]
            target = matches[0]["id"] if len(matches) == 1 else None
        evidence, context = [], {"search_matches": matches}
        for item in matches:
            self._node_evidence(evidence, item["id"])
        if target:
            context["crltp"] = self.service.context(target, domain=domain, max_items=max_items, as_of=as_of)["data"]
            self._node_evidence(evidence, target)
            assertions = self.service.query("current_assertions", {"node_id": target})["data"]
            priorities = pack.get("retrieval", {}).get("predicate_priority", [])
            assertions.sort(key=lambda item: priorities.index(item["predicate"]) if item["predicate"] in priorities else len(priorities))
            for item in assertions[:max_items]:
                evidence.append({"ref": item["id"], "kind": "assertion", "subject": item["node_id"],
                                 "predicate": item["predicate"], "value": item["value"], "temporal": item["temporal"],
                                 "provenance": item["provenance"], "confidence": item["epistemic"]["confidence"]})
            for index, edge in enumerate(context["crltp"]["relations"]["edges"][:max_items]):
                evidence.append({"ref": "relation:" + digest(edge)[:20], "kind": "relation", "value": edge,
                                 "source_refs": [edge["source_path"]] if edge.get("source_path") else [], "ordinal": index})
        evidence = list({item["ref"]: item for item in evidence}.values())
        request = {"protocol_version": PROTOCOL_VERSION, "registry_fingerprint": self.fingerprint(),
                   "question": question, "domain": domain, "skill": selected, "target": target, "as_of": as_of,
                   "reasoning_plan": {"framework": "C-R-L-T-P", "domain_workflow": pack.get("workflow", []),
                                      "skill_id": selected["id"], "skill_instructions": selected.get("agent", {}).get("instructions"),
                                      "deterministic_models": selected.get("deterministic_models", []), "output_contract": selected.get("output")},
                   "context": context, "evidence": evidence, "available_tools": self._tools(pack.get("tool_policy", {}).get("allow", [])),
                   "instructions": ["Return one JSON object matching output_schema.",
                                    "Every claim must cite evidence refs present in this request or a validated tool result.",
                                    "Separate missing information into knowledge_gaps.",
                                    "Use calculate for arithmetic, unit-sensitive values, schedules and policy decisions.",
                                    "Recommendations are not executed. Durable changes require a separate ChangeSet operation."],
                   "output_schema": self._schema()}
        request["request_id"] = "agent-request:" + digest(request)
        request["request_hash"] = digest(request)
        return request

    def _node_evidence(self, evidence, identifier):
        row = self.service.db.first("SELECT id,type,label,attrs_json,lifecycle,source_class,source_path,source_hash FROM node WHERE id=?", (identifier,))
        if row:
            evidence.append({"ref": "node:" + identifier, "kind": "node", "subject": identifier, "type": row["type"],
                             "label": row["label"], "attrs": json.loads(row["attrs_json"]), "lifecycle": row["lifecycle"],
                             "source_class": row["source_class"], "source_refs": [row["source_path"]] if row["source_path"] else [],
                             "source_hash": row["source_hash"]})

    def finalize(self, request, output, tool_results=None):
        if not isinstance(request, dict) or not isinstance(output, dict):
            raise ValidationError("agent request and model output must be objects")
        if request.get("protocol_version") != PROTOCOL_VERSION or request.get("registry_fingerprint") != self.fingerprint():
            raise ValidationError("agent request protocol or registry changed")
        if digest({k: v for k, v in request.items() if k != "request_hash"}) != request.get("request_hash"):
            raise ValidationError("agent request integrity check failed")
        if self.registry.skill(request.get("skill", {}).get("id")).get("domain") != request.get("domain"):
            raise ValidationError("agent request skill does not match its domain")
        if output.get("protocol_version") != PROTOCOL_VERSION or output.get("request_id") != request.get("request_id"):
            raise ValidationError("model output protocol_version or request_id mismatch")
        if not isinstance(output.get("answer"), str) or not output["answer"].strip():
            raise ValidationError("model output answer is required")
        if set(output) - {"protocol_version", "request_id", "answer", "claims", "knowledge_gaps", "recommended_actions"}:
            raise ValidationError("model output has unsupported fields")
        claims = output.get("claims")
        if not isinstance(claims, list):
            raise ValidationError("model output claims must be an array")
        evidence_index = {item["ref"]: item for item in request.get("evidence", [])}
        if not isinstance(tool_results or [], list):
            raise ValidationError("tool_results must be an array")
        permitted_models = self.registry.domain(request["domain"]).get("required_models", [])
        for tool in tool_results or []:
            run_id = tool.get("data", {}).get("run_id")
            if not run_id:
                continue
            row = self.service.db.first("SELECT * FROM derived_result WHERE run_id=?", (run_id,))
            if not row or row["model_id"] not in permitted_models or tool.get("quality_status", {}).get("agent_evidence_ref") != "derived:" + run_id:
                raise ValidationError("invalid deterministic tool evidence")
            evidence_index["derived:" + run_id] = row
        used = []
        for index, claim in enumerate(claims):
            if not isinstance(claim, dict) or set(claim) != {"statement", "evidence_refs", "confidence"}:
                raise ValidationError(f"claim {index} has invalid fields")
            refs = claim["evidence_refs"]
            if not str(claim["statement"]).strip() or not isinstance(refs, list) or not refs:
                raise ValidationError(f"claim {index} must have a statement and cite evidence")
            if any(ref not in evidence_index for ref in refs):
                raise ValidationError(f"claim {index} cites unknown evidence")
            if isinstance(claim["confidence"], bool) or not isinstance(claim["confidence"], (int, float)) or not 0 <= claim["confidence"] <= 1:
                raise ValidationError(f"claim {index} confidence must be between 0 and 1")
            used.extend(refs)
        for key in ("knowledge_gaps", "recommended_actions"):
            if key in output and not isinstance(output[key], list):
                raise ValidationError(f"model output {key} must be an array")
        return {"protocol_version": PROTOCOL_VERSION, "request_id": request["request_id"], "domain": request["domain"],
                "skill_id": request["skill"]["id"], "answer": output["answer"].strip(), "claims": claims,
                "knowledge_gaps": output.get("knowledge_gaps", []), "recommended_actions": output.get("recommended_actions", []),
                "grounding": {"valid": True, "claim_count": len(claims), "cited_evidence_refs": list(dict.fromkeys(used)),
                              "available_evidence_count": len(evidence_index)}, "write_performed": False}

    def invoke(self, *, domain, operation, arguments=None):
        pack = self.registry.domain(domain)
        if operation not in pack.get("tool_policy", {}).get("allow", []):
            raise ValidationError(f"operation {operation} is not allowed for domain {domain}")
        args, service = arguments or {}, self.service
        if operation == "resolve":
            result = service.resolve(args["query"], args.get("scope"))
        elif operation == "get":
            result = service.get(args["id"], args.get("include_history", False))
        elif operation == "query":
            result = service.query(args["template"], args.get("params", {}))
        elif operation == "neighbors":
            result = service.neighbors(args["id"], args.get("relation_types"), args.get("depth", 1), args.get("as_of"))
        elif operation == "history":
            result = service.history(args["id"], args.get("predicate"), args.get("range"))
        elif operation == "search":
            result = service.search(args["query"], args.get("filters", {}), args.get("mode", "hybrid"))
        elif operation == "calculate":
            if args["model_id"] not in pack.get("required_models", []):
                raise ValidationError(f"model {args['model_id']} is not allowed for domain {domain}")
            result = service.calculate(args["model_id"], args["inputs"], args.get("scenario", "default"))
            result["quality_status"]["agent_evidence_ref"] = "derived:" + result["data"]["run_id"]
        elif operation == "calculate_entity":
            if args["model_id"] not in pack.get("required_models", []):
                raise ValidationError(f"model {args['model_id']} is not allowed for domain {domain}")
            result = service.calculate_for_entity(args["model_id"], args["node_id"], as_of=args.get("as_of"),
                                                  scenario=args.get("scenario", "default"))
            result["quality_status"]["agent_evidence_ref"] = "derived:" + result["data"]["run_id"]
        elif operation == "convert_currency":
            if "fx_conversion" not in pack.get("required_models", []):
                raise ValidationError(f"currency conversion is not allowed for domain {domain}")
            result = service.convert_currency(args["amount"], args["quote_node_id"], as_of=args["as_of"],
                                              rate_type=args.get("rate_type", "mid"))
            result["quality_status"]["agent_evidence_ref"] = "derived:" + result["data"]["run_id"]
        elif operation == "explain":
            result = service.explain(args["target"], args.get("item"))
        elif operation == "context":
            if args.get("domain", domain) != domain:
                raise ValidationError("context domain must match tool policy domain")
            result = service.context(args["id"], domain=domain, max_items=args.get("max_items", 25), as_of=args.get("as_of"))
        elif operation == "propose":
            result = service.propose(**{key: args[key] for key in ("actor", "target_source", "patch", "reason")},
                                     risk=args.get("risk", "normal"), title=args.get("title"), operations=args.get("operations", []))
        elif operation == "review":
            result = service.review(args.get("priority"), args.get("limit", 100))
        else:
            raise ValidationError(f"unsupported agent operation: {operation}")
        return result
