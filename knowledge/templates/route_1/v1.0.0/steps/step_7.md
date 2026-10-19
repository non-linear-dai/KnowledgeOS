---
base:
  schema:
    ckm: '3.0'
  node:
    id: route-step:route_1:1.0.0:step_7
    kind: entity
    type: route_step
    key: step_7
    key_namespace: expert_template:route_1:1.0.0
    label: 电镀
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
    route_key: step_7
    cost_basis: piece
  assertions: []
  relations:
  - predicate: route_uses_operation
    target: operation:operation_8:1.0.0
  - predicate: route_precedes
    target: route-step:route_1:1.0.0:step_8
  logic_refs: []
external:
  assertions_ref: null
---
