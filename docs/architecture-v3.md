# Enterprise Knowledge and AI Agent System — V3.0 implementation baseline

This repository implements the final architecture frozen in the “企业知识库架构” design conversation. The design is governed by five semantic dimensions:

| Dimension | Question | Runtime capability |
|---|---|---|
| C — Concept | What is it? | Node, type, ontology, stable attributes |
| R — Relation | How is it connected? | Simple edges and reified relation nodes |
| L — Logic | How is it judged or calculated? | Versioned deterministic models and constraints |
| T — Temporality | When is it valid? | Validity, history, supersession, ledger |
| P — Provenance | Why should it be trusted? | Source, evidence, authority, trace |

## Responsibility boundaries

1. `control/` and `knowledge/` are Git-authored truth for ontology, predicates, policies, models, decisions, research, context, and reviewed knowledge.
2. Enterprise applications remain authoritative for operational facts. Connectors map their records directly into the canonical runtime projection and ledger.
3. `runtime/knowledge.ledger.db` is append-only audit. `runtime/knowledge.state.db` contains durable governance, pending audit deliveries, connector records, model results and recorded-time snapshots. They are backed up together and do not replace Git or source systems.
4. `runtime/knowledge.index.db` is a disposable query projection. Rebuild replays Git sources and durable connector records. Projection/state commits use attached SQLite databases with rollback journals and FULL synchronization so a super-journal can commit them together; the ledger receives committed outbox events separately and idempotently.

## Frozen decisions

- There is one canonical kernel across all domains.
- Stable current values are attributes; independently temporal, evidenced, uncertain, disputed, or supersedable statements are assertions.
- A global predicate registry governs storage, value type, authority, freshness, history, embedding, write policy, and provenance tier.
- Simple relations remain lightweight; relations with their own attributes, evidence, validity, confidence, or identity become normal relation nodes.
- Domain packs contain behavior only and cannot introduce new source envelopes or business tables.
- Embedding is off by default and is allowed only by predicate policy.
- Agent durable writes are ChangeSets and must reach the real source of truth before compilation.
- Precise calculations are deterministic and retain model version, input hash, run ID, output, and trace.
- Physical units and currencies are versioned control definitions; empirical coefficients and exchange quotes are time- and source-bound assertions. Bound formula runs retain the exact input assertion references and a model snapshot.
- Human governance is exception-based; maintenance queues are budgeted and prioritized.
- API identities are authenticated from environment-managed bearer tokens and authorized by explicit roles; caller-supplied actor names are never trusted at the HTTP boundary.
- Projection mutations and ledger writes use a transactional outbox. Pending events are replayed idempotently after interruption.
- Full rebuild is transactional; control fingerprints invalidate dependent projections. A failed validation preserves the previous usable index. State migration moves legacy governance tables out of the index before normal operation.
- ChangeSet approval binds expected semantic source content. Publication requires that exact result, source revision verification, compilation, and a conditional state transition in one transaction.
- Local business keys are scoped by namespace and concept type; global node IDs remain canonical. Ontology definitions retain their actual authored file paths.
- The non-instance control plane is itself a validated contract. Schemas, ontology shapes, predicate policies, deterministic models, domain/retrieval behavior, and connector mappings must resolve through one registry snapshot.
- Concept-property bindings and relation domain/range/reification declarations are compiler-enforced contracts, not UI-only metadata.

## Delivery mapping

| Design phase | Repository implementation |
|---|---|
| Phase 0 — Kernel freeze | Registries, schemas, policies, directory contract |
| Phase 1 — Git + index | Markdown/NDJSON parser, compiler, SQLite, FTS, cards, API |
| Phase 2 — Ledger + governance | Hash chain, transactional outbox, verified ChangeSet state machine, review items, provenance and maintenance checks |
| Phase 3 — Connectors | Generic NDJSON adapter and mapping contract |
| Phase 4 — Logic + agents | Deterministic cost, schedule and risk models, historical C-R-L-T-P projection, three domain packs |
| Agent service facade | Capability discovery, governed invocation, grounded request packets, cited response validation, portable `SKILL.md` + `contract.yaml` packages |
| Phase 5 — lifecycle | Hot/Warm/Cold classification and rebuild/verification commands |
| Cross-cutting extraction | Normalized source envelopes, live registry-derived model contracts, evidence-bound create/update candidates, provider adapters |

The kernel supplies bearer-token authentication, role authorization, and provider-free deterministic vector retrieval. Enterprise identity federation, managed secret storage, production connectors, and optional learned embedding providers remain deployment integrations.
