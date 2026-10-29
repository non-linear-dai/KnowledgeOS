import { ApiError, request, sessionAuthorization } from "../knowledgeos-api";

export type AuthoringTask = "logic.draft" | "ontology.model" | "extraction.card" | "template.interview";
export type IssueSeverity = "error" | "warning" | "info";
export type AuthoringIssue = { severity: IssueSeverity; code: string; message: string; path?: string; regions?: Record<string, string>[]; quotes?: string[] };
export type AuthoringCheck = {
  valid: boolean; error_count: number; warning_count: number; issues: AuthoringIssue[];
  analysis: Record<string, unknown>; draft_hash: string | null; submitted: unknown; registry_fingerprint: string;
};
/** Author's declaration recorded with the proposal; KnowledgeOS adds whether the draft was changed after the suggestion. */
export type Assistance = {
  suggestion_id: string; task: AuthoringTask; provider: string; model: string; renderer_version: string;
  instructions_hash: string; context_hash: string; output_hash: string; created_at: string;
  items_total: number; items_accepted?: number; repair_rounds: number;
};
export type Suggestion<O = Record<string, unknown>> = {
  suggestion_id: string; task: AuthoringTask; created_at: string; demo: boolean; output: O; check: AuthoringCheck;
  assistance: Assistance; usage: { input_tokens: number; output_tokens: number }; attempts: { attempt: number; errors: number }[];
  context: { request_id: string; registry_fingerprint: string; instructions_hash: string };
  ontology_request?: Record<string, unknown>;
};
export type AssistantStatus = { configured: boolean; provider: string | null; model: string | null; demo: boolean; tasks: string[]; send_document_samples: boolean };
export type TaskFeedback = {
  task: AuthoringTask; label: string; submitted: number; statuses: Record<string, number>; modified: number;
  items_total: number; items_accepted: number; approval_rate: number | null; modification_rate: number | null; item_acceptance_rate: number | null; models: Record<string, number>;
};
export type AuthoringFeedback = { tasks: TaskFeedback[]; recent: { id: string; title: string; status: string; task: AuthoringTask; modified_after_suggestion: boolean; model: string; created_at: string }[] };

export type LogicDraftOutput = {
  kind: "decision_table" | "business_rule" | "business_constraint"; definition: Record<string, unknown>; summary: string;
  assumptions?: string[]; open_questions?: string[]; test_cases: IntentCase[]; evidence?: { quote: string; supports?: string }[];
};
export type IntentCase = { id: string; label: string; input: Record<string, unknown>; expected: Record<string, unknown>; rationale?: string };
export type IntentResult = IntentCase & { actual?: unknown; passed: boolean; error?: string };
export type BoundaryCase = { id: string; label: string; rule: string; input: Record<string, unknown>; actual?: Record<string, unknown>; error?: string; matched_rules?: string[]; used_default?: boolean };
export type Coverage = { exhaustive: boolean; regions_checked: number; regions_total: number; gaps: Record<string, string>[]; gap_count: number; has_default: boolean; unreachable_rules: string[]; boundary_cases: BoundaryCase[] };
export type RegressionCase = { id: string; label?: string; input: Record<string, unknown>; expected: Record<string, unknown> };
export type InterviewTurn = { role: "expert" | "assistant"; content: string };
export type InterviewOutput = { summary: string; reply: string; questions: { id: string; question: string; why?: string; options?: string[] }[]; template: Record<string, unknown> | null; changes?: string[]; open_questions?: string[] };
export type CardOutput = { summary: string; card: Record<string, unknown>; changes: { suggestion_id?: string; field: string; description: string }[]; open_questions?: string[] };
export type OntologyOutput = { summary: string; model_output: Record<string, unknown>; notes?: { target_id: string; explanation: string; reused?: string[] }[]; open_questions?: string[] };

const ASSISTANT_ROOT = "/api/assistant";

const codeMessages: Record<string, string> = {
  ASSISTANT_GATEWAY_NOT_CONFIGURED: "助手网关尚未配置。仍可手工编制，并使用下方的确定性检查。",
  ASSISTANT_NOT_CONFIGURED: "助手网关已启动，但尚未配置模型服务。",
  ASSISTANT_UNREACHABLE: "无法连接助手网关，请确认它已启动。",
  RATE_LIMITED: "请求过于频繁，请稍后再试。",
};

async function assistantRequest<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${ASSISTANT_ROOT}${path}`, {
    ...init, cache: "no-store", signal: init?.signal ?? AbortSignal.timeout(300_000),
    headers: { Accept: "application/json", ...sessionAuthorization(), ...(init?.body ? { "Content-Type": "application/json" } : {}), ...init?.headers },
  });
  const payload = await response.json().catch(() => ({})) as Record<string, unknown>;
  if (!response.ok) {
    const code = typeof payload.code === "string" ? payload.code : undefined;
    throw new ApiError((code && codeMessages[code]) || (typeof payload.error === "string" ? payload.error : `助手请求失败（${response.status}）`), response.status, code);
  }
  return payload as T;
}

export async function loadAssistantStatus(signal?: AbortSignal) {
  return (await assistantRequest<{ data: AssistantStatus }>("/v1/assistant/status", { signal })).data;
}
export async function requestSuggestion<O>(task: AuthoringTask, input: Record<string, unknown>, signal?: AbortSignal) {
  return (await assistantRequest<{ data: Suggestion<O> }>("/v1/assistant/suggest", { method: "POST", body: JSON.stringify({ task, input }), signal })).data;
}
/** Deterministic KnowledgeOS dry run; works without the assistant gateway. */
export async function checkDraft(task: AuthoringTask, draft: Record<string, unknown>, input?: Record<string, unknown>, signal?: AbortSignal) {
  return (await request<{ data: AuthoringCheck }>("/v1/authoring/check", { method: "POST", body: JSON.stringify({ task, draft, ...(input ? { input } : {}) }), signal })).data;
}
export async function loadAuthoringFeedback(signal?: AbortSignal) {
  return (await request<{ data: AuthoringFeedback }>("/v1/authoring/feedback", { signal })).data;
}

// ---------------------------------------------------------------------------
// Pure helpers (unit tested)

export const severityLabels: Record<IssueSeverity, string> = { error: "必须修正", warning: "需要确认", info: "提示" };
export const taskLabels: Record<AuthoringTask, string> = { "logic.draft": "业务逻辑", "ontology.model": "本体建模", "extraction.card": "抽取说明卡", "template.interview": "工艺模板访谈" };

export function sortIssues(issues: AuthoringIssue[]): AuthoringIssue[] {
  const order: Record<IssueSeverity, number> = { error: 0, warning: 1, info: 2 };
  return [...issues].sort((a, b) => order[a.severity] - order[b.severity]);
}

export function withAccepted(assistance: Assistance, accepted: number): Assistance {
  return { ...assistance, items_accepted: Math.max(0, Math.min(Math.round(accepted), assistance.items_total)) };
}

export function percent(value: number | null | undefined) {
  return value == null ? "—" : `${Math.round(value * 1000) / 10}%`;
}

function uniqueId(base: string, used: Set<string>) {
  const stem = (/^[a-z][a-z0-9_-]{0,55}$/.test(base) ? base : `case_${base.replace(/[^a-z0-9_-]/gi, "").toLowerCase()}`).slice(0, 56) || "case";
  let candidate = stem, index = 2;
  while (used.has(candidate)) candidate = `${stem}_${index++}`;
  used.add(candidate);
  return candidate;
}

/**
 * Build the regression cases an expert accepted: intent cases whose expectation the
 * engine already confirms, plus boundary cases whose observed output the expert confirmed.
 */
export function acceptedCases(existing: RegressionCase[], intent: IntentResult[], acceptedIntent: string[], boundary: BoundaryCase[], confirmedBoundary: string[]): RegressionCase[] {
  const used = new Set(existing.map(item => item.id));
  const result = [...existing];
  for (const item of intent) {
    if (!item.passed || !acceptedIntent.includes(item.id)) continue;
    result.push({ id: uniqueId(item.id, used), label: item.label, input: structuredClone(item.input), expected: structuredClone(item.expected) });
  }
  for (const item of boundary) {
    if (!item.actual || !confirmedBoundary.includes(item.id)) continue;
    result.push({ id: uniqueId(item.id, used), label: `专家确认：${item.label}`, input: structuredClone(item.input), expected: structuredClone(item.actual) });
  }
  return result;
}

export function adoptLogic(suggestion: Suggestion<LogicDraftOutput>, acceptedIntent: string[], confirmedBoundary: string[]) {
  const submitted = suggestion.check.submitted as Record<string, unknown> | null;
  if (!submitted) throw new Error("草稿未通过结构校验，不能采纳");
  const analysis = suggestion.check.analysis;
  const intent = (analysis.intent_tests ?? []) as IntentResult[];
  const coverage = analysis.coverage as Coverage | undefined;
  const existing = (Array.isArray(submitted.test_cases) ? submitted.test_cases : []) as RegressionCase[];
  const cases = acceptedCases(existing, intent, acceptedIntent, coverage?.boundary_cases ?? [], confirmedBoundary);
  // Items the assistant proposed: its rules/checks and its intent cases. Boundary cases are the expert's own confirmations.
  const accepted = intent.filter(item => item.passed && acceptedIntent.includes(item.id)).length;
  const definition: Record<string, unknown> = { ...structuredClone(submitted), test_cases: cases };
  const rules = Array.isArray(definition.rules) ? definition.rules.length : Array.isArray(definition.checks) ? definition.checks.length : 0;
  return { kind: suggestion.output.kind, definition, assistance: withAccepted(suggestion.assistance, rules + accepted) };
}

export function regionText(region: Record<string, string>, labels: Record<string, string> = {}) {
  return Object.entries(region).map(([key, value]) => `${labels[key] ?? key} ${value === "任意" ? "为任意值" : value}`).join("，");
}

const templateDefaults = { summary: "", groups: [], steps: [], edges: [], operations: [], cases: [] };
/** Fill collections the model may omit so the route editor can open the draft. */
export function normalizeTemplate<T extends Record<string, unknown>>(raw: T) {
  const value = structuredClone(raw) as Record<string, unknown>;
  for (const [key, fallback] of Object.entries(templateDefaults)) if (value[key] == null) value[key] = structuredClone(fallback);
  for (const step of (value.steps as Record<string, unknown>[])) if (step.parent === undefined) step.parent = null;
  for (const group of (value.groups as Record<string, unknown>[])) group.join_policy ??= "all";
  return value as T & typeof templateDefaults;
}

export function nextTurns(turns: InterviewTurn[], reply: InterviewOutput): InterviewTurn[] {
  const questions = reply.questions.map((item, index) => `${index + 1}. ${item.question}`).join("\n");
  return [...turns, { role: "assistant", content: [reply.reply, questions].filter(Boolean).join("\n").slice(0, 4000) }];
}

export function resolvedCount(check: AuthoringCheck) {
  const resolves = check.analysis.resolves;
  return { resolved: Array.isArray(resolves) ? resolves.length : 0, open: typeof check.analysis.open_before === "number" ? check.analysis.open_before : 0 };
}
