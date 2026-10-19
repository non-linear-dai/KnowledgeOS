---
base:
  schema:
    ckm: '3.0'
  node:
    id: route-step:route_1:1.0.0:step_17
    kind: entity
    type: route_step
    key: step_17
    key_namespace: expert_template:route_1:1.0.0
    label: 锣板
    aliases: []
  classification:
    tags:
    - expert_template
  lifecycle:
    state: active
  version:
    entity_revision: 1
knowledge:
  attrs:
    route_key: step_17
    cost_basis: piece
  assertions: []
  relations:
  - predicate: route_uses_operation
    target: operation:operation_18:1.0.0
  logic_refs: []
external:
  assertions_ref: null
---
