# KnowledgeOS

KnowledgeOS is an executable foundation for the V3.0 enterprise knowledge and AI-agent architecture. It keeps one domain-independent knowledge language while separating authored truth, enterprise operational truth, immutable audit history, and rebuildable runtime indexes.

The core contract is:

```text
Node + attrs + assertions + relations + logic_refs
```

Cost analysis, industry research, project management, and future domains all use that contract. Domain packs only define how agents retrieve, reason, calculate, assemble context, and call tools.

## What is implemented

- Canonical Markdown/YAML node envelope and external NDJSON assertions
- Executable predicate, authority, freshness, provenance, maintenance-budget, retention, and externalization policies
- Deterministic validation and incremental compilation
- Rebuildable SQLite node/assertion/edge/FTS/vector/entity-card projection
- Append-only, hash-chained immutable ledger with transactional outbox recovery and tamper verification
- Hot/Warm/Cold assertion classification
- Source references, Tier A/B/C provenance checks, review queue, and maintenance priority
- Generic NDJSON enterprise connector with canonical mapping (no Markdown copy)
- Provider-neutral extraction workflow for text, files, captured web pages, audio transcripts, and meeting minutes
- Ontology-derived JSON extraction contracts with create/update candidates, evidence checks, and no implicit writes
- Authenticated, role-authorized domain-independent Knowledge API and HTTP server
- Agent/LLM facade for capability discovery, grounded task packets, governed tool calls, and cited output validation
- C-R-L-T-P context-plan output
- Safe deterministic arithmetic, schedule-variance, and project-risk models with result traces
- Thin Cost, Industry, and Project Management domain packs
- Portable two-layer skill packages (`SKILL.md` for Agents, `contract.yaml` for service policy) for cost, industry, and project work
- Complete non-instance control-plane catalog for schemas, ontology, predicates, policies, models, domains, retrieval profiles, connector mappings, and explicit extension points
- Executable JSON Schema, concept/cardinality, relation endpoint/reification, deterministic model, constraint, rule, and control-reference validation
- Example entities and automated tests

KnowledgeOS includes a deterministic local hash-vector index for provider-free hybrid retrieval. Higher-quality embedding providers and real ERP/QMS/PLM/PM adapters remain extension points; no fake external integration is bundled.

## Quick start

Ruby 2.6+ with `sqlite3` and `webrick` is required.

```bash
bundle install
bin/knowledgeos doctor
bin/knowledgeos rebuild
bin/knowledgeos search "KnowledgeOS"
bin/knowledgeos get org:acme
bin/knowledgeos context org:acme --domain industry
bin/knowledgeos agent-capabilities --domain industry
bin/knowledgeos agent-skill industry-evidence-brief
bin/knowledgeos agent-request "What is Acme's market position?" --domain industry --target org:acme
bin/knowledgeos control
bin/knowledgeos extract-contract --source-type file --source notes.md
bin/knowledgeos verify-ledger
rake test
```

Start the local API:

```bash
export KNOWLEDGEOS_AUTH_MODE=required
export KNOWLEDGEOS_AUTH_TOKENS='{"replace-with-a-long-random-token":{"principal":"local:admin","roles":["admin"]}}'
bin/knowledgeos serve --bind 127.0.0.1 --port 8787
curl -H 'Authorization: Bearer replace-with-a-long-random-token' \
  'http://127.0.0.1:8787/v1/get?id=org:acme'
```

Supported roles are `reader`, `agent`, `reviewer`, `publisher`, and `admin`. Tokens live only in the process environment. `KNOWLEDGEOS_AUTH_MODE=disabled` is available solely for explicit local test harnesses.

Runtime databases are created in `runtime/` and intentionally ignored by Git. The query index is disposable; both `knowledge.state.db` and `knowledge.ledger.db` must be backed up together. Rebuild replays durable connector records and preserves governance state:

```bash
bin/knowledgeos rebuild
```

Studio users sign in using their own backend bearer token. The proxy forwards that identity; there is no shared privileged Studio token. Tokens are held in page memory only. Demo mode requires an explicit user action and is separate from connection failure.

For an offline, checksummed backup, stop API/CLI writers and run `bin/knowledgeos backup /absolute/new-backup-directory`. Restore the matching Git-authored checkout into a separate workspace with an empty runtime, then run `bin/knowledgeos restore /absolute/backup-directory`, `bin/knowledgeos rebuild`, and `bin/knowledgeos verify-ledger`. Restore refuses to overwrite existing databases.

See [docs/iteration-3.5.md](docs/iteration-3.5.md) for the nine accepted review items, compatibility changes, recovery, and validation boundaries. Deployment-platform adapters and the CI matrix are unchanged in this iteration.

## Repository map

```text
control/      Knowledge code: ontology, predicates, policies, models, domain packs
knowledge/    Git-authored knowledge truth
connectors/   Enterprise source mapping examples
lib/          Compiler, index, ledger, services, API, connector and engine
runtime/      Rebuildable query state plus durable local audit ledger
tests/        Architecture and behavior tests
docs/         Architecture decisions and implementation mapping
prototype/    KnowledgeOS structure and governance control panel
```

See [docs/architecture-v3.md](docs/architecture-v3.md) for the frozen design-to-code mapping and [docs/contracts.md](docs/contracts.md) for authoring and API contracts.
See [docs/extraction.md](docs/extraction.md) for the multi-source, model-portable candidate extraction protocol.
See [docs/agent-service.md](docs/agent-service.md) for the Agent/LLM request, tool, grounding, and response protocol.
