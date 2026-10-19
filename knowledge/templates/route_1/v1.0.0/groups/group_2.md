---
base:
  schema:
    ckm: '3.0'
  node:
    id: route-group:route_1:1.0.0:group_2
    kind: entity
    type: route_group
    key: group_2
    key_namespace: expert_template:route_1:1.0.0
    label: 整板加工
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
    route_key: group_2
    iteration_set: items_group_2
    join_policy: all
    group_execution:
      group_mode: conditional
      execution_mode: parallel
  assertions: []
  relations:
  - predicate: group_contains
    target: route-step:route_1:1.0.0:step_4
  - predicate: group_contains
    target: route-step:route_1:1.0.0:step_5
  - predicate: group_contains
    target: route-step:route_1:1.0.0:step_6
  - predicate: group_contains
    target: route-step:route_1:1.0.0:step_7
  - predicate: group_contains
    target: route-step:route_1:1.0.0:step_8
  - predicate: group_contains
    target: route-step:route_1:1.0.0:step_9
  - predicate: group_contains
    target: route-step:route_1:1.0.0:step_10
  - predicate: group_contains
    target: route-step:route_1:1.0.0:step_11
  - predicate: group_contains
    target: route-step:route_1:1.0.0:step_12
  - predicate: group_contains
    target: route-step:route_1:1.0.0:step_13
  - predicate: group_contains
    target: route-step:route_1:1.0.0:step_14
  - predicate: group_contains
    target: route-step:route_1:1.0.0:step_15
  - predicate: group_contains
    target: route-step:route_1:1.0.0:step_16
  - predicate: group_contains
    target: route-step:route_1:1.0.0:step_17
  - predicate: route_precedes
    target: route-step:route_1:1.0.0:step_18
  logic_refs: []
external:
  assertions_ref: null
---
