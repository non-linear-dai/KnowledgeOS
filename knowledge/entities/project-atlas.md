---
base:
  schema:
    ckm: "3.0"
  node:
    id: "project:atlas"
    kind: entity
    type: project
    key: ATLAS
    label: Project Atlas
    aliases: []
  classification:
    tags: [project-management]
  lifecycle:
    state: active
  version:
    entity_revision: 1
knowledge:
  attrs:
    summary: Reference project demonstrating temporal status and dependency traversal.
  assertions:
    - id: "assertion:atlas-risk-2026-09"
      predicate: risk_level
      value:
        type: enum
        literal: medium
      qualifiers:
        method: project-risk-matrix-v1
      temporal:
        observed_at: "2026-09-20T00:00:00Z"
        valid_from: "2026-09-20T00:00:00Z"
        valid_to:
      epistemic:
        assertion_kind: risk_assessment
        status: confirmed
        confidence: 0.9
      provenance:
        evidence_refs:
          - "evidence:atlas-weekly-review-2026-09-20"
        source_refs:
          - "pm:atlas:risk-register"
      version:
        supersedes:
  relations:
    - predicate: contains
      target: "task:atlas-m1"
  logic_refs: []
external:
  assertions_ref:
---

# Project Atlas

## Curated Context

Operational task status belongs to the PM source; this Markdown record stores durable context and reviewed risk interpretation.

## Important Findings

The first milestone has a supplier-data dependency.

## Open Questions

- Confirm whether the supplier data package will clear the next gate.

## Decision Summary

Escalate only if the milestone dependency remains unresolved at the gate review.

## Curated Timeline

- 2026-09-20: Project risk assessed as medium.

