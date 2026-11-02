import type { ChangeRecord } from "./changesets-api";

/** The review workspace's queue views. "mine" is the default for reviewers. */
export const reviewViews = [
  { id: "mine", label: "待我处理" },
  { id: "others", label: "需他人批准" },
  { id: "submitted", label: "我提交的" },
  { id: "all", label: "全部" },
] as const;
export type ReviewView = typeof reviewViews[number]["id"];

const riskOrder: Record<string, number> = { high: 0, normal: 1, low: 2 };
export const riskLabels: Record<string, string> = { high: "高风险", normal: "普通", low: "低风险" };

export function isPending(change: Pick<ChangeRecord, "status">) {
  return change.status === "proposed" || change.status === "review_required";
}

export function parseReviewView(value: string | null | undefined): ReviewView | null {
  return reviewViews.some(view => view.id === value) ? value as ReviewView : null;
}

/** A proposal belongs to "submitted" when the viewer is its author or the person accountable for the agent that wrote it. */
export function submittedBy(change: Pick<ChangeRecord, "actor" | "actor_info">, principal: string | undefined) {
  if (!principal) return false;
  return change.actor === principal || change.actor_info?.accountable === principal;
}

/** Records for one view, in the order a reviewer should work through them. */
export function queueFor(view: ReviewView, records: ChangeRecord[], principal: string | undefined) {
  const byPriority = (a: ChangeRecord, b: ChangeRecord) =>
    (riskOrder[a.risk] ?? 3) - (riskOrder[b.risk] ?? 3) || a.created_at.localeCompare(b.created_at);
  const newestFirst = (a: ChangeRecord, b: ChangeRecord) => b.created_at.localeCompare(a.created_at);
  if (view === "mine") return records.filter(change => isPending(change) && change.governance?.allowed).sort(byPriority);
  if (view === "others") return records.filter(change => isPending(change) && change.governance && !change.governance.allowed).sort(byPriority);
  if (view === "submitted") return records.filter(change => submittedBy(change, principal)).sort(newestFirst);
  return [...records].sort(newestFirst);
}

export function viewCounts(records: ChangeRecord[], principal: string | undefined) {
  return Object.fromEntries(reviewViews.map(view => [view.id, queueFor(view.id, records, principal).length])) as Record<ReviewView, number>;
}

/** The item to open after deciding on `id`: the next one in the same list, else the previous, else none. */
export function nextAfter(id: string, list: Pick<ChangeRecord, "id">[]) {
  const index = list.findIndex(item => item.id === id);
  if (index < 0) return list[0]?.id ?? "";
  return list[index + 1]?.id ?? list[index - 1]?.id ?? "";
}
