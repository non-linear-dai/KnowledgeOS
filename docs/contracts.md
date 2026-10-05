# Authoring and runtime contracts

## Studio control-plane projection

`GET /v1/studio` is the UI-facing projection of the Git-authored control plane. It returns ten definition kinds (`schema`, `domain`, `concept`, `relation`, `predicate`, `model`, `unit`, `currency`, `policy`, and `connector`) in one normalized catalog, the current coverage counts and extension-point counts, supported governance capabilities, and the current ChangeSet list. The endpoint contains no instance data.

The UI must treat this response as read-only source state. Durable edits use `POST /v1/propose`, followed by `POST /v1/changesets/review`. Approved single-file model, unit, and currency changes may use publisher-only `POST /v1/changesets/apply` to write the verified definition to the Git working tree, then `POST /v1/changesets/publish` to compile and register its source revision. Approval alone never mutates Git truth.

## Authentication and authorization

All `/v1/*` routes require `Authorization: Bearer <token>`. `KNOWLEDGEOS_AUTH_TOKENS` is an environment-only JSON object mapping tokens to principals and roles. `reader` can query, `agent` can prepare/invoke/extract/propose, `reviewer` can govern ordinary ChangeSets, `publisher` can publish ordinary ChangeSets, and `admin` has all permissions. `template_author`, `template_reviewer`, and `template_publisher` separately govern expert template ChangeSets. HTTP mutation actors are always derived from the authenticated principal.

`GET /v1/session` returns the current principal, roles, and permissions. Studio forwards the user's bearer token, never a server-wide admin token. The proxy rejects cross-origin writes, bounds request bodies, and times out upstream requests. Credentials remain in browser memory only. Service tokens and user tokens are not substituted for one another.

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
    key_namespace: example-system
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

## Expert route template workbench

The /templates workbench authors instances separately from the non-instance Studio whiteboard. It composes the registered route_template, route_group, route_step, and operation_template concepts without changing the canonical envelope. The template API lists and reads published versions, previews bounded deterministic expansion over named scenario collections, and proposes an immutable multi-file Git-authored package. The compiler checks route membership, acyclic precedence, version-pinned operation references and expert sample expansion counts. Proposed packages require an independent high-risk review, publisher-only source application, and verified compilation and publication. Template author, reviewer, and publisher permissions are separate from ontology control-plane roles. See [expert-template-workbench.md](expert-template-workbench.md).

## Business constraint and rule contract

Business-authored definitions use the separate `knowledgeos.business-constraint.v1` and `knowledgeos.business-rule.v1` formats in `control/constraints/business/` and `control/rules/business/`. A definition has a stable ID, label, description, semantic version, lifecycle, registered concept scope, 1–20 named predicate inputs, and 1–20 declarative comparisons. Rules bind `subject` and `candidate` roles; constraints bind only `subject`. An optional registered relation type must allow the declared source and target concepts. Comparisons support equality, inequality, and ordered comparisons; quantity values are normalized through registered units with decimal arithmetic. Missing facts produce `unknown` rather than a positive match. No user-authored code is executed.

`POST /v1/business/preview` validates a proposed definition and evaluates supplied sample facts without a durable write. `POST /v1/business/impact` applies a draft definition read-only to current entities and returns verdict counts and fact source references before approval. `POST /v1/business/evaluate` evaluates an active published definition against confirmed entity facts as of a requested time, returning verdicts, eligible candidate IDs, fact source references, and optional derived relation edges. Business rule verdicts are `eligible` (all checks pass), `not_matched` (a subject-only scope check fails), `condition_failed` (subject scope passes but a candidate-dependent check fails), and `unknown` (required facts are missing). Subject scope is evaluated before candidate conditions; `not_matched` does not mean the candidate failed the rule. Omitted candidate IDs select up to 500 active entities of the candidate concept. Derived edges are query results, not persisted assertions. Active business constraints run during canonical knowledge compilation and reject invalid or missing required facts. Connector ingestion checks active constraints against the facts supplied in each record and rejects invalid records transactionally; records lacking all declared inputs remain pending rather than being falsely accepted as valid. Publishing an active constraint also checks previously ingested operational entities. Technical compiler constraints and maintenance rules keep their existing formats and remain outside the whiteboard.

Business definition changes require a higher semantic version and a high-risk ChangeSet with an independent reviewer. Approved standalone YAML can be applied to Git-authored truth, compiled, and published with a verified source revision. Published definitions are deprecated instead of deleted.

## ChangeSet lifecycle

Durable agent-originated changes remain ChangeSets. The lifecycle is `proposed` or `review_required`, followed by `approved`, `rejected`, or `changes_requested`; invalid transitions are rejected. Approval does not mutate truth. A ChangeSet reaches `published` only after the source differs from its proposal baseline, its current SHA-256 or Git revision matches `source_revision`, and registry validation plus compilation succeed. Upstream publications must match a connector source version/hash. The runtime stores the verification receipt with the review and publication record.

Every projection mutation stages its audit event in `audit_outbox` within the same SQLite transaction. Delivery to the append-only ledger is idempotent; service startup replays pending events and reconciles ledger references.

Contract 3.5 adds `base_revision` and `idempotency_key` to proposals. Studio submits its registry fingerprint as the baseline. Supported publishable patches are JSON-pointer add/replace/remove, complete-file deletion, and complete Studio concept/relation/predicate operations. A proposal without a verifiable result may be rejected, but cannot be approved. Upstream proposals require `expected_source_hash`. Approval captures the complete expected semantic contents of target files; unrelated semantic changes invalidate publication. Identical publication retries are idempotent; competing transitions use `lock_version`, return conflict, and cannot overwrite one another. Pre-3.5 approvals without an expectation must be replaced and reapproved.

ChangeSets, the outbox, model results and maintenance workflow state live in `knowledge.state.db`; they are not removed when the index is rebuilt. The state schema records migrations and upgrades legacy index-resident governance tables automatically.

## Temporal and retrieval policy

Freshness windows classify confirmed assertions as Hot, Warm, or Cold at compile and service startup. `context?as_of=` reconstructs assertions from observed/valid time and supersession rather than returning the current card. Domain retrieval profiles control the reported plan and cold-data policy. Search supports `keyword`, `vector`, `hybrid`, `hybrid_research`, `structured_first`, and `temporal_graph_first`; vector retrieval uses the deterministic local `knowledgeos-hash-embedding-v1` index.

Timestamps require an explicit UTC offset and normalize to UTC. Valid intervals are half-open `[valid_from, valid_to)`, including relations. Current reads refresh freshness during long-running service operation. `recorded_as_of` selects durable snapshots representing what the system had recorded at that time; it may be combined with `as_of` for business validity. Historical attributes without a recorded snapshot are returned as unknown with a knowledge gap, never filled from today's attributes. Historical context intentionally does not invent data predating the first captured snapshot.

Quantities require an explicit unit or a registered predicate default. Decimal/currency values use exact decimal normalization; node references are validated after all nodes are projected. Models accept legacy scalar inputs in declared model units, or typed `{literal, unit}` inputs. Supported unit conversions are explicit. Mixed currencies are rejected; a dedicated exchange-rate model is required. Date and datetime are distinct value types.

Physical units are registered in `control/units/` with a dimension and exact factor to that dimension's base unit. Registered currencies and their minor-unit precision live in `control/currencies/`. Bound declarative models validate formula operations and dimensions, and `POST /v1/models/preview` evaluates a proposed model without persistence. `POST /v1/calculate/entity` selects confirmed, time-valid assertion inputs declared by the model and retains their evidence references. `POST /v1/fx/convert` requires a confirmed connector exchange quote with explicit base/quote currencies, rate type, business time, and provenance. Currency conversion never occurs implicitly. See [units-formulas-fx.md](units-formulas-fx.md).

The local JSON Schema validator implements an explicit subset, not every Draft 2020-12 feature. It supports local JSON pointers, types, const/enum, required/properties/additionalProperties, array/string/numeric bounds, uniqueness, patterns, date/date-time/URI formats, multipleOf, allOf/anyOf/oneOf/not. Unsupported keywords and formats fail configuration validation instead of being ignored. The shared Studio snapshot schema is under `control/schemas/studio-snapshot.schema.json`; frontend runtime validation and inferred wire types live in `app/studio-contract.ts`.

## Connector rule

A connector mapping must provide a stable source record ID, source timestamp/version or content hash, deterministic node identity, canonical predicate mappings, and authority class. Replaying the same record/version is idempotent.

Connectors validate normalized canonical documents with the same validator as authored files, then use the compiler's card/FTS/vector projection. Predicate embedding policy applies to both. Accepted records and their mappings are durable, support rebuild replay, and never become Markdown copies. Older observed versions cannot overwrite newer ones; same-time conflicting payloads require a higher numeric source version. `deleted_field` names an explicit boolean tombstone field. `ingest --isolate-errors` collects invalid records while accepting independent valid records. Connector business keys default to a source-system namespace; authored nodes may explicitly set `base.node.key_namespace`.

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
