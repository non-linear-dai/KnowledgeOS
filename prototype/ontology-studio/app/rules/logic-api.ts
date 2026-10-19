import { request } from "../knowledgeos-api";

export type LogicKind = "business_rule" | "business_constraint" | "model" | "decision_table" | "decision_model";
export type LogicDocument = Record<string, unknown> & { id: string; version: string; label?: string; description?: string; test_cases?: LogicCase[] };
export type LogicCase = { id: string; label?: string; input: Record<string, unknown>; expected: Record<string, unknown> };
export type LogicUse = { kind: string; id: string; label: string; href: string };
export type LogicAsset = { ref: string; kind: LogicKind; id: string; label: string; version: string; lifecycle: string; source_path: string; definition: LogicDocument; bindings: string[]; used_by: LogicUse[] };
export type LogicCatalog = { assets: LogicAsset[]; registry_fingerprint: string; capabilities: Record<string, unknown> };
export type TestReport = { passed: boolean; cases: { id: string; passed: boolean; error?: string; expected: unknown; actual?: unknown }[] };
export type LogicPreview = { definition: LogicDocument; result: Record<string, unknown> | null; tests: TestReport; source_refs: unknown; definition_hash: string; input_hash: string | null };
export type EntityBindings = { subject_id: string; candidate_ids?: string[]; context_ids: Record<string, string> };
export type LogicRun = { id: string; asset_ref: string; actor: string; recorded_at: string; record: { kind: LogicKind; mode: string; definition: LogicDocument; definition_hash: string; input: Record<string, unknown>; input_hash: string; result: Record<string, unknown>; source_refs: unknown; source_path: string; entity_bindings?: EntityBindings } };
export const logicKindLabels: Record<LogicKind, string> = { business_rule: "匹配规则", business_constraint: "业务约束", model: "计算模型", decision_table: "决策表", decision_model: "决策模型" };
export const logicStatusLabels: Record<string, string> = { draft: "草稿", active: "已启用", deprecated: "已弃用", retired: "已退役", complete: "已完成", incomplete: "信息不足", blocked: "约束未通过" };
export const object = (value: unknown): Record<string, unknown> => value && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : {};
export const records = (value: unknown): Record<string, unknown>[] => Array.isArray(value) ? value.map(object) : [];
export const pretty = (value: unknown) => JSON.stringify(value, null, 2);
export const logicHref = (params: Record<string, string>) => `/rules?${new URLSearchParams(params)}`;

export async function loadLogic(signal?: AbortSignal) { return (await request<{ data: LogicCatalog }>("/v1/logic", { signal })).data; }
export async function previewLogic(kind: LogicKind, definition: LogicDocument, facts?: Record<string, unknown>, entity_bindings?: EntityBindings) {
  return (await request<{ data: LogicPreview }>("/v1/logic/preview", { method: "POST", body: JSON.stringify({ kind, definition, facts, entity_bindings }) })).data;
}
export async function proposeLogic(kind: LogicKind, definition: LogicDocument, reason: string, base_revision: string, idempotency_key: string) {
  return (await request<{ data: { id: string } }>("/v1/logic/propose", { method: "POST", body: JSON.stringify({ kind, definition, reason, base_revision, idempotency_key }) })).data;
}
export async function runLogic(ref: string, idempotency_key: string, facts?: Record<string, unknown>, entity_bindings?: EntityBindings) {
  return (await request<{ data: LogicRun }>("/v1/logic/run", { method: "POST", body: JSON.stringify({ ref, facts, entity_bindings, idempotency_key }) })).data;
}
export async function loadLogicRuns(ref?: string, subject_id?: string, signal?: AbortSignal) {
  return (await request<{ data: LogicRun[] }>(`/v1/logic/runs?${new URLSearchParams({ ...(ref ? { ref } : {}), ...(subject_id ? { subject_id } : {}) })}`, { signal })).data;
}
export async function replayLogic(id: string) { return (await request<{ data: { matches_recorded: boolean; result: unknown } }>("/v1/logic/replay", { method: "POST", body: JSON.stringify({ id }) })).data; }

export function bumpVersion(version: string): string { const parts = version.split("."); return parts.length === 3 ? `${parts[0]}.${parts[1]}.${Number(parts[2]) + 1}` : "1.0.0"; }
export function filterLogic(assets: LogicAsset[], query: string, kind: string, lifecycle: string, concept: string) {
  const needle = query.trim().toLowerCase();
  return assets.filter(asset => (!kind || asset.kind === kind) && (!lifecycle || asset.lifecycle === lifecycle) && (!concept || asset.bindings.includes(concept)) && `${asset.label} ${asset.ref} ${asset.definition.description ?? ""}`.toLowerCase().includes(needle));
}

export function graphDependencies(definition: LogicDocument) {
  const nodes = records(definition.nodes);
  return nodes.flatMap(node => Object.values(object(node.bindings)).map(object).filter(binding => binding.source === "node").map(binding => ({ source: String(binding.node), target: String(node.id) }))).filter((edge, index, all) => all.findIndex(other => other.source === edge.source && other.target === edge.target) === index);
}

export function sampleInput(kind: LogicKind, definition: LogicDocument, definitions: { id: string; config: Record<string, unknown> }[]): Record<string, unknown> {
  if (definition.test_cases?.[0]) return structuredClone(definition.test_cases[0].input);
  if (kind === "model") return { inputs: Object.fromEntries(records(definition.inputs).map(input => [String(input.id), input.type === "date" ? "2026-01-01T00:00:00Z" : "1"])) };
  if (kind === "decision_table") { const result: Record<string, Record<string, unknown>> = { facts: {}, item: {} }; for (const input of records(definition.inputs)) result[String(input.source ?? "facts")][String(input.field)] = input.type === "boolean" ? true : input.type === "number" ? "1" : "example"; return result; }
  if (kind === "decision_model") return {};
  const subject: Record<string, unknown> = {}, candidate: Record<string, unknown> = { id: "candidate_demo" }, contexts: Record<string, Record<string, unknown>> = {};
  for (const input of records(definition.inputs)) {
    const predicate = definitions.find(item => item.id === input.predicate)?.config ?? {};
    const value = predicate.value_type === "quantity" ? { literal: "10", unit: predicate.default_unit ?? (predicate.units as string[])?.[0] ?? "" } : predicate.value_type === "boolean" ? true : ["number", "decimal"].includes(String(predicate.value_type)) ? "10" : (predicate.allowed as string[])?.[0] ?? "example";
    const target = input.role === "subject" ? subject : input.role === "candidate" ? candidate : (contexts[String(input.role)] ??= {});
    target[String(input.predicate)] = value;
  }
  return { subject, ...(kind === "business_rule" ? { candidates: [candidate], contexts } : {}) };
}

export function newLogic(kind: LogicKind, id: string, assets: LogicAsset[], concepts: string[], predicates: string[]): LogicDocument {
  const common = { id, version: "1.0.0", label: `新${logicKindLabels[kind]}`, description: "请填写业务目的与适用范围", status: { lifecycle: "active" }, test_cases: [] };
  const concept = concepts.includes("equipment") ? "equipment" : concepts[0] ?? "";
  const predicate = predicates.includes("rated_power") ? "rated_power" : predicates[0] ?? "";
  if (kind === "decision_table") return { ...common, hit_policy: "UNIQUE", inputs: [{ id: "size", source: "facts", field: "size", type: "number" }], outputs: [{ id: "equipment_class", type: "string" }], rules: [{ id: "small", when: { size: { op: "lte", value: "300" } }, then: { equipment_class: "small" } }, { id: "large", when: { size: { op: "gt", value: "300" } }, then: { equipment_class: "large" } }], test_cases: [{ id: "small_part", input: { facts: { size: "100" } }, expected: { result: { equipment_class: "small" } } }, { id: "large_part", input: { facts: { size: "600" } }, expected: { result: { equipment_class: "large" } } }] };
  if (kind === "model") return { ...common, inputs: [{ id: "quantity", unit: "count" }], output_unit: "count", formula: { op: "input", id: "quantity" }, precision: 2, test_cases: [{ id: "basic", input: { inputs: { quantity: "2" } }, expected: { output: { value: "2.0", unit: "count" } } }] };
  if (kind === "decision_model") {
    const asset = assets.find(asset => asset.kind === "decision_table" && asset.lifecycle === "active");
    return { ...common, format: "knowledgeos.decision-model.v1", nodes: asset ? [{ id: "select", kind: asset.kind, ref: asset.ref, bindings: { facts: { source: "facts", path: ["facts"] } } }] : [], outputs: asset ? { selection: { source: "node", node: "select", path: ["result"] } } : {} };
  }
  return { ...common, format: kind === "business_rule" ? "knowledgeos.business-rule.v1" : "knowledgeos.business-constraint.v1", scope: { subject_concept: concept, ...(kind === "business_rule" ? { candidate_concept: concept, context_concepts: {} } : {}) }, inputs: [{ id: "required", role: "subject", predicate }, ...(kind === "business_rule" ? [{ id: "available", role: "candidate", predicate }] : [])], checks: [{ left: kind === "business_rule" ? "available" : "required", operator: "gte", right: { input: "required" }, message: "候选能力应达到主体要求；请按实际业务调整" }] };
}
