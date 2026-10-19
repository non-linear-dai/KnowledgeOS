---
base:
  schema:
    ckm: '3.0'
  node:
    id: route:route_1:1.0.0
    kind: entity
    type: route_template
    key: route_1
    key_namespace: expert_template:1.0.0
    label: PCB制造工艺
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
    template_family: general
    template_version: 1.0.0
    output_basis: piece
    template_cases:
      cases:
      - id: basic
        facts: {}
        sets: {}
        expected_operations: 21
    template_author: local:admin
  assertions: []
  relations:
  - predicate: route_contains
    target: route-group:route_1:1.0.0:group_1
  - predicate: route_contains
    target: route-group:route_1:1.0.0:group_2
  - predicate: route_contains
    target: route-step:route_1:1.0.0:step_1
  - predicate: route_contains
    target: route-step:route_1:1.0.0:step_18
  - predicate: route_contains
    target: route-step:route_1:1.0.0:step_19
  - predicate: route_contains
    target: route-step:route_1:1.0.0:step_20
  - predicate: route_contains
    target: route-step:route_1:1.0.0:step_21
  logic_refs: []
external:
  assertions_ref: null
---
