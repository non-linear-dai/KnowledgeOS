import {
  type ChangeOperation,
  type ChangeOperationType,
  type ChangeSet,
  type ChangeSetStatus,
  type ConfigValue,
  type DefinitionKind,
  type Lifecycle,
  type OntologyDefinition,
} from "./studio-data";
import { wireSnapshot, connectionFailure } from "./studio-contract";

let sessionToken = "";
export function setSessionToken(token: string) { sessionToken = token.trim(); }
export class ApiError extends Error {
  constructor(message: string, public status: number, public code?: string) { super(message); }
  get connectionState() { return connectionFailure(this.status, this.code); }
}
export type UserSession = { principal: string; roles: string[]; permissions: string[] };
export function loadSession() { return request<UserSession>("/v1/session"); }

const API_ROOT = "/api/knowledgeos";
const definitionKinds: DefinitionKind[] = ["schema", "domain", "concept", "relation", "predicate", "model", "unit", "currency", "policy", "connector", "business_constraint", "business_rule"];
const lifecycles: Lifecycle[] = ["draft", "active", "deprecated", "merged", "retired"];
const changeStatuses: ChangeSetStatus[] = ["proposed", "review_required", "approved", "rejected", "changes_requested", "published"];
const operationTypes: ChangeOperationType[] = ["create", "update", "deprecate", "delete"];

type JsonRecord = Record<string, unknown>;

export type ApiConnectionState = "connecting" | "connected" | "demo" | "unauthorized" | "forbidden" | "offline" | "unconfigured";

export interface StudioMetadata {
  contractVersion: string;
  registryFingerprint: string;
  coverage: Partial<Record<DefinitionKind, number>>;
  extensions: { constraints: number; rules: number; skills: number };
  durableWrites: string;
}

export interface StudioSnapshot {
  definitions: OntologyDefinition[];
  changeSets: ChangeSet[];
  metadata: StudioMetadata;
}

function record(value: unknown): JsonRecord {
  return value && typeof value === "object" && !Array.isArray(value) ? value as JsonRecord : {};
}

function array(value: unknown): unknown[] {
  return Array.isArray(value) ? value : [];
}

function string(value: unknown, fallback = ""): string {
  return typeof value === "string" ? value : fallback;
}

function number(value: unknown, fallback = 0): number {
  return typeof value === "number" && Number.isFinite(value) ? value : fallback;
}

function config(value: unknown): Record<string, ConfigValue> {
  return record(value) as Record<string, ConfigValue>;
}

function formatTimestamp(value: unknown): string {
  const raw = string(value);
  if (!raw) return "时间未知";
  const date = new Date(raw);
  if (Number.isNaN(date.getTime())) return raw;
  return new Intl.DateTimeFormat("zh-CN", { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" }).format(date);
}

function mapDefinition(value: unknown): OntologyDefinition | null {
  const item = record(value);
  const id = string(item.id);
  const kind = string(item.kind) as DefinitionKind;
  if (!id || !definitionKinds.includes(kind)) return null;
  const lifecycleValue = string(item.lifecycle, "active") as Lifecycle;
  const bindings = array(item.bindings).map((entry) => {
    const binding = record(entry);
    return {
      predicateId: string(binding.predicate_id ?? binding.predicateId),
      required: Boolean(binding.required),
      cardinality: string(binding.cardinality, "inherit") as "inherit" | "one" | "many" | "temporal_many",
      group: string(binding.group, "other") as "identity" | "profile" | "measurement" | "governance" | "other",
    };
  }).filter((binding) => binding.predicateId);
  const endpoints = array(item.endpoints).map((entry) => {
    const endpoint = record(entry);
    return {
      sourceConceptId: string(endpoint.source_concept_id ?? endpoint.sourceConceptId),
      targetConceptId: string(endpoint.target_concept_id ?? endpoint.targetConceptId),
      sourceCardinality: string(endpoint.source_cardinality ?? endpoint.sourceCardinality, "many") as "one" | "many",
      targetCardinality: string(endpoint.target_cardinality ?? endpoint.targetCardinality, "many") as "one" | "many",
    };
  }).filter((endpoint) => endpoint.sourceConceptId && endpoint.targetConceptId);
  const rawReification = record(item.reification);
  const reification = Object.keys(rawReification).length ? {
    nodeType: string(rawReification.node_type ?? rawReification.nodeType),
    identity: string(rawReification.identity, "optional") as "optional" | "required",
    properties: array(rawReification.properties).map((entry) => {
      const binding = record(entry);
      return {
        predicateId: string(binding.predicate_id ?? binding.predicateId),
        required: Boolean(binding.required),
        cardinality: string(binding.cardinality, "inherit") as "inherit" | "one" | "many" | "temporal_many",
        group: string(binding.group, "other") as "identity" | "profile" | "measurement" | "governance" | "other",
      };
    }).filter((binding) => binding.predicateId),
  } : undefined;

  return {
    id,
    kind,
    label: string(item.label, id),
    description: string(item.description),
    lifecycle: lifecycles.includes(lifecycleValue) ? lifecycleValue : "active",
    sourcePath: string(item.source_path ?? item.sourcePath),
    refs: number(item.refs),
    files: array(item.files).filter((entry): entry is string => typeof entry === "string"),
    config: config(item.config),
    bindings: kind === "concept" ? bindings : undefined,
    endpoints: kind === "relation" ? endpoints : undefined,
    relationMode: kind === "relation" ? string(item.relation_mode ?? item.relationMode, "simple") as "simple" | "reifiable" | "reified" : undefined,
    reification: kind === "relation" ? reification : undefined,
    conceptScopes: kind === "domain" ? array(item.concept_scopes).filter((entry): entry is string => typeof entry === "string") : undefined,
    readOnly: Boolean(item.read_only ?? item.readOnly),
  };
}

function mapOperation(value: unknown, definitions: OntologyDefinition[], index: number): ChangeOperation | null {
  const item = record(value);
  const targetId = string(item.targetId ?? item.target_id ?? item.target);
  if (!targetId) return null;
  const before = item.before ? mapDefinition(item.before) : null;
  const after = item.after ? mapDefinition(item.after) : null;
  const inferredKind = after?.kind ?? before?.kind ?? definitions.find((definition) => definition.id === targetId)?.kind ?? "concept";
  const typeValue = string(item.type, "update") as ChangeOperationType;
  return {
    id: string(item.id, `operation-${index}-${targetId}`),
    type: operationTypes.includes(typeValue) ? typeValue : "update",
    targetId,
    targetKind: definitionKinds.includes(string(item.targetKind ?? item.target_kind) as DefinitionKind) ? string(item.targetKind ?? item.target_kind) as DefinitionKind : inferredKind,
    before,
    after,
  };
}

function mapChangeSet(value: unknown, definitions: OntologyDefinition[]): ChangeSet | null {
  const item = record(value);
  const id = string(item.id);
  if (!id) return null;
  const status = string(item.status, "review_required") as ChangeSetStatus;
  const risk = string(item.risk, "normal");
  return {
    id,
    title: string(item.title, `控制面变更 ${id.slice(0, 8)}`),
    reason: string(item.reason),
    actor: string(item.actor, "unknown"),
    targetSource: string(item.target_source ?? item.targetSource),
    createdAt: formatTimestamp(item.created_at ?? item.createdAt),
    risk: risk === "low" || risk === "high" ? risk : "normal",
    status: changeStatuses.includes(status) ? status : "review_required",
    operations: array(item.operations).map((operation, index) => mapOperation(operation, definitions, index)).filter((operation): operation is ChangeOperation => Boolean(operation)),
    reviewNote: string(item.review_note ?? item.reviewNote) || undefined,
    reviewedBy: string(item.reviewed_by ?? item.reviewedBy) || undefined,
    reviewedAt: item.reviewed_at || item.reviewedAt ? formatTimestamp(item.reviewed_at ?? item.reviewedAt) : undefined,
    sourceRevision: string(item.source_revision ?? item.sourceRevision) || undefined,
  };
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_ROOT}${path}`, {
    ...init,
    cache: "no-store",
    signal: init?.signal ?? AbortSignal.timeout(30_000),
    headers: { Accept: "application/json", ...(sessionToken ? { Authorization: `Bearer ${sessionToken}` } : {}), ...(init?.body ? { "Content-Type": "application/json" } : {}), ...init?.headers },
  });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) throw new ApiError(string(record(payload).error, `KnowledgeOS API 请求失败（${response.status}）`), response.status, string(record(payload).code));
  return payload as T;
}

export async function loadStudioSnapshot(): Promise<StudioSnapshot> {
  const envelope = wireSnapshot.parse(await request<unknown>("/v1/studio"));
  const data = record(envelope.data);
  const definitions = array(data.definitions).map(mapDefinition).filter((definition): definition is OntologyDefinition => Boolean(definition));
  if (!definitions.length) throw new Error("KnowledgeOS API 未返回控制面定义");
  const coverage = record(data.coverage);
  const extensions = record(data.extensions);
  const capabilities = record(data.capabilities);
  return {
    definitions,
    changeSets: array(data.changesets).map((item) => mapChangeSet(item, definitions)).filter((item): item is ChangeSet => Boolean(item)),
    metadata: {
      contractVersion: string(data.contract_version, "unknown"),
      registryFingerprint: string(data.registry_fingerprint),
      coverage: Object.fromEntries(definitionKinds.map((kind) => [kind, number(coverage[kind])])) as Partial<Record<DefinitionKind, number>>,
      extensions: { constraints: number(extensions.constraints), rules: number(extensions.rules), skills: number(extensions.skills) },
      durableWrites: string(capabilities.durable_writes, "changeset_only"),
    },
  };
}

export async function proposeChangeSet(input: { title: string; reason: string; risk: "low" | "normal" | "high"; targetSource: string; operations: ChangeOperation[]; baseRevision?: string; idempotencyKey?: string }) {
  return request("/v1/propose", {
    method: "POST",
    body: JSON.stringify({
      title: input.title,
      reason: input.reason,
      risk: input.risk,
      target_source: input.targetSource,
      patch: { op: "studio_batch", operation_count: input.operations.length },
      operations: input.operations,
      base_revision: input.baseRevision,
      idempotency_key: input.idempotencyKey,
    }),
  });
}

export async function previewModel(model: Record<string, unknown>, inputs: Record<string, unknown>) {
  return request<{ data: { output: { value: string; unit: string }; trace: unknown[]; persisted: boolean } }>("/v1/models/preview", {
    method: "POST", body: JSON.stringify({ model, inputs }),
  });
}

export async function previewBusiness(kind: "business_constraint" | "business_rule", definition: Record<string, unknown>, facts: Record<string, unknown>) {
  return request<{ data: { results: { verdict: string; candidate_id: string | null; checks: { passed: boolean | null; message: string; left_value: string | null; right_value: string | null }[] }[]; eligible_candidate_ids: string[] } }>("/v1/business/preview", {
    method: "POST", body: JSON.stringify({ kind, definition, facts }),
  });
}

export async function impactBusiness(kind: "business_constraint" | "business_rule", definition: Record<string, unknown>, subjectId: string, candidateIds: string[] | null) {
  return request<{ data: { results: { verdict: string; candidate_id: string | null; entity_id?: string; checks: { passed: boolean | null; message: string; left_value: string | null; right_value: string | null }[] }[]; eligible_candidate_ids?: string[]; verdict_counts: Record<string, number>; source_refs: unknown } }>("/v1/business/impact", {
    method: "POST", body: JSON.stringify({ kind, definition, subject_id: subjectId || null, candidate_ids: candidateIds }),
  });
}

export async function evaluateBusiness(kind: "business_constraint" | "business_rule", id: string, subjectId: string, candidateIds: string[] | null) {
  return request<{ data: { results: { verdict: string; candidate_id: string | null }[]; eligible_candidate_ids: string[]; source_refs: unknown } }>("/v1/business/evaluate", {
    method: "POST", body: JSON.stringify({ kind, id, subject_id: subjectId, candidate_ids: candidateIds }),
  });
}

export type ModelRevision = { id: string; model_id: string; version: string; definition: Record<string, ConfigValue>; recorded_at: string; source_path: string };
export async function loadModelHistory(modelId: string): Promise<ModelRevision[]> {
  const envelope = await request<{ data: ModelRevision[] }>(`/v1/models/history?id=${encodeURIComponent(modelId)}`);
  return envelope.data;
}

export async function reviewChangeSet(id: string, decision: "approved" | "rejected" | "changes_requested", note: string) {
  return request("/v1/changesets/review", {
    method: "POST",
    body: JSON.stringify({ id, decision, note }),
  });
}

export async function publishChangeSet(id: string, sourceRevision: string) {
  return request("/v1/changesets/publish", {
    method: "POST",
    body: JSON.stringify({ id, source_revision: sourceRevision }),
  });
}

export async function applyChangeSet(id: string): Promise<string> {
  const envelope = await request<{ data: { source_revision: string } }>("/v1/changesets/apply", {
    method: "POST", body: JSON.stringify({ id }),
  });
  return envelope.data.source_revision;
}
