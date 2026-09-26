---
base:
  schema:
    ckm: "3.0"
  node:
    id: "org:acme"
    kind: entity
    type: organization
    key: ACME
    label: Acme Industrial Systems
    aliases:
      - Acme
  classification:
    tags: [supplier, industrial-automation]
  lifecycle:
    state: active
  version:
    entity_revision: 1
knowledge:
  attrs:
    legal_name: Acme Industrial Systems Ltd.
    country: China
  assertions:
    - id: "assertion:acme-finding-2026-01"
      predicate: finding
      value:
        type: text
        literal: Acme has expanded its industrial automation portfolio toward smart assembly cells.
      qualifiers:
        scope: corporate_strategy
      temporal:
        observed_at: "2026-09-01T00:00:00Z"
        valid_from: "2026-09-01T00:00:00Z"
        valid_to:
      epistemic:
        assertion_kind: finding
        status: confirmed
        confidence: 0.82
      provenance:
        evidence_refs:
          - "evidence:acme-annual-review-2026"
        source_refs:
          - "source:acme-annual-review-2026"
      version:
        supersedes:
  relations:
    - predicate: participates_in
      target: "market:industrial-automation"
    - predicate: supplies
      target: "product:servo-module"
  logic_refs: []
external:
  assertions_ref:
---

# Acme Industrial Systems

## Curated Context

Reference organization used to demonstrate one global identity across supplier, industry-company, and project-partner roles.

## Important Findings

The role is expressed through relations and assertions rather than duplicate domain-specific entities.

## Open Questions

- Confirm the next audited supplier capacity update.

## Decision Summary

No active decision.

## Curated Timeline

- 2026-09: Added as the cross-domain reference organization.

