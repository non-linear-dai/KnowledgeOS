---
name: project-risk-review
description: Review project and milestone status, dependencies, gates, and risks from temporal KnowledgeOS evidence. Use for delivery health and risk reviews; do not use for market research or unsupported schedule forecasting.
metadata:
  short-description: Review cited project risks
---

# Project Risk Review

Produce a time-aware project health assessment from governed status and dependency evidence. Keep `contract.yaml` with this file when copying the skill to another Agent.

## Required capabilities

Use KnowledgeOS operations named `resolve`, `get`, `context`, `neighbors`, `history`, `calculate`, and `explain`. If those exact tools are unavailable, ask the caller to provide equivalent JSON evidence instead of guessing status.

## Workflow

1. Resolve the project, tasks, milestones, baseline, and review time.
2. Retrieve the PM-domain context and traverse dependency and risk relations.
3. Compare current status against the requested baseline or `as_of` state.
4. When baseline and forecast dates are evidenced, call `schedule_variance`; never subtract dates in model prose.
5. When the required governed counts are available, call `project_risk_score` and preserve its trace and classification.
6. Identify blockers, stale status, missing evidence, and contradictions before prioritizing risks.
7. Report evidence-backed status, risks, dependencies, gaps, and recommended actions.

## Output

Return an answer, current status, prioritized risks, dependencies, knowledge gaps, and recommended actions. Cite every factual claim. Leave owners, dates, and completion percentages unspecified unless evidence provides them.

## Guardrails

- Do not infer schedule dates, owners, or completion percentages from narrative language.
- Keep operational PM facts in their authoritative source system.
- Do not treat a recommendation as an executed change.
- Represent durable Agent-originated updates only as ChangeSets.
