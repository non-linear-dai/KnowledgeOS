"""Read-only directory across canonical nodes, facts, edges and recorded model runs."""
from __future__ import annotations

import json
import re

from .compiler import decode_assertion
from .core import NotFoundError, ValidationError, json_text, utcnow
from .domain_scope import domain_scope


KINDS = ("concept", "relation", "predicate", "model")

# Keys identify projection records without adding IDs to the canonical envelope.
# Composite keys use JSON encoded as hex, preserving separators and temporal edges.
PROJECTION = """
WITH registry AS (
  SELECT json_extract(value,'$.kind') AS kind,json_extract(value,'$.id') AS type,
         json_extract(value,'$.label') AS type_label FROM json_each(?)
), hidden AS (SELECT value AS id FROM json_each(?)), clock AS (SELECT ? AS now),
visible_node AS (SELECT n.* FROM node n WHERE n.id NOT IN (SELECT id FROM hidden)),
raw AS (
  SELECT 'concept:'||n.id AS key,n.id,'concept' AS kind,'node' AS record_kind,n.type,
    n.label,n.lifecycle,n.source_class,n.source_path,n.compiled_at,
    n.id AS subject_id,NULL AS target_id,
    COALESCE(n.natural_key,'')||' '||n.aliases_json||' '||n.tags_json||' '||n.narrative AS content,
    NULL AS value_json FROM visible_node n
  UNION ALL
  SELECT 'relation:'||hex(json_array(e.src,e.predicate,e.dst,e.rel_id,e.valid_from)),
    CASE WHEN e.rel_id<>'' THEN e.rel_id ELSE e.src||' → '||e.dst END,
    'relation','edge',e.predicate,s.label||' → '||COALESCE(t.label,e.dst),
    CASE WHEN e.valid_from<>'' AND julianday(e.valid_from)>julianday(clock.now) THEN 'scheduled'
         WHEN e.valid_to<>'' AND julianday(e.valid_to)<=julianday(clock.now) THEN 'expired' ELSE 'active' END,
    CASE WHEN e.source_path LIKE 'connector:%' THEN 'enterprise_system' ELSE s.source_class END,
    e.source_path,s.compiled_at,e.src,e.dst,e.src||' '||e.dst||' '||e.rel_id,NULL
    FROM edge e JOIN visible_node s ON s.id=e.src LEFT JOIN visible_node t ON t.id=e.dst CROSS JOIN clock
    WHERE e.dst NOT IN (SELECT id FROM hidden) AND e.rel_id NOT IN (SELECT id FROM hidden)
  UNION ALL
  SELECT 'assertion:'||a.id,a.id,'predicate','assertion',a.predicate,n.label,a.status,
    CASE WHEN a.source_path LIKE 'connector:%' THEN 'enterprise_system' ELSE n.source_class END,
    a.source_path,COALESCE(a.observed_at,n.compiled_at),a.node_id,NULL,
    a.node_id||' '||a.value_json||' '||a.qualifiers_json||' '||a.source_refs_json||' '||a.evidence_refs_json,a.value_json
    FROM assertion a JOIN visible_node n ON n.id=a.node_id
  UNION ALL
  SELECT 'attribute:'||hex(json_array(n.id,j.key)),n.id||'#'||j.key,'predicate','attribute',j.key,
    n.label,n.lifecycle,n.source_class,n.source_path,n.compiled_at,n.id,NULL,
    n.id||' '||COALESCE(CAST(j.value AS TEXT),''),
    CASE j.type WHEN 'true' THEN 'true' WHEN 'false' THEN 'false' WHEN 'null' THEN 'null'
      WHEN 'object' THEN j.value WHEN 'array' THEN j.value ELSE json_quote(j.value) END
    FROM visible_node n,json_each(n.attrs_json) j
  UNION ALL
  SELECT 'model:'||r.id,r.run_id,'model','model_run',r.model_id,r.scenario,r.status,
    'model_derived',NULL,r.calculated_at,NULL,NULL,
    r.id||' '||r.model_version||' '||r.input_refs_json||' '||r.output_json||' '||r.trace_json,r.output_json
    FROM durable.derived_result r
), instances AS (
  SELECT raw.*,COALESCE(registry.type_label,raw.type) AS type_label,
    CASE raw.kind WHEN 'predicate' THEN COALESCE(registry.type_label,raw.type)||' · '||raw.label
      WHEN 'model' THEN COALESCE(registry.type_label,raw.type)||' · '||raw.label ELSE raw.label END AS title
    FROM raw LEFT JOIN registry ON registry.kind=raw.kind AND registry.type=raw.type
)
"""


class InstanceDirectory:
    def __init__(self, service):
        self.service, self.db = service, service.db

    def _projection(self):
        registry = self.service.registry
        definitions = [{"kind": kind, "id": item["id"], "label": item.get("label", item["id"])}
                       for kind, items in (("concept", registry.ontology.get("concept_types", [])),
                                           ("relation", registry.ontology.get("relation_types", [])),
                                           ("predicate", registry.predicates.values()), ("model", registry.models.values()))
                       for item in items]
        templates, operations = self.service._unpublished_template_assets()
        hidden = templates | operations | self.service._unpublished_decision_assets()
        # Children of a package awaiting publication must stay hidden with its root.
        for identifier in templates:
            _, key, version = identifier.split(":", 2)
            prefix = f"knowledge/templates/{key}/v{version}/"
            hidden.update(row["id"] for row in self.db.execute(
                "SELECT id FROM node WHERE substr(source_path,1,?)=?", (len(prefix), prefix)))
        return [json_text(definitions), json_text(sorted(hidden)), utcnow()]

    @staticmethod
    def _number(value, name, minimum, maximum):
        if not re.fullmatch(r"0|[1-9][0-9]{0,18}", str(value)):
            raise ValidationError(f"{name} must be an integer")
        number = int(value)
        if not minimum <= number <= maximum:
            raise ValidationError(f"{name} is out of range")
        return number

    @staticmethod
    def _summary(row):
        return {**{key: value for key, value in row.items() if key not in ("content", "title", "value_json")},
                "label": row["title"], "value": json.loads(row["value_json"]) if row["value_json"] is not None else None}

    def list(self, query="", *, kind=None, type=None, lifecycle=None, source_class=None, domain=None,
             limit=25, offset=0, sort="label"):
        limit = self._number(limit, "limit", 1, 200)
        offset = self._number(offset, "offset", 0, 9_223_372_036_854_775_807)
        orders = {"label": "title COLLATE NOCASE,key", "updated": "julianday(compiled_at) DESC,key"}
        if sort not in orders:
            raise ValidationError("unsupported instance sort")
        if kind and kind not in KINDS:
            raise ValidationError("unsupported instance kind")
        projection = self._projection()
        source, sql = "instances", PROJECTION
        if domain:
            scope = domain_scope(self.service.registry, domain)
            # Bound parameters apply before search, totals, facets and pagination.
            # Subject/endpoint types keep shared predicates and relations from
            # leaking records belonging only to another domain into this view.
            sql += """, domain_nodes AS (
              SELECT id FROM visible_node WHERE type IN (SELECT value FROM json_each(?))
            ), domain_instances AS (
              SELECT * FROM instances WHERE
                (kind='concept' AND id IN (SELECT id FROM domain_nodes)) OR
                (kind='predicate' AND type IN (SELECT value FROM json_each(?))
                  AND subject_id IN (SELECT id FROM domain_nodes)) OR
                (kind='relation' AND type IN (SELECT value FROM json_each(?))
                  AND subject_id IN (SELECT id FROM domain_nodes) AND target_id IN (SELECT id FROM domain_nodes)) OR
                (kind='model' AND type IN (SELECT value FROM json_each(?)))
            )
            """
            projection.extend(json_text(scope[key]) for key in ("concept", "predicate", "relation", "model"))
            source = "domain_instances"
        clauses, binds = [], []
        term = str(query).strip()
        if term:
            pattern = "%" + term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
            fields = ("id", "type", "type_label", "title", "content")
            clauses.append("(" + " OR ".join(f"{field} LIKE ? ESCAPE '\\'" for field in fields) + ")")
            binds.extend([pattern] * len(fields))
        for field, value in (("kind", kind), ("type", type), ("lifecycle", lifecycle), ("source_class", source_class)):
            if value:
                clauses.append(f"{field}=?")
                binds.append(value)
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        total = self.db.first(sql + f"SELECT count(*) AS count FROM {source}" + where, [*projection, *binds])["count"]
        rows = self.db.execute(sql + f"SELECT * FROM {source}" + where + " ORDER BY " + orders[sort] + " LIMIT ? OFFSET ?",
                               [*projection, *binds, limit, offset])
        counts = {row["kind"]: row["count"] for row in self.db.execute(
            sql + f"SELECT kind,count(*) AS count FROM {source} GROUP BY kind", projection)}
        facets = {"kind": [{"value": value, "count": counts.get(value, 0)} for value in KINDS]}
        scope, scope_binds = (" WHERE kind=?", [kind]) if kind else ("", [])
        for field in ("type", "lifecycle", "source_class"):
            grouping = "type,kind,type_label" if field == "type" else field
            extra = ",kind,type_label AS label" if field == "type" else ""
            facets[field] = self.db.execute(sql + f"SELECT {field} AS value{extra},count(*) AS count FROM {source}" +
                                           scope + f" GROUP BY {grouping} ORDER BY {field}", [*projection, *scope_binds])
        return {"items": [self._summary(row) for row in rows], "total": total, "limit": limit, "offset": offset,
                "overall_total": sum(counts.values()), "facets": facets, "domain": domain or None,
                "domains": [{"id": item["id"], "label": item.get("label", item["id"])}
                            for _, item in sorted(self.service.registry.domains.items())]}

    def get(self, key):
        row = self.db.first(PROJECTION + "SELECT * FROM instances WHERE key=?", [*self._projection(), key])
        if not row:
            raise NotFoundError(f"instance not found: {key}")
        summary = self._summary(row)
        sources, evidence, references = [], [], []

        def node_ref(identifier):
            if not identifier:
                return
            node = self.db.first("SELECT id,label,type FROM node WHERE id=?", (identifier,))
            if node:
                references.append({"key": "concept:" + identifier, **node})

        if row["record_kind"] == "node":
            data = self.service.get(row["id"])["data"]
        elif row["record_kind"] == "assertion":
            assertion = self.db.first("SELECT * FROM assertion WHERE id=?", (row["id"],))
            data = decode_assertion(assertion)
            sources, evidence = data["provenance"]["source_refs"], data["provenance"]["evidence_refs"]
            node_ref(row["subject_id"])
        elif row["record_kind"] == "attribute":
            data = {"node_id": row["subject_id"], "predicate": row["type"], "value": summary["value"]}
            for candidate in self.db.execute("SELECT c.value_json,s.* FROM attribute_candidate c JOIN source_ref s ON s.id=c.source_ref_id WHERE c.node_id=? AND c.predicate=?",
                                             (row["subject_id"], row["type"])):
                if json.loads(candidate.pop("value_json")) == data["value"]:
                    sources.append(candidate)
            node_ref(row["subject_id"])
        elif row["record_kind"] == "edge":
            # The opaque identity includes validity and the optional reification ID.
            identity = json.loads(bytes.fromhex(key.split(":", 1)[1]).decode())
            data = self.db.first("SELECT * FROM edge WHERE src=? AND predicate=? AND dst=? AND rel_id=? AND valid_from=?", identity)
            node_ref(data["src"])
            node_ref(data["dst"])
            if data["rel_id"]:
                node_ref(data["rel_id"])
        else:
            result = self.db.first("SELECT * FROM durable.derived_result WHERE id=?", (key.split(":", 1)[1],))
            data = {name.removesuffix("_json"): json.loads(value) if name.endswith("_json") and value else value
                    for name, value in result.items()}
            for ref in data.get("input_refs", {}).values():
                if not isinstance(ref, dict):
                    continue
                sources.extend(ref.get("source_refs", []))
                evidence.extend(ref.get("evidence_refs", []))
                assertion = self.db.first("SELECT node_id FROM assertion WHERE id=?", (ref.get("assertion_id", ""),))
                if assertion:
                    node_ref(assertion["node_id"])
        return {"summary": summary, "data": data, "source_refs": sources, "evidence_refs": evidence,
                "references": list({ref["key"]: ref for ref in references}.values())}
