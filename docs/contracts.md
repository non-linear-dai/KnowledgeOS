# Authoring and runtime contracts

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

## Connector rule

A connector mapping must provide a stable source record ID, source timestamp/version or content hash, deterministic node identity, canonical predicate mappings, and authority class. Replaying the same record/version is idempotent.

