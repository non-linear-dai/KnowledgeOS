---
base:
  schema:
    ckm: '3.0'
  node:
    id: route-group:route_1:1.0.0:group_1
    kind: entity
    type: route_group
    key: group_1
    key_namespace: expert_template:route_1:1.0.0
    label: 内层线路
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
    route_key: group_1
    iteration_set: items_group_1
    join_policy: all
    group_execution:
      group_mode: conditional
      execution_mode: parallel
  assertions: []
  relations:
  - predicate: group_contains
    target: route-step:route_1:1.0.0:step_2
  - predicate: group_contains
    target: route-step:route_1:1.0.0:step_3
  - predicate: route_precedes
    target: route-group:route_1:1.0.0:group_2
  logic_refs: []
external:
  assertions_ref: null
---
