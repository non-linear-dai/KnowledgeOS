# Multi-source knowledge candidate extraction

KnowledgeOS separates media acquisition from semantic extraction and durable publication:

```text
source/media -> materializer -> source envelope -> live registry contract
             -> model adapter -> deterministic candidate builder -> human/governed write path
```

Candidate extraction never writes to `knowledge/`, `control/`, the runtime projection, the ledger, or the ChangeSet table.

## Supported source envelope kinds

The default profile accepts `text`, `file`, `web`, `audio`, `meeting_minutes`, `image`, `video`, `email`, and `chat`. Additional normalized source kinds can be added in an extraction profile without changing the extraction engine.

- Plain UTF-8 text files, Markdown, CSV, JSON, YAML, HTML, VTT, and SRT can be read directly.
- Web pages can be supplied as captured/cleaned text while retaining the original URL as `locator`.
- Audio requires a transcript. A speech-to-text process can be attached as a materializer command.
- Binary documents, OCR, email exports, and platform-specific meeting records use the same materializer command contract.

A materializer receives JSON on stdin:

```json
{
  "protocol_version": "knowledgeos.materialization.v1",
  "source": {
    "kind": "audio",
    "locator": "/records/review.m4a"
  }
}
```

It returns normalized content:

```json
{
  "content": "speaker-labelled transcript...",
  "mime_type": "text/plain",
  "metadata": { "engine": "configured-transcriber" }
}
```

## Two-phase model-neutral workflow

Generate an extraction request from the current control plane:

```bash
bin/knowledgeos extract-contract \
  --source-type web \
  --locator 'https://example.test/page' \
  --content-file /tmp/page.txt
```

The request contains:

- normalized, addressable source segments;
- current concept/property and relation constraints;
- source-relevant entity identity hints (the final resolver searches the current index, not just this bounded hint list);
- a provider-neutral JSON Schema for model output;
- `protocol_version` and `registry_fingerprint` guards.

The model must copy both guards into its response. If an ontology, predicate, canonical schema, provenance/authority policy, or extraction profile changes before finalization, KnowledgeOS rejects the stale response and requires a fresh extraction request.

The HTTP equivalents are:

- `POST /v1/extraction/request` with `{ "source": ... }`;
- `POST /v1/extraction/candidates` with `{ "request": ..., "model_output": ... }`.
- `POST /v1/extraction/propose` with `{ "request": ..., "model_output": ..., "input_index": 0, "reason": "..." }` to re-resolve one candidate and create a governed ChangeSet. The authenticated principal is its actor.

For an `unresolved` mention, a principal with both proposal and review permission may provide `resolved_id` and `resolution_reason` to this endpoint. The ID must be one of the cited server candidates. This creates a high-risk ChangeSet requiring a different reviewer; an arbitrary ID or unsupported mention cannot be forced through.

This lets any LLM platform use its own SDK or structured-output facility without adding that SDK to the KnowledgeOS kernel.
The HTTP request endpoint requires already materialized `content` or a complete source envelope; it never reads an arbitrary server-local path. Local file reading and materializer commands are CLI-only capabilities.

## One-step command adapter

A model wrapper may also read the extraction request as JSON from stdin and write only model-output JSON to stdout:

```bash
bin/knowledgeos extract \
  --source-type meeting_minutes \
  --source review-notes.md \
  --model-command '/opt/company/bin/knowledge-extractor'
```

Switching providers means replacing or reconfiguring that wrapper. The ontology contract and candidate validation remain unchanged.

## Candidate behavior

For every extracted entity, the deterministic builder:

1. searches all active instances of the concept type using cited IDs, scoped business keys, names, aliases, and cited linked context; an `existing_id` from the model is checked against source evidence;
2. accepts only predicates bound to the current concept type;
3. checks evidence quotes against cited source segments;
4. attaches source IDs, hashes, timestamps, authority classes, and evidence references;
5. validates a full merged canonical node with the current registry;
6. emits either a full `create_instance`, JSON-style `update_instance` operations, or `no_change` only when identity is resolved;
7. returns weak or conflicting matches in `unresolved`, invalid entities in `rejected`, and connector-only facts in `unmapped_facts`.

The resolver never treats a missing item from `existing_entities` as proof that an entity is new. A new identity must appear in exact cited text and have no matching candidate. Same-name ambiguity blocks both update and creation. `context_ids` can distinguish otherwise identical names when the cited source names those context entities and the graph contains the corresponding link. Source records with a stable locator keep the same source ID across content versions; callers should supply a stable source ID or locator for repeatable meeting and document ingestion.

Attribute candidates retain the previous value, source hash, and evidence references. Git-authored instance patches preserve the original document body, bump `entity_revision`, and are applied only after ChangeSet review. Assertions are emitted as `proposed`; `supersedes_id` may reference a confirmed assertion on the same authored instance and predicate. Published extraction ChangeSets record a durable source-mention-to-instance binding, which subsequent versions can reuse. `knowledge.state.db` and `knowledge.ledger.db` must be backed up together.

Operational predicates whose authority policy has no Git primary and uses non-Git history are returned as unmapped connector work; they are not written into Markdown. Connector-backed instances use `publication_route: upstream_source_system` and cannot be proposed through `/v1/extraction/propose`. Versioned `decision_table` assets remain maintained through the expert template workbench; ordinary `decision` instances can receive reviewed attribute and assertion updates.

Candidates targeting Git-authored nodes declare `publication_route: governed_changeset`. Candidates targeting connector-projected operational nodes declare `publication_route: upstream_source_system`; they must be applied to the authoritative application rather than copied into Markdown.
