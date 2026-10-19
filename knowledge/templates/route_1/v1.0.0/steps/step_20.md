---
base:
  schema:
    ckm: '3.0'
  node:
    id: route-step:route_1:1.0.0:step_20
    kind: entity
    type: route_step
    key: step_20
    key_namespace: expert_template:route_1:1.0.0
    label: 清洗
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
    route_key: step_20
    cost_basis: piece
  assertions: []
  relations:
  - predicate: route_uses_operation
    target: operation:operation_21:1.0.0
  - predicate: route_precedes
    target: route-step:route_1:1.0.0:step_21
  logic_refs: []
external:
  assertions_ref: null
---
