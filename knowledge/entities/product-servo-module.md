---
base:
  schema:
    ckm: "3.0"
  node:
    id: "product:servo-module"
    kind: entity
    type: product
    key: SERVO-MODULE-X1
    label: Servo Module X1
    aliases: []
  classification:
    tags: [cost-analysis]
  lifecycle:
    state: active
  version:
    entity_revision: 1
knowledge:
  attrs:
    summary: Reference servo module used for deterministic cost analysis.
  assertions:
    - id: "assertion:servo-unit-cost-2026q3"
      predicate: unit_cost
      value:
        type: quantity
        literal: 128.45
        unit: USD_per_unit
      qualifiers:
        cost_basis: standard
      temporal:
        observed_at: "2026-09-01T00:00:00Z"
        valid_from: "2026-07-01T00:00:00Z"
        valid_to: "2026-09-30T23:59:59Z"
      epistemic:
        assertion_kind: measurement
        status: confirmed
        confidence: 1.0
      provenance:
        evidence_refs:
          - "snapshot:erp-standard-cost-2026q3"
        source_refs:
          - "erp:material:SERVO-MODULE-X1"
      version:
        supersedes:
  relations:
    - predicate: uses
      target: "org:acme"
  logic_refs:
    - cost_rollup
external:
  assertions_ref:
---

# Servo Module X1

## Curated Context

The authored record carries product semantics. Operational price updates should arrive from ERP through a connector.

## Important Findings

Cost outputs must use the deterministic model and retain an input trace.

## Open Questions

- Confirm the next quarterly cost release.

## Decision Summary

No active decision.

## Curated Timeline

- 2026-Q3: Reference standard cost published.

