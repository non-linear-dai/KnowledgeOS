---
base:
  schema:
    ckm: "3.0"
  node:
    id: "task:atlas-m1"
    kind: entity
    type: task
    key: ATLAS-M1
    label: Atlas Milestone 1
    aliases: []
  classification:
    tags: [project-management, milestone]
  lifecycle:
    state: active
  version:
    entity_revision: 1
knowledge:
  attrs:
    summary: First governed delivery milestone for Project Atlas.
  assertions:
    - id: "assertion:atlas-m1-status-2026-09-20"
      predicate: task_status
      value:
        type: enum
        literal: in_progress
      qualifiers:
        baseline: v1
      temporal:
        observed_at: "2026-09-20T00:00:00Z"
        valid_from: "2026-09-20T00:00:00Z"
        valid_to:
      epistemic:
        assertion_kind: status_assessment
        status: confirmed
        confidence: 1.0
      provenance:
        evidence_refs:
          - "snapshot:pm-atlas-m1-2026-09-20"
        source_refs:
          - "pm:atlas:milestone-1"
      version:
        supersedes:
  relations:
    - predicate: depends_on
      target: "org:acme"
  logic_refs: []
external:
  assertions_ref:
---

# Atlas Milestone 1

## Curated Context

Reference milestone. In production its operational status should be supplied by the PM connector.

## Important Findings

The dependency on Acme represents a role relation, not a duplicate organization record.

## Open Questions

- Is the delivery evidence complete?

## Decision Summary

No active decision.

## Curated Timeline

- 2026-09-20: Milestone entered in-progress status.

