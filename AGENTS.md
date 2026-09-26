# KnowledgeOS contributor guide

- Keep the canonical source envelope domain-independent. Domain packs may change workflows, retrieval, context, skills, and tool policy only.
- Treat `knowledge/` and `control/` as Git-authored truth. Treat `runtime/*.db` as disposable projections and never commit them.
- Every new predicate must be registered under `control/predicates/` before use.
- Do not write enterprise operational facts back into Markdown; ingest them through a connector and preserve source references.
- Agent-originated durable changes must be represented as ChangeSets. Tier A assertions require complete provenance.
- Use deterministic code for arithmetic, unit-sensitive values, schedules, and policy decisions.
- Add or update tests for compiler, ledger, policy, and API behavior with every semantic change.

