# KnowledgeOS contributor guide

- Keep the canonical source envelope domain-independent. Domain packs may change workflows, retrieval, context, skills, and tool policy only.
- Treat `knowledge/` and `control/` as Git-authored truth. `runtime/knowledge.index.db` is disposable; `runtime/knowledge.state.db` and `runtime/knowledge.ledger.db` are durable and must be backed up together, never committed. State includes ChangeSets, outbox, connector records, model runs and recorded snapshots.
- Every new predicate must be registered under `control/predicates/` before use.
- Do not write enterprise operational facts back into Markdown; ingest them through a connector and preserve source references.
- Agent-originated durable changes must be represented as ChangeSets. Tier A assertions require complete provenance.
- Use deterministic code for arithmetic, unit-sensitive values, schedules, and policy decisions.
- Add or update tests for compiler, ledger, policy, and API behavior with every semantic change.
- After each UI update, start or reuse a working local frontend and backend, verify access, and include clickable test URLs and any required local login instructions in the handoff. Keep those preview services running for the user's validation unless asked to stop them.
- External agents reach KnowledgeOS through `knowledgeos-mcp`, which calls only the public HTTP API with the agent's own credential and never imports `knowledge_os` or calls a model. Review independence follows the accountability chain (an agent and its owner are one responsible party); agents never approve or publish.
