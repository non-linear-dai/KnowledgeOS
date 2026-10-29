# KnowledgeOS

KnowledgeOS is an executable foundation for the V4.0 enterprise knowledge and AI-agent architecture. It keeps one domain-independent knowledge language while separating authored truth, enterprise operational truth, immutable audit history, and rebuildable runtime indexes.

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
- Structured source envelopes and compatibility contracts for external Agent/Skill adapters; KnowledgeOS itself does not provide multimodal extraction UI or media extraction
- Ontology-derived validation for externally proposed structured candidates, with evidence checks and no implicit writes
- Extraction protocol v2 server side: registry-generated extraction contracts, versioned source registration with block diffs and facts-at-risk, deterministic Chinese-aware value normalization, field-level evidence acceptance, governed ChangeSet proposals, and pending-verification/draft/rule-signal queues (`/v2/extraction/*`)
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

4.0 adds a generated RDF/JSON-LD and SHACL projection, SKOS terminology, PROV-O provenance export, portable ontology assistance, competency checks and explicit batch review. Git-authored truth and connector authority remain the source of record.

## Quick start

For a one-command local preview of the backend API and Studio (macOS/Linux, Python ≥ 3.10, Node.js ≥ 22.13):

```bash
bash scripts/preview.sh   # opens http://localhost:5173 ; sign in with local-admin-token
```


Python 3.10+ is required for the backend. The commands below work in macOS/Linux shells and Windows PowerShell after Python is on `PATH`:
On macOS installations that expose Python as `python3`, use `python3` in place of `python`.

```bash
python -m pip install -e '.[test]'
```

```bash
knowledgeos doctor
knowledgeos rebuild
knowledgeos search "KnowledgeOS"
knowledgeos get org:acme
knowledgeos context org:acme --domain industry
knowledgeos agent-capabilities --domain industry
knowledgeos agent-skill industry-evidence-brief
knowledgeos agent-request "What is Acme's market position?" --domain industry --target org:acme
knowledgeos control
knowledgeos verify-ledger
python -m pytest -q
```

Start the local API:

```bash
knowledgeos dev-serve --port 8787
```

`dev-serve` always binds `127.0.0.1` and accepts the development-only admin token `local-admin-token`. It also accepts `local-reviewer-token` as a distinct reviewer identity for testing high-risk ChangeSets. These keys remain available after restarting the local API. For a separately configured server, use:

```bash
export KNOWLEDGEOS_AUTH_MODE=required
export KNOWLEDGEOS_AUTH_TOKENS='{"replace-with-a-long-random-token":{"principal":"local:admin","roles":["admin"]}}'
knowledgeos serve --bind 127.0.0.1 --port 8787
curl -H 'Authorization: Bearer replace-with-a-long-random-token' \
  'http://127.0.0.1:8787/v1/get?id=org:acme'
```

Supported roles are `reader`, `agent`, `reviewer`, `publisher`, `template_author`, `template_reviewer`, `template_publisher`, and `admin`. Normal `serve` tokens live only in the process environment. `KNOWLEDGEOS_AUTH_MODE=disabled` is available solely for explicit local test harnesses.

Runtime databases are created in `runtime/` and intentionally ignored by Git. The query index is disposable; both `knowledge.state.db` and `knowledge.ledger.db` must be backed up together. Rebuild replays durable connector records and preserves governance state:

```bash
knowledgeos rebuild
```

Studio users sign in using their own backend bearer token. The proxy forwards that identity; there is no shared privileged Studio token. Tokens are held in page memory only. Demo mode requires an explicit user action and is separate from connection failure.

For an offline, checksummed backup, stop API/CLI writers and run `knowledgeos backup <new-backup-directory>`. Restore the matching Git-authored checkout into a separate workspace with an empty runtime, then run `knowledgeos restore <backup-directory>`, `knowledgeos rebuild`, and `knowledgeos verify-ledger`. Restore refuses to overwrite existing databases. On Windows, quote paths containing spaces.

In PowerShell, set server credentials with `$env:KNOWLEDGEOS_AUTH_MODE = 'required'` and `$env:KNOWLEDGEOS_AUTH_TOKENS = '{"replace-with-a-long-random-token":{"principal":"local:admin","roles":["admin"]}}'` before running `knowledgeos serve`. The repository-local `bin/knowledgeos` file remains available on Unix; Windows uses the installed `knowledgeos` entry point.

The current control/API contract is **4.0**, with backend and Studio releases **4.0.0**. See [the 4.0 delivery and rollback guide](docs/knowledgeos-4.0.md) for the semantic application profile, assisted modeling, terminology and upgrade procedure. [docs/iteration-3.5.md](docs/iteration-3.5.md) remains the preceding accepted review baseline; CI validates the Python backend and TypeScript Studio separately.

## Repository map

```text
control/      Knowledge code: ontology, predicates, policies, models, domain packs
knowledge/    Git-authored knowledge truth
connectors/   Enterprise source mapping examples
src/          Python compiler, index, ledger, services, API, connector and engine
runtime/      Rebuildable query state plus durable local audit ledger
pytests/      Python backend architecture and behavior tests
prototype/ontology-studio/tests/  TypeScript Studio tests
docs/         Architecture decisions and implementation mapping
prototype/    KnowledgeOS structure and governance control panel
```

See [docs/architecture-v3.md](docs/architecture-v3.md) for the frozen design-to-code mapping and [docs/contracts.md](docs/contracts.md) for authoring and API contracts.
See [docs/extraction.md](docs/extraction.md) for the compatibility protocol used by external extraction Agents and Skills; it is not a KnowledgeOS Studio product page.
See [docs/extraction-v2.md](docs/extraction-v2.md) for extraction protocol v2: contract, source registration, acceptance rules, normalization, proposals and queues, with a worked example in `docs/examples/extraction-v2/`.
See [docs/agent-service.md](docs/agent-service.md) for the Agent/LLM request, tool, grounding, and response protocol.
See [docs/authoring-assistant.md](docs/authoring-assistant.md) for model-assisted authoring in Studio: the deterministic `/v1/authoring` contract (context, standard instructions, dry-run checks), the optional `knowledgeos-assistant` gateway, and how assistant-written drafts stay inside ChangeSet review. The kernel itself never calls a model.
See [docs/python-backend.md](docs/python-backend.md) for migration and validation commands.
See [docs/knowledgeos-v3.5-guide.html](docs/knowledgeos-v3.5-guide.html) for a Chinese, diagram-rich V3.5 architecture and Ontology Studio user guide.
See [docs/units-formulas-fx.md](docs/units-formulas-fx.md) for physical units, currencies, source-backed FX, bound empirical formulas, and Studio review behavior.
