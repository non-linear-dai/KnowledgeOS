"use client";

import { useEffect, useState, type ReactNode } from "react";
import { CircleAlert, Info, Loader2, ShieldCheck, Sparkles, TriangleAlert } from "lucide-react";
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from "@/components/ui/sheet";
import { loadAssistantStatus, severityLabels, sortIssues, type AssistantStatus, type AuthoringIssue, type Suggestion } from "./assistant-api";

export function AssistantSheet({ open, onOpenChange, title, description, children }: { open: boolean; onOpenChange: (open: boolean) => void; title: string; description: string; children: ReactNode }) {
  return <Sheet open={open} onOpenChange={onOpenChange}>
    <SheetContent side="right" className="assistant-sheet w-full gap-0 overflow-y-auto sm:max-w-xl" aria-describedby={undefined}>
      <SheetHeader className="assistant-sheet-head"><SheetTitle className="flex items-center gap-2"><Sparkles className="size-4" aria-hidden="true" />{title}</SheetTitle><SheetDescription>{description}</SheetDescription></SheetHeader>
      <div className="assistant-sheet-body">{children}</div>
    </SheetContent>
  </Sheet>;
}

/** Gateway availability; failures leave the deterministic checks usable. */
export function useAssistantStatus(open: boolean) {
  const [status, setStatus] = useState<AssistantStatus | null>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    if (!open) return;
    const controller = new AbortController();
    void loadAssistantStatus(controller.signal).then(value => { if (!controller.signal.aborted) { setStatus(value); setError(""); } })
      .catch(cause => { if (!controller.signal.aborted) { setStatus(null); setError(cause instanceof Error ? cause.message : "无法读取助手状态"); } });
    return () => controller.abort();
  }, [open]);
  return { status, error, ready: Boolean(status?.configured) };
}

export function AssistantStatusNotice({ status, error, sends }: { status: AssistantStatus | null; error: string; sends: string }) {
  if (error) return <p className="assistant-notice" data-tone="warning" role="status"><CircleAlert size={14} aria-hidden="true" />{error}</p>;
  if (!status) return <p className="assistant-notice" role="status"><Loader2 size={14} className="animate-spin" aria-hidden="true" />正在连接助手…</p>;
  if (!status.configured) return <p className="assistant-notice" data-tone="warning" role="status"><CircleAlert size={14} aria-hidden="true" />助手网关尚未配置模型服务。仍可手工编制，并使用确定性检查。</p>;
  return <div className="assistant-notice" data-tone={status.demo ? "warning" : "neutral"} role="note">
    <ShieldCheck size={14} aria-hidden="true" />
    <span>{status.demo ? "演示模式：返回固定样例，不调用模型，仅用于体验流程。" : `由 ${status.model ?? status.provider} 起草。`}助手只起草；采纳后仍需检查、确认案例并提交独立审核。<br /><small>将发送给模型：{sends}</small></span>
  </div>;
}

export function IssueList({ issues, empty = "确定性检查未发现问题。" }: { issues: AuthoringIssue[]; empty?: string }) {
  if (!issues.length) return <p className="assistant-ok" role="status"><ShieldCheck size={14} aria-hidden="true" />{empty}</p>;
  return <ul className="assistant-issues" aria-label="检查结果">{sortIssues(issues).map((item, index) => {
    const Icon = item.severity === "error" ? CircleAlert : item.severity === "warning" ? TriangleAlert : Info;
    return <li key={`${item.code}:${index}`} data-severity={item.severity}><Icon size={14} aria-hidden="true" /><span><strong>{severityLabels[item.severity]}</strong>{item.message}</span></li>;
  })}</ul>;
}

export function SuggestionSummary({ suggestion, summary, assumptions, questions }: { suggestion: Suggestion<unknown>; summary: string; assumptions?: string[]; questions?: string[] }) {
  return <section className="assistant-summary" aria-label="助手草稿说明">
    <div className="flex flex-wrap items-center gap-2"><span className="neutral-badge">{suggestion.demo ? "演示样例" : "助手草稿"}</span><small>{suggestion.assistance.model}{suggestion.assistance.repair_rounds ? ` · 自动修正 ${suggestion.assistance.repair_rounds} 轮` : ""}</small></div>
    <p>{summary}</p>
    {assumptions && assumptions.length > 0 && <><h4>假设</h4><ul>{assumptions.map(item => <li key={item}>{item}</li>)}</ul></>}
    {questions && questions.length > 0 && <><h4>需要您确认</h4><ul>{questions.map(item => <li key={item}>{item}</li>)}</ul></>}
  </section>;
}

export function Busy({ label }: { label: string }) {
  return <p className="assistant-notice" role="status"><Loader2 size={14} className="animate-spin" aria-hidden="true" />{label}</p>;
}
