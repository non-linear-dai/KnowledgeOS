---
name: industry-evidence-brief
description: Build a time-bounded company, market, product, or technology brief from KnowledgeOS evidence. Use for industry research and evidence synthesis; do not use for deterministic cost calculation or project execution status.
metadata:
  short-description: Synthesize cited industry evidence
---

# Industry Evidence Brief

Produce a decision-oriented research brief that separates sourced facts, bounded inference, conflicting evidence, and unknowns. Keep `contract.yaml` with this file when copying the skill to another Agent.

## Required capabilities

Use KnowledgeOS operations named `resolve`, `search`, `get`, `context`, `history`, and `explain`. If those exact tools are unavailable, ask the caller to provide equivalent JSON evidence; do not substitute unsupported web recollection.

## Workflow

1. Define the research question, subjects, scope, and `as_of` time before synthesis.
2. Resolve the relevant companies, markets, products, and technologies, then retrieve their findings and relations.
3. Compare source authority, confidence, observation time, validity, supporting evidence, and contradictions.
4. Label inference as inference and state what evidence bounds it.
5. Write a concise brief with citations, evidence limitations, gaps, and useful next research actions.

## Output

Return an answer, key findings, evidence limits, knowledge gaps, and recommended actions. Every factual claim must cite one or more evidence references from KnowledgeOS. Preserve the source's original time and scope when comparing market statements.

## Guardrails

- Never present an unsupported inference as an observed fact.
- Do not merge market figures with different definitions, currencies, or periods as if they were comparable.
- Do not fabricate citations or resolve an ambiguous entity silently.
- Research updates are proposals; durable publication requires a ChangeSet and review.

