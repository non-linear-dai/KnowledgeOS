# Python backend migration

The TypeScript Ontology Studio continues to use its server-side `/api/knowledgeos/*` proxy. Set `KNOWLEDGEOS_API_BASE_URL` to the Python API origin. The proxy forwards each user's bearer token; the Python API authenticates and authorizes the principal.

For Studio development, put the URL in `prototype/ontology-studio/.env.local`. For a built Wrangler preview, pass it as a Worker binding, for example `npm start -- --port 15174 --var KNOWLEDGEOS_API_BASE_URL:http://127.0.0.1:8787`. Hosted deployments also need that Worker binding configured.

The backend entry point is the installed `knowledgeos` command, implemented in `src/knowledge_os/`. Python 3.10 or newer is required. Install dependencies with `python -m pip install -e '.[test]'`, then run `knowledgeos doctor`, `knowledgeos rebuild`, and `knowledgeos serve --bind 127.0.0.1 --port 8787`. The repository-local `bin/knowledgeos` file also works on Unix.

The Python backend implements the `/health` and `/v1/*` routes, including Agent capability discovery, skill retrieval, request preparation, response grounding, tool invocation, extraction, governance, and deterministic calculation. CLI commands use the same service classes.

`knowledge/` and `control/` remain Git-authored truth. The query index is disposable. Durable ChangeSets, connector records, model runs, snapshots, and the audit outbox live in `runtime/knowledge.state.db`; the hash-chained audit trail lives in `runtime/knowledge.ledger.db`. The Python backend reads existing Ruby-era ledger hashes and migrates legacy index state into the durable database. Back up state and ledger together while writers are stopped before migration. Rebuild replays connector records.

Authored Markdown, YAML, JSON, connector NDJSON, CLI input files, and backup manifests use UTF-8 on every operating system. UTF-8 BOM is accepted on authored and CLI input files. Model and materializer command adapters exchange UTF-8 JSON over standard input and output. Windows system code pages such as GBK do not change this file and adapter contract. Existing files that were previously saved as GBK must be converted to UTF-8 before compilation; the compiler does not rewrite Git-authored sources.

For validation, run `python -m pytest -q`, then from `prototype/ontology-studio` run `npm run install:ci`, `pnpm test`, `pnpm exec tsc --noEmit`, and `pnpm build`. Backend tests use temporary workspaces and never modify the repository's runtime databases.
