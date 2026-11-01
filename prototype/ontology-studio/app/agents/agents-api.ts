import { ApiError, request, type ApprovalPolicy } from "../knowledgeos-api";
import type { McpInfo } from "./host-config";

export type Credential = {
  id: string; kind: "agent" | "personal" | "session"; label: string; scopes: string[] | null; created_by: string; created_at: string;
  expires_at: string | null; last_used_at: string | null; last_client: string | null; revoked_at: string | null;
};
export type Participant = {
  id: string; kind: "human" | "agent"; label: string; owner: string | null; roles: string[]; permissions: string[]; status: "active" | "disabled";
  host: string | null; model: string | null; description: string; created_at: string; credentials: Credential[];
  changesets: Record<string, number>; last_used_at: string | null;
};
export type Directory = { participants: Participant[]; policy: ApprovalPolicy; agent_roles: string[] };
export type IssuedCredential = { id: string; token: string; participant_id: string; label: string; expires_at: string | null };

export const rolePresets = [
  { id: "research", label: "研究与提案", roles: ["agent"], description: "读取知识、提交文档抽取、本体与规则提案" },
  { id: "authoring", label: "研究与提案 + 决策表/模板起草", roles: ["agent", "template_author"], description: "另可提交决策表与专家模板" },
  { id: "reader", label: "只读", roles: ["reader"], description: "只能读取知识与事件" },
] as const;

export function presetFor(roles: string[]) {
  return rolePresets.find(preset => preset.roles.length === roles.length && preset.roles.every(role => roles.includes(role)));
}

export async function loadDirectory(signal?: AbortSignal) {
  return (await request<{ data: Directory }>("/v1/participants", { signal })).data;
}
export async function createAgent(body: { id: string; label: string; roles: string[]; host?: string; model?: string; description?: string }) {
  return (await request<{ data: Participant }>("/v1/participants/agents", { method: "POST", body: JSON.stringify(body) })).data;
}
export async function updateParticipant(id: string, changes: Partial<Pick<Participant, "label" | "roles" | "status" | "host" | "model" | "description">>) {
  return (await request<{ data: Participant }>("/v1/participants/update", { method: "POST", body: JSON.stringify({ id, changes }) })).data;
}
export async function issueCredential(participantId: string, label: string) {
  return (await request<{ data: IssuedCredential }>("/v1/participants/credentials", { method: "POST", body: JSON.stringify({ participant_id: participantId, label }) })).data;
}
export async function revokeCredential(id: string) {
  await request("/v1/participants/credentials/revoke", { method: "POST", body: JSON.stringify({ id }) });
}
export async function updatePolicy(changes: Partial<ApprovalPolicy>) {
  return (await request<{ data: ApprovalPolicy }>("/v1/participants/policy", { method: "POST", body: JSON.stringify({ changes }) })).data;
}
export async function loadMcpInfo(signal?: AbortSignal): Promise<McpInfo> {
  const response = await fetch("/api/mcp-info", { cache: "no-store", signal });
  const payload = await response.json().catch(() => ({})) as { data?: McpInfo; error?: string; code?: string };
  if (!response.ok || !payload.data) throw new ApiError(payload.error ?? "无法读取 MCP 服务信息", response.status, payload.code);
  return payload.data;
}

/** A stable identifier suggestion from an ASCII display name; Chinese names need one typed in. */
export function slugFromLabel(label: string) {
  const ascii = label.trim().toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "");
  return /^[a-z]/.test(ascii) ? ascii.slice(0, 40) : "";
}

export function activeCredentials(participant: Participant) {
  return participant.credentials.filter(item => !item.revoked_at);
}

export function proposalSummary(counts: Record<string, number>) {
  const pending = (counts.proposed ?? 0) + (counts.review_required ?? 0);
  const total = Object.values(counts).reduce((sum, value) => sum + value, 0);
  return { total, pending, approved: (counts.approved ?? 0) + (counts.published ?? 0), rejected: (counts.rejected ?? 0) + (counts.changes_requested ?? 0) };
}
