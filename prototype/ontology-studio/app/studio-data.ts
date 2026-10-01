export type DefinitionKind =
  | "concept"
  | "relation"
  | "predicate"
  | "policy"
  | "model"
  | "unit"
  | "currency"
  | "domain"
  | "connector"
  | "schema"
  | "business_constraint"
  | "business_rule";

export type Lifecycle = "draft" | "active" | "deprecated" | "merged" | "retired";

export type ConfigValue = string | boolean | number | null | ConfigValue[] | { [key: string]: ConfigValue };

export interface ConceptBinding {
  predicateId: string;
  required: boolean;
  cardinality: "inherit" | "one" | "many" | "temporal_many";
  group: "identity" | "profile" | "measurement" | "governance" | "other";
  inherited?: boolean;
}

export interface RelationEndpoint {
  sourceConceptId: string;
  targetConceptId: string;
  sourceCardinality: "one" | "many";
  targetCardinality: "one" | "many";
}

export type RelationMode = "simple" | "reifiable" | "reified";

export interface RelationReification {
  nodeType: string;
  identity: "optional" | "required";
  properties: ConceptBinding[];
}

export interface OntologyDefinition {
  id: string;
  kind: DefinitionKind;
  label: string;
  description: string;
  lifecycle: Lifecycle;
  sourcePath: string;
  refs: number;
  files: string[];
  config: Record<string, ConfigValue>;
  bindings?: ConceptBinding[];
  endpoints?: RelationEndpoint[];
  relationMode?: RelationMode;
  reification?: RelationReification;
  conceptScopes?: string[];
  readOnly?: boolean;
}

export interface DependencyEdge {
  id: string;
  source: string;
  target: string;
  relation: "governed_by" | "applies_to" | "relation_source" | "relation_target" | "domain_scope" | "reifies_as" | "reification_property" | "requires_model" | "model_input" | "model_output" | "model_scope" | "uses_relation" | "prioritizes" | "maps_to" | "conforms_to" | "business_scope" | "business_input";
  editable: boolean;
}

export type ChangeOperationType = "create" | "update" | "deprecate" | "delete";

export interface ChangeOperation {
  id: string;
  type: ChangeOperationType;
  targetId: string;
  targetKind: DefinitionKind;
  before: OntologyDefinition | null;
  after: OntologyDefinition | null;
}

export type ChangeSetStatus =
  | "proposed"
  | "review_required"
  | "approved"
  | "rejected"
  | "changes_requested"
  | "published";

export interface ChangeSet {
  id: string;
  title: string;
  reason: string;
  actor: string;
  targetSource: string;
  createdAt: string;
  risk: "low" | "normal" | "high";
  status: ChangeSetStatus;
  operations: ChangeOperation[];
  reviewNote?: string;
  reviewedBy?: string;
  reviewedAt?: string;
  sourceRevision?: string;
}

const concept = (
  id: string,
  label: string,
  description: string,
  refs: number,
  files: string[],
  bindings: ConceptBinding[] = [],
): OntologyDefinition => ({
  id,
  kind: "concept",
  label,
  description,
  lifecycle: "active",
  sourcePath: "control/ontology/core.yaml",
  refs,
  files,
  config: { schema_support: "target-contract" },
  bindings,
});

const relation = (
  id: string,
  label: string,
  description: string,
  refs: number,
  files: string[],
  endpoints: RelationEndpoint[] = [],
  relationMode: RelationMode = "simple",
  reification?: RelationReification,
): OntologyDefinition => ({
  id,
  kind: "relation",
  label,
  description,
  lifecycle: "active",
  sourcePath: "control/ontology/core.yaml",
  refs,
  files,
  config: { domain_range: endpoints.length ? "declared" : "undeclared", relation_mode: relationMode, schema_support: "target-contract" },
  endpoints,
  relationMode,
  reification,
});

const predicate = (
  id: string,
  label: string,
  description: string,
  valueType: string,
  cardinality: string,
  storage: string,
  authority: string,
  freshness: string,
  provenance: string,
  write: string,
  refs: number,
  files: string[],
  policyOverrides: Record<string, ConfigValue> = {},
): OntologyDefinition => ({
  id,
  kind: "predicate",
  label,
  description,
  lifecycle: "active",
  sourcePath: `control/predicates/${id}.yaml`,
  refs,
  files,
  config: {
    value_type: valueType,
    cardinality,
    storage_mode: storage,
    authority,
    freshness,
    provenance_tier: provenance,
    write,
    temporal: storage === "attr" ? "none" : "required",
    evidence: storage === "attr" ? "optional" : "required",
    history: storage === "attr" ? "git_only" : "full",
    embedding: id === "summary" || id === "finding",
    equivalent_to: null,
    ...policyOverrides,
  },
});

export const initialDefinitions: OntologyDefinition[] = [
  concept("organization", "组织", "企业、供应商与合作伙伴的统一身份。", 4, ["knowledge/entities/org-acme.md"], [
    { predicateId: "legal_name", required: true, cardinality: "inherit", group: "identity" },
    { predicateId: "country", required: false, cardinality: "inherit", group: "profile" },
    { predicateId: "finding", required: false, cardinality: "inherit", group: "governance" },
    { predicateId: "lead_time_days", required: false, cardinality: "inherit", group: "measurement" },
  ]),
  concept("market", "市场", "研究对象与市场范围。", 2, ["knowledge/entities/market-industrial-automation/entity.md"], [
    { predicateId: "summary", required: false, cardinality: "inherit", group: "profile" },
    { predicateId: "market_size", required: false, cardinality: "inherit", group: "measurement" },
    { predicateId: "finding", required: false, cardinality: "inherit", group: "governance" },
  ]),
  concept("product", "产品", "可被分析、供应或使用的产品对象。", 2, ["knowledge/entities/product-servo-module.md"], [
    { predicateId: "summary", required: false, cardinality: "inherit", group: "profile" },
    { predicateId: "unit_cost", required: false, cardinality: "inherit", group: "measurement" },
    { predicateId: "lead_time_days", required: false, cardinality: "inherit", group: "measurement" },
  ]),
  concept("project", "项目", "受治理的项目工作单元。", 2, ["knowledge/entities/project-atlas.md"], [
    { predicateId: "summary", required: false, cardinality: "inherit", group: "profile" },
    { predicateId: "risk_level", required: false, cardinality: "inherit", group: "governance" },
  ]),
  concept("task", "任务", "项目内可跟踪的任务或里程碑。", 2, ["knowledge/entities/task-atlas-m1.md"], [
    { predicateId: "summary", required: false, cardinality: "inherit", group: "profile" },
    { predicateId: "task_status", required: true, cardinality: "inherit", group: "governance" },
    { predicateId: "risk_level", required: false, cardinality: "inherit", group: "governance" },
  ]),
  concept("relation", "实体化关系", "需要自身属性、证据或时态的关系节点。", 0, [], [
    { predicateId: "lead_time_days", required: false, cardinality: "inherit", group: "measurement" },
    { predicateId: "risk_level", required: false, cardinality: "inherit", group: "governance" },
  ]),
  concept("decision", "决策", "经治理的业务或研究决策。", 0, [], [
    { predicateId: "summary", required: false, cardinality: "inherit", group: "profile" },
    { predicateId: "finding", required: false, cardinality: "inherit", group: "governance" },
    { predicateId: "risk_level", required: false, cardinality: "inherit", group: "governance" },
  ]),

  relation("participates_in", "参与", "表达组织或产品参与某一市场。", 2, ["control/domains/industry/pack.yaml", "knowledge/entities/org-acme.md"], [
    { sourceConceptId: "organization", targetConceptId: "market", sourceCardinality: "many", targetCardinality: "many" },
    { sourceConceptId: "product", targetConceptId: "market", sourceCardinality: "many", targetCardinality: "many" },
  ]),
  relation("supplies", "供应", "表达供应方与产品之间的供给关系。", 3, ["control/domains/cost/pack.yaml", "control/domains/industry/pack.yaml", "knowledge/entities/org-acme.md"], [
    { sourceConceptId: "organization", targetConceptId: "product", sourceCardinality: "many", targetCardinality: "many" },
  ], "reifiable", {
    nodeType: "relation",
    identity: "required",
    properties: [
      { predicateId: "lead_time_days", required: false, cardinality: "inherit", group: "measurement" },
      { predicateId: "risk_level", required: false, cardinality: "inherit", group: "governance" },
    ],
  }),
  relation("contains", "包含", "表达组合、项目或范围的包含关系。", 2, ["control/domains/cost/pack.yaml", "knowledge/entities/project-atlas.md"], [
    { sourceConceptId: "project", targetConceptId: "task", sourceCardinality: "one", targetCardinality: "many" },
  ]),
  relation("depends_on", "依赖", "表达任务、项目或组件的依赖关系。", 2, ["control/domains/pm/pack.yaml", "knowledge/entities/task-atlas-m1.md"], [
    { sourceConceptId: "task", targetConceptId: "task", sourceCardinality: "many", targetCardinality: "many" },
    { sourceConceptId: "task", targetConceptId: "organization", sourceCardinality: "many", targetCardinality: "many" },
  ]),
  relation("uses", "使用", "表达产品或分析对象对其他对象的使用。", 3, ["control/domains/cost/pack.yaml", "control/domains/industry/pack.yaml", "knowledge/entities/product-servo-module.md"], [
    { sourceConceptId: "product", targetConceptId: "product", sourceCardinality: "many", targetCardinality: "many" },
    { sourceConceptId: "product", targetConceptId: "organization", sourceCardinality: "many", targetCardinality: "many" },
  ]),
  relation("threatens", "威胁", "表达风险对目标的威胁关系。", 1, ["control/domains/pm/pack.yaml"], [
    { sourceConceptId: "relation", targetConceptId: "project", sourceCardinality: "many", targetCardinality: "many" },
    { sourceConceptId: "relation", targetConceptId: "task", sourceCardinality: "many", targetCardinality: "many" },
  ], "reified", {
    nodeType: "relation",
    identity: "required",
    properties: [{ predicateId: "risk_level", required: true, cardinality: "inherit", group: "governance" }],
  }),

  predicate("country", "国家或地区", "节点关联的主要国家或地区。", "string", "one", "attr", "organization_master", "stable", "B", "reviewed", 2, ["knowledge/entities/org-acme.md", "connectors/examples/erp-suppliers.mapping.yaml"]),
  predicate("finding", "研究发现", "有证据支持的分析或研究发现。", "text", "temporal_many", "assertion", "research_sources", "research_180d", "B", "reviewed", 3, ["control/domains/industry/pack.yaml", "knowledge/entities/org-acme.md", "knowledge/entities/market-industrial-automation/assertions.ndjson"], { temporal: "optional", evidence: "optional" }),
  predicate("lead_time_days", "交付周期", "以自然日计量的业务交付周期。", "quantity", "temporal_many", "assertion", "erp_operational", "operational_30d", "A", "auto", 2, ["control/domains/cost/pack.yaml", "connectors/examples/erp-suppliers.mapping.yaml"]),
  predicate("legal_name", "法定名称", "注册或权威来源中的组织名称。", "string", "one", "attr", "organization_master", "stable", "B", "reviewed", 2, ["knowledge/entities/org-acme.md", "connectors/examples/erp-suppliers.mapping.yaml"]),
  predicate("market_size", "市场规模", "限定地域、细分与币种的市场规模。", "quantity", "temporal_many", "assertion", "research_sources", "research_180d", "B", "reviewed", 2, ["control/domains/industry/pack.yaml", "knowledge/entities/market-industrial-automation/assertions.ndjson"]),
  predicate("risk_level", "风险等级", "在声明方法下评估的风险严重程度。", "enum", "temporal_many", "assertion", "project_governance", "operational_30d", "A", "reviewed", 3, ["control/domains/cost/pack.yaml", "control/domains/industry/pack.yaml", "knowledge/entities/project-atlas.md"], { history: "preserve" }),
  predicate("summary", "摘要", "人工维护的紧凑背景说明。", "text", "one", "attr", "git_authored", "stable", "C", "reviewed", 3, ["knowledge/entities/product-servo-module.md", "knowledge/entities/project-atlas.md", "knowledge/entities/task-atlas-m1.md"]),
  predicate("task_status", "任务状态", "任务在业务源中的当前状态。", "enum", "temporal_many", "assertion", "pm_operational", "operational_7d", "A", "auto", 2, ["control/domains/pm/pack.yaml", "knowledge/entities/task-atlas-m1.md"]),
  predicate("unit_cost", "单位成本", "按声明单位与币种计量的单位成本。", "quantity", "temporal_many", "assertion", "erp_operational", "operational_30d", "A", "reviewed", 2, ["control/domains/cost/pack.yaml", "knowledge/entities/product-servo-module.md"]),

  {
    id: "policy:authority",
    kind: "policy",
    label: "权威来源策略",
    description: "决定同一属性的来源优先级与冲突处理。",
    lifecycle: "active",
    sourcePath: "control/policies/authority.yaml",
    refs: 9,
    files: ["control/predicates/*.yaml"],
    config: { entries: 6, strategies: ["review_equal_authority", "prefer_primary", "preserve_all"] },
    readOnly: true,
  },
  {
    id: "policy:freshness",
    kind: "policy",
    label: "新鲜度策略",
    description: "定义稳定、7 天、30 天与 180 天的失效窗口。",
    lifecycle: "active",
    sourcePath: "control/policies/freshness.yaml",
    refs: 9,
    files: ["control/predicates/*.yaml"],
    config: { entries: 4, windows: { stable: null, operational_7d: 7, operational_30d: 30, research_180d: 180 } },
    readOnly: true,
  },
  {
    id: "policy:provenance",
    kind: "policy",
    label: "溯源等级策略",
    description: "定义 Tier A、B、C 的来源与证据要求。",
    lifecycle: "active",
    sourcePath: "control/policies/provenance.yaml",
    refs: 9,
    files: ["control/predicates/*.yaml"],
    config: { entries: 3, tiers: { A: "source + locator + evidence/snapshot", B: "source reference", C: "origin" } },
    readOnly: true,
  },
  {
    id: "policy:maintenance",
    kind: "policy",
    label: "维护与分层策略",
    description: "定义审核预算、P0–P3 优先级、冷热分层和 Assertion 外置阈值。",
    lifecycle: "active",
    sourcePath: "control/policies/maintenance.yaml",
    refs: 5,
    files: ["control/policies/maintenance.yaml"],
    config: {
      daily_review_items: 20,
      weekly_review_items: 100,
      priority_thresholds: { P0: 90, P1: 70, P2: 40, P3: 0 },
      warm_retention_days: 365,
      assertion_externalization: { inline_count: 50, frontmatter_bytes: 102400, historical_count: 30 },
    },
    readOnly: true,
  },
  {
    id: "model:cost_rollup",
    kind: "model",
    label: "单位成本汇总",
    description: "确定性的单位成本汇总模型，保留版本、输入单位、舍入与运行追踪。",
    lifecycle: "active",
    sourcePath: "control/models/cost_rollup.yaml",
    refs: 2,
    files: ["control/models/cost_rollup.yaml", "control/domains/cost/pack.yaml"],
    config: {
      model_id: "cost_rollup",
      version: "1.0.0",
      inputs: [
        { id: "material_cost", unit: "currency_per_unit" },
        { id: "labor_hours", unit: "hour_per_unit" },
        { id: "labor_rate", unit: "currency_per_hour" },
        { id: "overhead", unit: "currency_per_unit" },
      ],
      formula: "material_cost + labor_hours × labor_rate + overhead",
      precision: 2,
      rounding: "half_up",
      output_unit: "currency_per_unit",
    },
    readOnly: false,
  },
  {
    id: "model:schedule_variance",
    kind: "model",
    label: "计划偏差",
    description: "依据有证据的基线与预测完成日期，确定性计算日历日偏差。",
    lifecycle: "active",
    sourcePath: "control/models/schedule_variance.yaml",
    refs: 2,
    files: ["control/models/schedule_variance.yaml", "control/domains/pm/pack.yaml"],
    config: {
      model_id: "schedule_variance", version: "1.0.0",
      inputs: [{ id: "baseline_finish", type: "date" }, { id: "forecast_finish", type: "date" }],
      formula: "forecast_finish − baseline_finish", precision: 0, rounding: "half_up", output_unit: "calendar_days",
    },
    readOnly: false,
  },
  {
    id: "model:project_risk_score",
    kind: "model",
    label: "项目风险评分",
    description: "把进度偏差、阻塞依赖和高风险项转换为可追踪的风险分值与等级。",
    lifecycle: "active",
    sourcePath: "control/models/project_risk_score.yaml",
    refs: 2,
    files: ["control/models/project_risk_score.yaml", "control/domains/pm/pack.yaml"],
    config: {
      model_id: "project_risk_score", version: "1.0.0",
      inputs: [{ id: "schedule_delay_days" }, { id: "blocked_dependencies" }, { id: "high_risk_items" }],
      formula: "delay × 0.2 + blocked × 2 + high-risk × 3", precision: 1, rounding: "half_up", output_unit: "risk_points",
    },
    readOnly: false,
  },
  {
    id: "domain:cost",
    kind: "domain",
    label: "成本分析",
    description: "结构优先的成本分析工作流。",
    lifecycle: "active",
    sourcePath: "control/domains/cost/pack.yaml",
    refs: 6,
    files: ["control/domains/cost/pack.yaml"],
    config: {
      workflow: ["resolve_target", "traverse_components_materials_processes", "verify_authoritative_inputs", "apply_as_of", "execute_deterministic_model", "explain_trace_and_gaps"],
      retrieval: { depth: 2, relation_types: ["contains", "uses", "supplies"], predicate_priority: ["unit_cost", "lead_time_days", "risk_level"] },
      required_models: ["cost_rollup"],
      tool_allow: ["resolve", "get", "query", "neighbors", "history", "calculate", "explain"],
      writes: "changeset_only",
      retrieval_profile: { mode: "structured_first", allow_cold_by_default: false },
    },
    conceptScopes: ["product", "organization"],
    readOnly: true,
  },
  {
    id: "domain:industry",
    kind: "domain",
    label: "行业研究",
    description: "证据优先的行业研究工作流。",
    lifecycle: "active",
    sourcePath: "control/domains/industry/pack.yaml",
    refs: 6,
    files: ["control/domains/industry/pack.yaml"],
    config: {
      workflow: ["resolve_company_market_technology", "retrieve_claims_and_findings", "retrieve_supporting_and_contradicting_evidence", "apply_time_and_source_policy", "synthesize_with_citations"],
      retrieval: { depth: 1, relation_types: ["participates_in", "supplies", "uses"], predicate_priority: ["finding", "market_size", "risk_level"] },
      required_models: [],
      tool_allow: ["resolve", "get", "query", "neighbors", "history", "search", "explain", "propose"],
      writes: "changeset_only",
      retrieval_profile: { mode: "hybrid_research", allow_cold_by_default: false },
    },
    conceptScopes: ["organization", "market", "product"],
    readOnly: true,
  },
  {
    id: "domain:pm",
    kind: "domain",
    label: "项目管理",
    description: "时态图优先的项目治理工作流。",
    lifecycle: "active",
    sourcePath: "control/domains/pm/pack.yaml",
    refs: 5,
    files: ["control/domains/pm/pack.yaml"],
    config: {
      workflow: ["resolve_project_task_milestone", "traverse_dependencies_and_gates", "compare_baseline_current_as_of", "execute_schedule_and_risk_logic", "explain_status_and_actions"],
      retrieval: { depth: 3, relation_types: ["contains", "depends_on", "threatens"], predicate_priority: ["task_status", "risk_level", "finding"] },
      required_models: ["schedule_variance", "project_risk_score"],
      tool_allow: ["resolve", "get", "query", "neighbors", "history", "calculate", "explain", "propose"],
      writes: "changeset_only",
      retrieval_profile: { mode: "temporal_graph_first", allow_cold_by_default: false },
    },
    conceptScopes: ["project", "task", "decision"],
    readOnly: true,
  },
  {
    id: "connector:erp_suppliers",
    kind: "connector",
    label: "ERP 供应商映射",
    description: "把 ERP 供应商主数据确定性映射为组织属性与交付周期 Assertion。",
    lifecycle: "active",
    sourcePath: "connectors/examples/erp-suppliers.mapping.yaml",
    refs: 3,
    files: ["connectors/examples/erp-suppliers.mapping.yaml"],
    config: {
      source_system: "erp",
      source_object: "supplier_master",
      identity: { record_id_field: "supplier_id", version_field: "version", timestamp_field: "updated_at", authority_class: "erp" },
      node: { type: "organization", id_prefix: "org", key_field: "supplier_id", label_field: "legal_name" },
      attrs: { legal_name: "legal_name", country: "country" },
      assertions: { lead_time_days: { field: "lead_time_days", kind: "measurement" } },
    },
    readOnly: true,
  },
  {
    id: "schema:canonical_node",
    kind: "schema",
    label: "Canonical Node Envelope",
    description: "所有领域共享的 Node + attrs + assertions + relations + logic_refs 契约。",
    lifecycle: "active",
    sourcePath: "control/schemas/canonical-node.schema.json",
    refs: 7,
    files: ["control/schemas/canonical-node.schema.json", "docs/contracts.md"],
    config: {
      version: "3.0",
      required_root: ["base", "knowledge"],
      required_base: ["schema", "node", "lifecycle", "version"],
      required_node: ["id", "kind", "type", "key", "label"],
      required_knowledge: ["attrs", "assertions", "relations", "logic_refs"],
      external_assertions: true,
    },
    readOnly: false,
  },
];

const governedEdges = initialDefinitions
  .filter((item) => item.kind === "predicate")
  .flatMap((item) => [
    {
      id: `${item.id}-authority`,
      source: item.id,
      target: "policy:authority",
      relation: "governed_by" as const,
      editable: false,
    },
    {
      id: `${item.id}-freshness`,
      source: item.id,
      target: "policy:freshness",
      relation: "governed_by" as const,
      editable: false,
    },
    {
      id: `${item.id}-provenance`,
      source: item.id,
      target: "policy:provenance",
      relation: "governed_by" as const,
      editable: false,
    },
  ]);

const domainRefs: DependencyEdge[] = initialDefinitions
  .filter((item) => item.kind === "domain")
  .flatMap((domain) => (domain.conceptScopes ?? []).map((conceptId) => ({
    id: `domain-scope-${domain.id}-${conceptId}`,
    source: domain.id,
    target: conceptId,
    relation: "domain_scope" as const,
    editable: false,
  })));

const contractEdges: DependencyEdge[] = [
  { id: "cost-model", source: "domain:cost", target: "model:cost_rollup", relation: "requires_model", editable: false },
  { id: "pm-schedule-model", source: "domain:pm", target: "model:schedule_variance", relation: "requires_model", editable: false },
  { id: "pm-risk-model", source: "domain:pm", target: "model:project_risk_score", relation: "requires_model", editable: false },
  { id: "connector-schema", source: "connector:erp_suppliers", target: "schema:canonical_node", relation: "conforms_to", editable: false },
  { id: "connector-legal-name", source: "connector:erp_suppliers", target: "legal_name", relation: "maps_to", editable: false },
  { id: "connector-country", source: "connector:erp_suppliers", target: "country", relation: "maps_to", editable: false },
  { id: "connector-lead-time", source: "connector:erp_suppliers", target: "lead_time_days", relation: "maps_to", editable: false },
  { id: "schema-maintenance", source: "schema:canonical_node", target: "policy:maintenance", relation: "governed_by", editable: false },
];

export const initialEdges: DependencyEdge[] = [...governedEdges, ...domainRefs, ...contractEdges];

export const kindMeta: Record<DefinitionKind, { label: string; short: string; color: string }> = {
  concept: { label: "概念类型", short: "C", color: "#2563eb" },
  relation: { label: "关系类型", short: "R", color: "#d97706" },
  predicate: { label: "判断类型", short: "P", color: "#7c3aed" },
  policy: { label: "治理策略", short: "G", color: "#0891b2" },
  model: { label: "确定性模型", short: "L", color: "#dc2626" },
  business_constraint: { label: "业务约束", short: "BC", color: "#b91c1c" },
  business_rule: { label: "业务规则", short: "BR", color: "#0d9488" },
  unit: { label: "物理单位", short: "U", color: "#b45309" },
  currency: { label: "货币", short: "¥", color: "#0f766e" },
  domain: { label: "工作领域", short: "D", color: "#475569" },
  connector: { label: "连接器映射", short: "X", color: "#0f766e" },
  schema: { label: "核心契约", short: "S", color: "#111827" },
};

export const initialChangeSets: ChangeSet[] = [
  {
    id: "CS-0241",
    title: "统一风险等级写入策略",
    reason: "使项目治理与成本分析采用一致的人工审核门槛。",
    actor: "林知遥 · 本体管理员",
    targetSource: "control/predicates/risk_level.yaml",
    createdAt: "今天 09:42",
    risk: "normal",
    status: "review_required",
    operations: [
      {
        id: "op-cs241",
        type: "update",
        targetId: "risk_level",
        targetKind: "predicate",
        before: initialDefinitions.find((item) => item.id === "risk_level")!,
        after: {
          ...initialDefinitions.find((item) => item.id === "risk_level")!,
          config: {
            ...initialDefinitions.find((item) => item.id === "risk_level")!.config,
            write: "reviewed",
            history: "preserve",
          },
        },
      },
    ],
  },
  {
    id: "CS-0238",
    title: "新增 capability 概念类型",
    reason: "为产品能力地图预留稳定类型。",
    actor: "周惟 · 业务建模",
    targetSource: "control/ontology/core.yaml",
    createdAt: "昨天 16:18",
    risk: "low",
    status: "approved",
    operations: [],
  },
  {
    id: "CS-0234",
    title: "移除 supplies 关系",
    reason: "尝试合并重复关系。",
    actor: "周惟 · 业务建模",
    targetSource: "control/ontology/core.yaml",
    createdAt: "9 月 25 日",
    risk: "high",
    status: "rejected",
    reviewNote: "该关系仍被成本与行业工作领域引用，请改为弃用并指定替代关系。",
    operations: [],
  },
];

export const authorityOptions = [
  "organization_master",
  "git_authored",
  "erp_operational",
  "fx_market",
  "pm_operational",
  "project_governance",
  "research_sources",
];

export const freshnessOptions = ["stable", "operational_7d", "operational_30d", "research_180d"];
export const provenanceOptions = ["A", "B", "C"];
