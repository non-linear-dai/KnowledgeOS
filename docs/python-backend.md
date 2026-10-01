# Python backend migration

The TypeScript Ontology Studio continues to use its server-side `/api/knowledgeos/*` proxy. Set `KNOWLEDGEOS_API_BASE_URL` to the Python API origin. The proxy forwards each user's bearer token; the Python API authenticates and authorizes the principal.

For Studio development, put the URL in `prototype/ontology-studio/.env.local`. For a built Wrangler preview, pass it as a Worker binding, for example `npm start -- --port 15174 --var KNOWLEDGEOS_API_BASE_URL:http://127.0.0.1:8787`. Hosted deployments also need that Worker binding configured.

The backend entry point is `bin/knowledgeos`, implemented in `src/knowledge_os/`. Python 3.10 or newer is required. Install dependencies with `python3 -m pip install -e '.[test]'`, then run `bin/knowledgeos doctor`, `bin/knowledgeos rebuild`, and `bin/knowledgeos serve --bind 127.0.0.1 --port 8787`.

The Python backend implements the `/health` and `/v1/*` routes, including Agent capability discovery, skill retrieval, request preparation, response grounding, tool invocation, extraction, governance, and deterministic calculation. CLI commands use the same service classes.

`knowledge/` and `control/` remain Git-authored truth. The query index is disposable. Durable ChangeSets, connector records, model runs, snapshots, and the audit outbox live in `runtime/knowledge.state.db`; the hash-chained audit trail lives in `runtime/knowledge.ledger.db`. The Python backend reads existing Ruby-era ledger hashes and migrates legacy index state into the durable database. Back up state and ledger together while writers are stopped before migration. Rebuild replays connector records.

For validation, run `python3 -m pytest -q`, then from `prototype/ontology-studio` run `pnpm test`, `pnpm exec tsc --noEmit`, and `pnpm build`. Backend tests use temporary workspaces and never modify the repository's runtime databases.
