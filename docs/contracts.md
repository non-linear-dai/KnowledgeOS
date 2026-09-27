# Authoring and runtime contracts

## Studio control-plane projection

`GET /v1/studio` is the UI-facing projection of the Git-authored control plane. It returns the eight definition kinds (`schema`, `domain`, `concept`, `relation`, `predicate`, `model`, `policy`, and `connector`) in one normalized catalog, the current coverage counts and extension-point counts, supported governance capabilities, and the current ChangeSet list. The endpoint contains no instance data.

The UI must treat this response as read-only source state. Durable edits continue to use `POST /v1/propose`, followed by `POST /v1/changesets/review` and `POST /v1/changesets/publish`; approval alone never mutates Git truth.

## Canonical node

Every authored node is one Markdown file with YAML frontmatter:

```yaml
---
base:
  schema:
    ckm: "3.0"
  node:
    id: org:example
    kind: entity
    type: organization
    key: EXAMPLE
    label: Example Organization
    aliases: []
  classification:
    tags: []
  lifecycle:
    state: active
  version:
    entity_revision: 1
knowledge:
  attrs: {}
  assertions: []
  relations: []
  logic_refs: []
external:
  assertions_ref:
---
```

Large assertion histories may move to `assertions.ndjson`; the `external.assertions_ref` path is resolved relative to the Markdown file.

## Knowledge API

The CLI and HTTP adapter expose the same domain-independent operations:

- `resolve`, `get`, `query`, `neighbors`, `history`, `search`
- `calculate`, `explain`, `context`
- `propose`, `review`

The control-plane endpoint `GET /v1/control` exposes the complete non-instance contract: canonical schemas, ontology shapes, predicates, policies, deterministic models, domain packs, retrieval profiles, connector mappings, and explicit constraint/rule/skill extension points.

Responses use a uniform envelope:

```json
{
  "data": {},
  "source_status": {},
  "temporal_status": {},
  "quality_status": {},
  "conflicts": [],
  "knowledge_gaps": [],
  "trace_refs": []
}
```

## Ontology shape contract

Concept definitions may declare `properties` with a registered predicate, required flag, concept-local cardinality, and display group. Relation definitions may declare allowed typed `connections`, a `simple`, `reifiable`, or `reified` mode, and a reification node template. The registry validates every reference before compilation; the compiler validates authored predicates, model references, and projected relation endpoint types.

## ChangeSet lifecycle

Durable agent-originated changes remain ChangeSets. The lifecycle is `proposed` or `review_required`, followed by `approved`, `rejected`, or `changes_requested`. Approval does not mutate truth. A ChangeSet reaches `published` only after the real source has been updated, compiled, and registered with a `source_revision`. The runtime stores the target source, structured patch/operations, review record, publisher, publication time, and source revision.

## Connector rule

A connector mapping must provide a stable source record ID, source timestamp/version or content hash, deterministic node identity, canonical predicate mappings, and authority class. Replaying the same record/version is idempotent.
