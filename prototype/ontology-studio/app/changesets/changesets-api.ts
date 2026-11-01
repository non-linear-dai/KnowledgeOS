import { request, type UserSession } from "../knowledgeos-api";
import type { ExpertTemplate } from "../templates/template-api";
import type { ChangeSet } from "../studio-data";

export type ChangeRecord = {
  id: string; title: string; reason: string; actor: string; status: string; risk: string;
  target_source: string; created_at: string; source_revision?: string | null;
  review_note?: string | null; reviewed_by?: string | null; reviewed_at?: string | null;
  published_by?: string | null; published_at?: string | null; publication?: unknown;
  operations: Record<string, unknown>[];
  patch: { op?: string; template?: ExpertTemplate; [key: string]: unknown };
};
export const changeStatusLabels: Record<string, string> = { proposed: "已提交", review_required: "待审核", approved: "已批准", changes_requested: "需修改", rejected: "已驳回", published: "已发布" };
export function isTemplateChange(change: ChangeRecord) {
  return change.target_source.split(",").some(path => /^(knowledge\/(templates|operations|decisions)\/)/.test(path.trim()));
}
export function isExtractionChange(change: ChangeRecord) {
  return change.patch.op === "extraction_asset" || change.target_source.split(",").every(path => path.trim().startsWith("control/extraction/"));
}
export function changeCategory(change: ChangeRecord) {
  if (isExtractionChange(change)) return "抽取配置";
  if (change.patch.op === "logic_asset" || change.target_source.split(",").every(path => /^control\/(models|rules\/business|constraints\/business|decision-models)\//.test(path.trim()))) return "业务逻辑";
  return isTemplateChange(change) ? "专家模板" : change.target_source.split(",").every(path => path.trim().startsWith("control/")) ? "本体与定义" : "知识变更";
}
export function reviewAccess(change: ChangeRecord, session: UserSession | null) {
  const permissions = session?.permissions ?? [];
  const pending = ["proposed", "review_required"].includes(change.status);
  const reviewer = permissions.includes(isTemplateChange(change) ? "template_review" : "review");
  return {
    canReview: pending && reviewer,
    canApprove: pending && reviewer && !(change.risk === "high" && change.actor === session?.principal),
    canPublish: change.status === "approved" && permissions.includes(isTemplateChange(change) ? "template_publish" : "publish"),
  };
}
export function supportsSourceApplication(change: ChangeRecord) {
  if (change.patch.op === "logic_asset") return true;
  if (change.patch.op === "extraction_asset") return /^control\/extraction\/v2\/(profile|cards\/[a-z][a-z0-9_]*|recipes\/[a-z][a-z0-9_]*)\.yaml$/.test(change.target_source.trim());
  if (isTemplateChange(change)) return change.patch.op === "template_package";
  return ["studio_batch", "semantic_control"].includes(change.patch.op ?? "") && change.target_source.split(",").every(path => /^control\/(ontology|predicates|models|units|currencies|vocabularies|constraints\/business|rules\/business)\/[^,]+\.yaml$/.test(path.trim()));
}
export async function loadChangeRecords(signal?: AbortSignal) {
  return (await request<{ data: ChangeRecord[] }>("/v1/changesets?limit=500", { signal })).data;
}
export function batchReviewRecords(changes: ChangeRecord[]): ChangeSet[] {
  // The backend batch endpoint supports ordinary changes only; templates keep
  // their separate permission policy inside this same workspace.
  return changes.filter(change => !isTemplateChange(change)).map(change => ({
    id: change.id, title: change.title, reason: change.reason, actor: change.actor,
    targetSource: change.target_source, createdAt: change.created_at,
    risk: change.risk === "high" || change.risk === "low" ? change.risk : "normal",
    status: change.status as ChangeSet["status"], operations: [],
  }));
}
