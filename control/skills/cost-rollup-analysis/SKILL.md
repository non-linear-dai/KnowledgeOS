---
name: cost-rollup-analysis
description: Analyze governed product or component unit cost with KnowledgeOS evidence and the deterministic cost_rollup model. Use for cost breakdowns, scenarios, and trace explanations; do not use for market research or project status reviews.
metadata:
  short-description: Explain governed cost roll-ups
---

# Cost Roll-up Analysis

Produce an auditable cost answer whose numeric conclusions come from the deterministic model rather than model prose. Keep `contract.yaml` with this file when copying the skill to another Agent.

## Required capabilities

Use KnowledgeOS operations named `resolve`, `get`, `context`, `calculate`, and `explain`. If those exact tools are unavailable, ask the caller to provide equivalent JSON responses; do not invent results.

## Workflow

1. Resolve the product or component and retrieve its cost-domain context.
2. Check every input's source authority, unit, scenario, and validity window. Report missing inputs before calculating.
3. Call `calculate` with `model_id: cost_rollup` for every numeric scenario. Preserve the returned `derived:<run_id>` evidence reference.
4. Compare scenarios only when units, currencies, and time bases are compatible.
5. Explain the result with cited inputs, deterministic trace, assumptions, and gaps.

## Output

Return a concise answer, cost breakdown, assumptions, knowledge gaps, and recommended actions. Every factual claim needs a KnowledgeOS evidence reference. A calculated claim must cite its `derived:<run_id>` reference.

## Guardrails

- Never perform unit-sensitive or monetary arithmetic in prose.
- Do not silently convert currencies, units, or validity windows without a registered conversion model.
- Treat recommendations as proposals. Durable updates require a ChangeSet and must reach the authoritative source before publication.
- Do not write operational cost facts into Markdown when an enterprise source is authoritative.

