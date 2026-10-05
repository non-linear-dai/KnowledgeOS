import { request } from "../knowledgeos-api";

export type Comparison = { source: "facts" | "item"; field: string; op: "eq" | "ne" | "gt" | "gte" | "lt" | "lte"; value: string | number | boolean };
export type Condition = Comparison | { all: Condition[] } | { any: Condition[] };
export type DecisionBinding = { ref: string; output: string; purpose: "enabled" | "operation"; equals?: string | number | boolean };
export type DecisionColumn = { id: string; label?: string; type: "string" | "number" | "boolean"; source?: "facts" | "item"; field?: string; unit?: string };
export type DecisionCell = { op: "eq" | "ne" | "gt" | "gte" | "lt" | "lte" | "in" | "between"; value: string | number | boolean | (string | number | boolean)[] } | null;
export type DecisionTable = { id: string; version: string; label: string; hit_policy: "UNIQUE" | "FIRST" | "COLLECT"; inputs: DecisionColumn[]; outputs: DecisionColumn[];
  rules: { id: string; when: Record<string, DecisionCell>; then: Record<string, string | number | boolean> }[]; default_output?: Record<string, string | number | boolean> };
export type RouteGroup = { id: string; label: string; iteration_set: string; join_policy: "all"; when?: Condition; group_mode?: "repeat" | "conditional"; execution_mode?: "parallel" | "sequential"; decision_binding?: DecisionBinding };
export type RouteStep = { id: string; label: string; cost_basis: string; operation_ref: string; parent: string | null; when?: Condition; decision_binding?: DecisionBinding };
export type ResourceRequirement = { kind: "material" | "part" | "equipment" | "energy" | "labor" | "facility"; ref?: string; amount?: string; unit?: string; quantity_model_ref?: string; quantity_inputs?:Record<string,{source:'facts'|'item'|'process';field:string}>; basis?: "cycle" | "piece" | "time" | "batch" };
export type Processing = { duration?: {value:string;unit:string}; setup?: {value:string;unit:string}; batch_size?: string; time_inputs?: Record<string, {source:"facts"|"item";field:string}> };
export type OperationTemplate = { id: string; version: string; label: string; cost_basis: string; summary?: string; method?: string; time_model_ref?: string; resources?: ResourceRequirement[]; processing?: Processing };
export type SampleCase = { id: string; facts: Record<string, string | number | boolean | {literal:string;unit:string}>; sets: Record<string, Record<string, string | number | boolean>[]>; expected_operations: number };
export type ExpertTemplate = {
  id: string; version: string; label: string; family: string; output_basis: string; summary: string;
  groups: RouteGroup[]; steps: RouteStep[]; edges: { from: string; to: string }[];
  operations: OperationTemplate[]; cases: SampleCase[]; author?: string; decisions?: DecisionTable[];
};
export type TemplateSummary = { id: string; key: string; label: string; version: string; family: string; output_basis: string; author: string };
export type OperationSummary = { id: string; label: string; cost_basis: string; method?: string; resources?:ResourceRequirement[]; processing?:Processing; time_model_ref?:string };
export type TimeModel = {id:string;version:string;description:string;inputs?:{id:string;unit:string}[];output_unit?:string};
export type DecisionTrace = {decision_ref:string;route_key:string;item_id?:string;hit_policy:string;matched_rules:string[];used_default:boolean;result:Record<string,unknown>|Record<string,unknown>[]};
export type Preview = {
  template_id: string; case_id: string; operation_count: number; matches_expected: boolean;
  operation_counts: Record<string, number>;
  instances: { id: string; step_key: string; label: string; operation_ref: string; cost_basis: string; group_id?: string; item_id: string | null }[];
  edges: { from: string; to: string }[];
  decisions?: DecisionTrace[]; skipped?: {route_key:string;item_id?:string;label:string}[];
};
export type CostScenario = {quantity:string;currency:string;margin:string;operating_ratio:string;rates:Record<string,{value?:string;unit?:string;currency?:string;source?:{node_id:string;predicate:string;as_of?:string}}>};
export type RouteCost = {status:"complete"|"incomplete";currency:string;quantity:string;route:Preview;known_cost:string;total_cost:string|null;unit_cost:string|null;material_cost:string|null;manufacturing_cost:string|null;reasonable_unit_price:string|null;
  model_traces:{instance_id:string;resource_ref?:string;model_ref:string;output:{value:string;unit:string};trace:unknown[]}[]; missing:{instance_id:string;field:string;message:string}[]; rows:{id:string;label:string;cycles:string;duration_seconds:string|null;known_cost:string;resources:{ref:string;kind:string;quantity:string;unit:string;rate:string;cost:string;provenance:{kind:string;source_backed:boolean;assertion_id?:string}}[]}[]};
export type TemplateChangeSet = { id: string; title: string; actor: string; status: string; risk: string; target_source: string; source_revision?: string; created_at: string; reason: string; patch?: { op: string; template?: ExpertTemplate } };

export async function loadTemplateCatalog() {
  return (await request<{ data: { templates: TemplateSummary[]; operations: OperationSummary[]; decisions:DecisionTable[]; currencies:string[];units: string[]; models: TimeModel[];quantity_models:TimeModel[]; registry_fingerprint: string } }>("/v1/templates")).data;
}
export async function calculateTemplate(template:ExpertTemplate,sample:SampleCase,scenario:CostScenario){
  return (await request<{data:RouteCost}>("/v1/templates/calculate",{method:"POST",body:JSON.stringify({template,case:sample,scenario})})).data;
}
export async function loadTemplate(id: string) {
  return (await request<{ data: ExpertTemplate }>(`/v1/templates/get?id=${encodeURIComponent(id)}`)).data;
}
export async function previewTemplate(template: ExpertTemplate, sample: SampleCase) {
  return (await request<{ data: Preview }>("/v1/templates/preview", {
    method: "POST", body: JSON.stringify({ template, case: sample }),
  })).data;
}
export async function submitTemplate(template: ExpertTemplate, reason: string, idempotencyKey: string) {
  return (await request<{ data: { id: string; status: string } }>("/v1/templates/propose", {
    method: "POST", body: JSON.stringify({ template, reason, idempotency_key: idempotencyKey }),
  })).data;
}
export async function loadTemplateChangeSets() {
  const data = (await request<{ data: TemplateChangeSet[] }>("/v1/changesets?limit=500")).data;
  return data.filter((item) => item.target_source.split(",").some((path) => path.startsWith("knowledge/templates/")));
}
