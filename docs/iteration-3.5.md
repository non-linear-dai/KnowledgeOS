# Review iteration — contract 3.5

This iteration implements review items 1–4 and 6–10. Review item 5 (deployment platform adapters and expanded CI) is excluded.

| Review | Result | Primary regression coverage |
| --- | --- | --- |
| 1. End-to-end identity | Personal bearer credentials, session discovery, upstream role enforcement, no shared proxy identity | security_test.rb, Studio proxy tests |
| 2. Durable state and recovery | Separate state database, legacy migration, durable outbox, connector replay, checksummed offline backup/restore | iteration_test.rb |
| 3. Incremental compilation | Control fingerprint invalidation, transactional rebuild, failed-compile rollback, affected-card updates | compiler_test.rb, iteration_test.rb |
| 4. Verified publication | Expected semantic result, atomic conditional transitions, idempotent proposals/publications | service_test.rb, iteration_test.rb |
| 6. Value/Schema contracts | Fail-closed Schema subset, typed quantities/decimals/dates/references, explicit model conversions | iteration_test.rb |
| 7. Shared ingest projection | Canonical validation, shared compiler projection, durable versions, tombstones, error isolation | service_test.rb, iteration_test.rb |
| 8. Domain identity | Namespace/type-scoped business keys, global canonical IDs, actual ontology source locations | iteration_test.rb |
| 9. Temporal consistency | UTC instants, half-open intervals, query-time freshness, recorded snapshots and historical graph | policy_test.rb, iteration_test.rb |
| 10. Frontend state | Explicit demo mode, distinct failure states, personal login, draft rebase/conflicts, live labels and dependency edges | tests/studio.test.mjs |

## Upgrade and operation

Back up existing runtime files before upgrading. On first open, the index's ChangeSet, audit-outbox, derived-result and review tables are migrated transactionally to `knowledge.state.db`. Legacy approvals lacking expected content cannot be published; submit a new verifiable ChangeSet. Existing authored canonical envelopes remain version 3.0, while the control/API catalog reports 3.5.

The durable pair is `knowledge.state.db` plus `knowledge.ledger.db`. The index is no longer needed to recover ChangeSets or newly captured connector records. Connector events captured before this upgrade contain hashes rather than complete records, so those historical records must be reingested from their source; they cannot be reconstructed from missing bytes.

Stop services and writers before `backup`. An exclusive runtime lease prevents backup or restore while the application is active. Backup uses SQLite's backup API, includes checksums and an authored-file fingerprint, and writes a manifest only after both durable databases are copied. Restore requires matching authored files and an empty runtime; it does not overwrite data. After restore run `rebuild` and `verify-ledger`. Interrupted backups without a manifest are incomplete and must not be used.

State and index use attached SQLite rollback-journal transactions for atomic cross-database writes. The ledger stays separate and receives outbox events only after the outer transaction commits. These stores target a single host and local filesystem; this iteration does not introduce distributed database operation.

New quantities must include a unit or use a predicate with an explicit default. The existing lead-time predicate defaults to `calendar_days`. No exchange rates or business calendars are inferred. Bare model inputs retain the declared model-unit semantics for compatibility; typed monetary inputs must all declare consistent currencies.

Historical attributes are available from the first captured recorded-time snapshot. A business-time query before that point returns an explicit gap. `recorded_as_of` makes the observation-time cutoff explicit; it does not imply that every external historical version existed before ingestion.

Studio credentials are personal backend bearer tokens, held only in memory. Refreshing the page requires login again. Demo data is loaded only by the explicit demo action. A failed connection retains the live snapshot/draft and cannot locally approve or publish real ChangeSets. Refresh rebases independent edits and surfaces conflicting edits without silently discarding them.

## Validation commands

```bash
rake test
cd prototype/ontology-studio
pnpm test
pnpm exec tsc --noEmit --incremental false
pnpm lint
pnpm build
```

The regression suite works on temporary workspaces; it covers index deletion, failed rebuilds, legacy migration, concurrent review, exact-result publication, timezone equivalence, long-running expiration, typed values, connector deletion/replay, backup/restore and historical snapshots. Frontend tests cover credential forwarding, failure states, draft merging/conflicts and live-contract validation.
