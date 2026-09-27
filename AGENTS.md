# KnowledgeOS contributor guide

- Keep the canonical source envelope domain-independent. Domain packs may change workflows, retrieval, context, skills, and tool policy only.
- Treat `knowledge/` and `control/` as Git-authored truth. `runtime/knowledge.index.db` is disposable; `runtime/knowledge.state.db` and `runtime/knowledge.ledger.db` are durable and must be backed up together, never committed. State includes ChangeSets, outbox, connector records, model runs and recorded snapshots.
- Every new predicate must be registered under `control/predicates/` before use.
- Do not write enterprise operational facts back into Markdown; ingest them through a connector and preserve source references.
- Agent-originated durable changes must be represented as ChangeSets. Tier A assertions require complete provenance.
- Use deterministic code for arithmetic, unit-sensitive values, schedules, and policy decisions.
- Add or update tests for compiler, ledger, policy, and API behavior with every semantic change.
