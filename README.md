# KnowledgeOS

KnowledgeOS is an executable foundation for the V3.0 enterprise knowledge and AI-agent architecture. It keeps one domain-independent knowledge language while separating authored truth, enterprise operational truth, immutable audit history, and rebuildable runtime indexes.

The core contract is:

```text
Node + attrs + assertions + relations + logic_refs
```

Cost analysis, industry research, project management, and future domains all use that contract. Domain packs only define how agents retrieve, reason, calculate, assemble context, and call tools.

## What is implemented

- Canonical Markdown/YAML node envelope and external NDJSON assertions
- Global predicate, ontology, authority, freshness, provenance, and maintenance policies
- Deterministic validation and incremental compilation
- Rebuildable SQLite node/assertion/edge/FTS/entity-card projection
- Append-only, hash-chained immutable ledger with tamper verification
- Hot/Warm/Cold assertion classification
- Source references, Tier A/B/C provenance checks, review queue, and maintenance priority
- Generic NDJSON enterprise connector with canonical mapping (no Markdown copy)
- Small domain-independent Knowledge API and HTTP server
- C-R-L-T-P context-plan output
- Safe deterministic formula engine with result traces
- Thin Cost, Industry, and Project Management domain packs
- Complete non-instance control-plane catalog for schemas, ontology, predicates, policies, models, domains, retrieval profiles, connector mappings, and explicit extension points
- Executable concept-shape, relation endpoint/reification, model-reference, and control-reference validation
- Example entities and automated tests

Vector search and real ERP/QMS/PLM/PM adapters are extension points: no fake external integrations or embedding provider is bundled.

## Quick start

Ruby 2.6+ with `sqlite3` and `webrick` is required.

```bash
bundle install
bin/knowledgeos doctor
bin/knowledgeos rebuild
bin/knowledgeos search "KnowledgeOS"
bin/knowledgeos get org:acme
bin/knowledgeos context org:acme --domain industry
bin/knowledgeos control
bin/knowledgeos verify-ledger
rake test
```

Start the local API:

```bash
bin/knowledgeos serve --bind 127.0.0.1 --port 8787
curl 'http://127.0.0.1:8787/v1/get?id=org:acme'
```

Runtime databases are created in `runtime/` and intentionally ignored by Git. Rebuild them at any time:

```bash
bin/knowledgeos rebuild
```

## Repository map

```text
control/      Knowledge code: ontology, predicates, policies, models, domain packs
knowledge/    Git-authored knowledge truth
connectors/   Enterprise source mapping examples
lib/          Compiler, index, ledger, services, API, connector and engine
runtime/      Disposable generated state
tests/        Architecture and behavior tests
docs/         Architecture decisions and implementation mapping
prototype/    KnowledgeOS structure and governance control panel
```

See [docs/architecture-v3.md](docs/architecture-v3.md) for the frozen design-to-code mapping and [docs/contracts.md](docs/contracts.md) for authoring and API contracts.
