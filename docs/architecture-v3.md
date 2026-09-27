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
3. `runtime/knowledge.ledger.db` is append-only audit, evidence, and recovery history. It does not replace Git or source systems.
4. `runtime/knowledge.index.db` is a disposable query projection. It can be deleted and rebuilt.

## Frozen decisions

- There is one canonical kernel across all domains.
- Stable current values are attributes; independently temporal, evidenced, uncertain, disputed, or supersedable statements are assertions.
- A global predicate registry governs storage, value type, authority, freshness, history, embedding, write policy, and provenance tier.
- Simple relations remain lightweight; relations with their own attributes, evidence, validity, confidence, or identity become normal relation nodes.
- Domain packs contain behavior only and cannot introduce new source envelopes or business tables.
- Embedding is off by default and is allowed only by predicate policy.
- Agent durable writes are ChangeSets and must reach the real source of truth before compilation.
- Precise calculations are deterministic and retain model version, input hash, run ID, output, and trace.
- Human governance is exception-based; maintenance queues are budgeted and prioritized.
- The non-instance control plane is itself a validated contract. Schemas, ontology shapes, predicate policies, deterministic models, domain/retrieval behavior, and connector mappings must resolve through one registry snapshot.
- Concept-property bindings and relation domain/range/reification declarations are compiler-enforced contracts, not UI-only metadata.

## Delivery mapping

| Design phase | Repository implementation |
|---|---|
| Phase 0 — Kernel freeze | Registries, schemas, policies, directory contract |
| Phase 1 — Git + index | Markdown/NDJSON parser, compiler, SQLite, FTS, cards, API |
| Phase 2 — Ledger + governance | Hash chain, ChangeSets, review items, provenance and maintenance checks |
| Phase 3 — Connectors | Generic NDJSON adapter and mapping contract |
| Phase 4 — Logic + agents | Deterministic model engine, C-R-L-T-P plan, three domain packs |
| Phase 5 — lifecycle | Hot/Warm/Cold classification and rebuild/verification commands |

The implementation intentionally keeps external vendor choices outside the kernel. Production connectors, authentication, authorization enforcement, secret management, and embedding providers must be selected for the target enterprise environment.
