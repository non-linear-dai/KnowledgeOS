# Agent and LLM service

KnowledgeOS exposes a provider-neutral service boundary between the canonical knowledge kernel and agents or language models. It does not call a particular model provider. Any provider that accepts JSON and returns the declared contract can participate.

## End-to-end flow

```text
capabilities -> request -> optional invoke calls -> model -> respond
                                      |
                                      +-> propose creates a ChangeSet only
```

### 1. Discover capabilities

`GET /v1/agent/capabilities?domain=industry` returns the domain workflow, retrieval profile, registered skills, tool allowlist, JSON input contracts, response schema, and a control-plane fingerprint. `GET /v1/agent/skill?id=industry-evidence-brief` returns the exact files for one independently copyable package.

### 2. Prepare a grounded task

`POST /v1/agent/request` accepts `question`, `domain`, and optional `target`, `query`, `skill`, `as_of`, and `max_items`. The service selects the domain's default skill unless a matching skill is requested, resolves the target, builds the C-R-L-T-P context, and emits evidence records with stable `ref` identifiers.

The returned packet is safe to send to a model. It tells the model to cite only those references, expose knowledge gaps, route deterministic work through tools, and omit hidden chain-of-thought.

### 3. Invoke governed tools

`POST /v1/agent/invoke` accepts `domain`, `operation`, and `arguments`. It dispatches through explicit code paths; arbitrary method names and SQL are not accepted. The operation must be present in the domain pack's `tool_policy.allow`. A deterministic model must also be declared by the domain before `calculate` can run.

Read operations return the ordinary KnowledgeOS envelope. `propose` is the only model-facing mutation and creates a reviewable ChangeSet without changing Git-authored truth or an enterprise source.

### 4. Validate model output

`POST /v1/agent/respond` accepts the original request packet, the model's JSON object, and optional `tool_results`. Every claim requires a statement, confidence from 0 to 1, and one or more evidence references. Deterministic tool results are accepted only when their `run_id` resolves to a stored derived-result trace. Unknown references, unknown runs, stale control fingerprints, request mismatches, unsupported fields, and malformed structures fail validation.

Successful output contains the answer, grounded claims, gaps, recommended actions, grounding metrics, cited trace references, and `write_performed: false`.

## Domain skills

Each Git-authored skill is a self-contained directory under `control/skills/`:

```text
skill-name/
|-- SKILL.md       Agent-facing, portable instructions
`-- contract.yaml  KnowledgeOS machine policy and capability contract
```

`SKILL.md` follows the conventional skill format and can be copied with its directory to another Agent for testing or use. It contains discovery metadata, workflow, expected tools, output guidance, and guardrails without depending on sibling packages. `contract.yaml` is not an Agent prompt: the Registry uses it to validate domain membership, allowed tools, deterministic models, inputs, and output requirements. Package directory name, contract id, frontmatter name, and entrypoint must match.

For a filesystem-based Agent, copy the complete directory rather than only one file:

```bash
cp -R control/skills/industry-evidence-brief /path/to/agent/skills/
```

For a remote Agent, call `GET /v1/agent/skill?id=industry-evidence-brief` or `bin/knowledgeos agent-skill industry-evidence-brief`; the returned `files` object contains the exact two files to recreate.

- `cost-rollup-analysis` requires governed inputs and the deterministic `cost_rollup` trace.
- `industry-evidence-brief` separates sourced facts, bounded inference, conflicting evidence, and unknowns.
- `project-risk-review` traverses project/task dependencies and respects temporal status without inventing dates or owners.

Registry loading fails when a package is incomplete, names an unknown domain/tool/model, has mismatched identities, or is selected as the wrong domain default.
