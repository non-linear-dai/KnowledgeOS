# Authoring and runtime contracts

## Studio control-plane projection

`GET /v1/studio` is the UI-facing projection of the Git-authored control plane. It returns the eight definition kinds (`schema`, `domain`, `concept`, `relation`, `predicate`, `model`, `policy`, and `connector`) in one normalized catalog, the current coverage counts and extension-point counts, supported governance capabilities, and the current ChangeSet list. The endpoint contains no instance data.

The UI must treat this response as read-only source state. Durable edits continue to use `POST /v1/propose`, followed by `POST /v1/changesets/review` and `POST /v1/changesets/publish`; approval alone never mutates Git truth.

## Authentication and authorization

All `/v1/*` routes require `Authorization: Bearer <token>`. `KNOWLEDGEOS_AUTH_TOKENS` is an environment-only JSON object mapping tokens to principals and roles. `reader` can query, `agent` can prepare/invoke/extract/propose, `reviewer` can govern, `publisher` can publish, and `admin` has all permissions. HTTP mutation actors are always derived from the authenticated principal.

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
- `agent_capabilities`, `agent_skill`, `agent_request`, `agent_invoke`, `agent_respond`
- `extraction_request`, `extraction_candidates`
- `propose`, `review`

The control-plane endpoint `GET /v1/control` exposes the complete non-instance contract: canonical schemas, ontology shapes, predicates, policies, deterministic models, domain packs, retrieval profiles, connector mappings, extraction profiles, and explicit constraint/rule/skill extension points.

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

Concept definitions may declare `properties` with a registered predicate, required flag, concept-local cardinality, and display group. Relation definitions may declare allowed typed `connections`, a `simple`, `reifiable`, or `reified` mode, and a reification node template. JSON Schema and Git-authored constraints run before semantic validation. The compiler enforces endpoint existence/types, endpoint cardinality, reified identity, required reification properties, predicate enum/cardinality, and deterministic-model references.

## ChangeSet lifecycle

Durable agent-originated changes remain ChangeSets. The lifecycle is `proposed` or `review_required`, followed by `approved`, `rejected`, or `changes_requested`; invalid transitions are rejected. Approval does not mutate truth. A ChangeSet reaches `published` only after the source differs from its proposal baseline, its current SHA-256 or Git revision matches `source_revision`, and registry validation plus compilation succeed. Upstream publications must match a connector source version/hash. The runtime stores the verification receipt with the review and publication record.

Every projection mutation stages its audit event in `audit_outbox` within the same SQLite transaction. Delivery to the append-only ledger is idempotent; service startup replays pending events and reconciles ledger references.

## Temporal and retrieval policy

Freshness windows classify confirmed assertions as Hot, Warm, or Cold at compile and service startup. `context?as_of=` reconstructs assertions from observed/valid time and supersession rather than returning the current card. Domain retrieval profiles control the reported plan and cold-data policy. Search supports `keyword`, `vector`, `hybrid`, `hybrid_research`, `structured_first`, and `temporal_graph_first`; vector retrieval uses the deterministic local `knowledgeos-hash-embedding-v1` index.

## Connector rule

A connector mapping must provide a stable source record ID, source timestamp/version or content hash, deterministic node identity, canonical predicate mappings, and authority class. Replaying the same record/version is idempotent.

## Candidate extraction protocol

Extraction is read-only and uses two provider-neutral phases:

1. `POST /v1/extraction/request` normalizes source content and returns the current ontology, existing entity hints, a dynamic JSON output schema, and a registry fingerprint.
2. An LLM or deterministic extractor returns JSON matching that contract. `POST /v1/extraction/candidates` checks the protocol and registry fingerprint, validates evidence against source segments, resolves existing identities, validates the merged canonical node, and returns create or incremental-update candidates.

The finalization call fails when control definitions changed after the request was created. The caller must regenerate the request and rerun extraction. Candidate generation never creates a ChangeSet, changes Git truth, or updates a runtime projection.

## Agent and model service protocol

The provider-neutral Agent service has four operations:

1. `GET /v1/agent/capabilities` discovers domain packs, skills, allowlisted tools, and the response schema.
2. `GET /v1/agent/skill?id=...` returns the exact `SKILL.md` and `contract.yaml` files of one portable skill package.
3. `POST /v1/agent/request` resolves a target, assembles a C-R-L-T-P packet, assigns stable evidence references, and returns the selected domain skill plus a registry-bound output contract.
4. `POST /v1/agent/invoke` dispatches only operations permitted by the selected domain. Registered deterministic models remain mandatory for arithmetic and unit-sensitive results. `propose`, where allowed, creates only a ChangeSet.
5. `POST /v1/agent/respond` checks protocol/request identity, rejects unknown evidence references, verifies cited deterministic tool runs against stored traces, validates claim confidence, and emits the standard KnowledgeOS envelope.

The service never stores chain-of-thought or treats model prose as truth. Finalized answers are read-only outputs with explicit claims, citations, gaps, and recommended actions. Durable changes still use the separate ChangeSet lifecycle.
