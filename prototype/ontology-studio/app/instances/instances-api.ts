import { request } from "../knowledgeos-api";

export type InstanceKind = "concept" | "relation" | "predicate" | "model";
export type InstanceSummary = { key: string; id: string; kind: InstanceKind; record_kind: string; type: string; type_label: string; label: string; lifecycle: string; source_class: string; source_path: string | null; compiled_at: string; subject_id?: string; target_id?: string; value?: unknown };
export type InstanceSelection = Pick<InstanceSummary, "key" | "id" | "kind">;
export type InstanceFilters = { query: string; domain: string; kind: InstanceKind | ""; type: string; lifecycle: string; source_class: string; sort: string; limit: number; offset: number };
export type InstancePage = { items: InstanceSummary[]; total: number; overall_total: number; limit: number; offset: number; domain: string | null; domains: { id: string; label: string }[];
  facets: { kind: { value: InstanceKind; count: number }[]; type: { value: string; count: number; kind: InstanceKind; label: string }[]; lifecycle: { value: string; count: number }[]; source_class: { value: string; count: number }[] } };
export type InstanceCard = { id: string; type: string; label: string; attrs: Record<string, unknown>; source_class: string; card_updated_at: string; knowledge_gaps: string[]; current_assertions: { id: string }[] };
export type InstanceAssertion = { id: string; predicate: string; value: unknown; temperature: string;
  temporal: { observed_at?: string; valid_from?: string; valid_to?: string };
  epistemic: { status: string; assertion_kind: string; confidence?: number };
  provenance: { tier: string; source_refs: unknown[]; evidence_refs: unknown[] } };
export type InstanceNeighbors = { nodes: { id: string; label: string; type: string }[]; edges: { src: string; dst: string; predicate: string; rel_id?: string }[] };
export type InstanceDetail = { card: InstanceCard; assertions: InstanceAssertion[]; neighbors: InstanceNeighbors };
export type RecordedInstanceDetail = { summary: InstanceSummary; data: Record<string, unknown>; source_refs: unknown[]; evidence_refs: unknown[];
  references: { key: string; id: string; label: string; type: string }[] };

export const defaultInstanceFilters: InstanceFilters = { query: "", domain: "", kind: "", type: "", lifecycle: "", source_class: "", sort: "label", limit: 25, offset: 0 };

export const instanceKindLabels: Record<InstanceKind, string> = { concept: "概念实例", relation: "关系实例", predicate: "判断实例", model: "模型运行" };
export const instanceStatusLabels: Record<string, string> = { active: "已启用", draft: "草稿", deprecated: "已弃用", retired: "已退役", merged: "已合并", confirmed: "已确认", proposed: "待确认", rejected: "已驳回", superseded: "已替代", retracted: "已撤回", disputed: "有争议", current: "有效结果", stale: "待重算", scheduled: "尚未生效", expired: "已失效" };
export const instanceSourceLabels: Record<string, string> = { git_authored: "Git 真源", enterprise: "企业连接器", enterprise_operational: "企业连接器", enterprise_system: "企业系统", expert: "专家维护", agent: "Agent 提案", model_derived: "模型推导" };

export async function loadRecordedInstance(key: string, signal?: AbortSignal) {
  return (await request<{ data: RecordedInstanceDetail }>(`/v1/instances/get?${new URLSearchParams({ key })}`, { signal })).data;
}

export function instanceQuery(filters: InstanceFilters) {
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(filters)) if (value !== "") params.set(key, String(value));
  return params.toString();
}

export async function loadInstances(filters: InstanceFilters, signal?: AbortSignal) {
  return (await request<{ data: InstancePage }>(`/v1/instances?${instanceQuery(filters)}`, { signal })).data;
}

export async function loadInstanceDetail(id: string, signal?: AbortSignal): Promise<InstanceDetail> {
  const params = new URLSearchParams({ id });
  const [card, history, neighbors] = await Promise.all([
    request<{ data: InstanceCard }>(`/v1/get?${params}`, { signal }),
    request<{ data: { assertions: InstanceAssertion[] } }>(`/v1/history?${params}`, { signal }),
    request<{ data: InstanceNeighbors }>(`/v1/neighbors?${params}`, { signal }),
  ]);
  return { card: card.data, assertions: history.data.assertions, neighbors: neighbors.data };
}

export function displayValue(value: unknown): string {
  if (value === null || value === undefined) return "—";
  if (typeof value !== "object") return String(value);
  if (Array.isArray(value)) return value.map(displayValue).join("、");
  const record = value as Record<string, unknown>;
  if ("literal" in record) return `${displayValue(record.literal)}${record.unit ? ` ${record.unit}` : ""}`;
  if ("ref" in record && Object.keys(record).length === 1) return String(record.ref);
  return JSON.stringify(value, null, 2);
}

export function displayDate(value?: string | null) {
  if (!value) return "—";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : new Intl.DateTimeFormat("zh-CN", { dateStyle: "medium", timeZone: "Asia/Shanghai" }).format(date);
}
