---
base:
  schema:
    ckm: '3.0'
  node:
    id: route-step:route_1:1.0.0:step_9
    kind: entity
    type: route_step
    key: step_9
    key_namespace: expert_template:route_1:1.0.0
    label: 背钻
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
    route_key: step_9
    cost_basis: piece
  assertions: []
  relations:
  - predicate: route_uses_operation
    target: operation:operation_10:1.0.0
  - predicate: route_precedes
    target: route-step:route_1:1.0.0:step_10
  logic_refs: []
external:
  assertions_ref: null
---
