"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { Bot, Inbox, RefreshCw, ShieldAlert, UserRound } from "lucide-react";
import { Button } from "@/components/ui/button";
import { request, type ApprovalPolicy } from "../knowledgeos-api";
import { useWorkspaceSession } from "../workspace-session";
import { NavigationToggle } from "../workspace-navigation-context";
import { displayDate } from "../instances/instances-api";
import { actorText, assistanceOf, changeCategory, changeStatusLabels, type ChangeRecord } from "../changesets/changesets-api";

type InboxItem = ChangeRecord & Required<Pick<ChangeRecord, "governance" | "actor_info">>;
type InboxData = { items: InboxItem[]; counts: { total: number; can_approve: number; from_my_agents_or_me: number }; policy: ApprovalPolicy };

const riskLabels: Record<string, string> = { high: "高风险", normal: "普通", low: "低风险" };

/** Split the reviewer's queue: what they may decide now, and what needs another accountable party. */
export function groupInbox(items: InboxItem[]) {
  return { mine: items.filter(item => item.governance.allowed), others: items.filter(item => !item.governance.allowed) };
}

function Row({ item }: { item: InboxItem }) {
  const Icon = item.actor_info.kind === "agent" ? Bot : UserRound;
  return <li><Link href={`/changesets?id=${encodeURIComponent(item.id)}`} className="inbox-row">
    <span className="inbox-risk" data-risk={item.risk}>{riskLabels[item.risk] ?? item.risk}</span>
    <span className="inbox-main"><strong>{item.title || item.reason}</strong>
      <small><Icon aria-hidden="true" size={13} />{actorText(item)} · {changeCategory(item)}{assistanceOf(item) ? " · 助手起草" : ""} · {displayDate(item.created_at)}</small>
      {item.governance.self_review && <small className="inbox-note">可按个人模式自审</small>}
      {!item.governance.allowed && item.governance.reason && <small className="inbox-note">{item.governance.reason.replace(/（.*）$/, "")}</small>}</span>
    <span className="neutral-badge">{changeStatusLabels[item.status] ?? item.status}</span>
  </Link></li>;
}

export default function InboxWorkspace({ active }: { active: boolean }) {
  const { session } = useWorkspaceSession();
  const [data, setData] = useState<InboxData | null>(null);
  const [loading, setLoading] = useState(false), [error, setError] = useState(""), [revision, setRevision] = useState(0);
  const canReview = Boolean(session?.permissions.includes("review"));
  useEffect(() => {
    if (!active || !session || !canReview) return;
    const controller = new AbortController();
    // Reset feedback for this abortable request.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setLoading(true); setError("");
    void request<{ data: InboxData }>("/v1/inbox", { signal: controller.signal }).then(result => { if (!controller.signal.aborted) setData(result.data); })
      .catch(cause => { if (!controller.signal.aborted) setError(cause instanceof Error ? cause.message : "无法加载待审列表"); })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [active, session, canReview, revision]);
  if (!session || !canReview) return <div className="inbox-workspace"><h1 className="sr-only">我的待审</h1><div className="workspace-navigation-access"><NavigationToggle /></div>
    <div className="change-empty"><Inbox size={28} strokeWidth={1.3} /><h2>我的待审</h2><p>{session ? "当前账号没有审核权限。" : "从左下角“账户与连接”登录后，在这里按风险处理 Agent 与成员提交的提案。"}</p></div></div>;
  const groups = groupInbox(data?.items ?? []);
  return <div className="inbox-workspace">
    <header className="agents-header"><div className="directory-heading"><NavigationToggle /><h1>我的待审</h1>
      <Button variant="ghost" size="icon-sm" aria-label="刷新" title="刷新" disabled={loading} onClick={() => setRevision(value => value + 1)}><RefreshCw className={loading ? "animate-spin" : ""} /></Button></div>
      <p role="status">{loading && !data ? "正在同步…" : `${data?.counts.total ?? 0} 项待审 · 你可处理 ${data?.counts.can_approve ?? 0} 项 · 来自你或你名下 Agent ${data?.counts.from_my_agents_or_me ?? 0} 项`}</p></header>
    {error && <div role="alert" className="instance-error"><p>{error}</p></div>}
    <section className="inbox-section" aria-label="可以由我批准"><h2>可以由我批准 <small>高风险在前；打开后在变更审核中查看差异并决定</small></h2>
      {groups.mine.length ? <ul>{groups.mine.map(item => <Row key={item.id} item={item} />)}</ul> : <p className="assistant-caption">{loading ? "正在加载…" : "没有需要你批准的提案。"}</p>}</section>
    {groups.others.length > 0 && <section className="inbox-section" aria-label="需要另一独立责任方批准"><h2><ShieldAlert aria-hidden="true" size={16} />需要另一独立责任方批准 <small>你或你名下 Agent 提交的高风险变更；可驳回或要求修改，批准需另一位成员{data?.policy.mode === "personal" ? "，或在「Agent 管理」开启个人模式自审" : ""}</small></h2>
      <ul>{groups.others.map(item => <Row key={item.id} item={item} />)}</ul></section>}
  </div>;
}
