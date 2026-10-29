"use client";

import { useEffect, useRef, useState } from "react";
import { Check, Sparkles } from "lucide-react";
import { Button } from "@/components/ui/button";
import { AssistantSheet, AssistantStatusNotice, Busy, IssueList, SuggestionSummary, useAssistantStatus } from "../assistant/assistant-panel";
import { requestSuggestion, resolvedCount, withAccepted, type Assistance, type CardOutput, type Suggestion } from "../assistant/assistant-api";

export type CardAssistTarget = { concept: string; label: string; suggestionIds: string[] };

export function CardAssistant({ target, onClose, canPropose, onAdopt }: { target: CardAssistTarget | null; onClose: () => void; canPropose: boolean; onAdopt: (concept: string, card: Record<string, unknown>, assistance: Assistance) => void }) {
  const open = Boolean(target);
  const { status, error: statusError, ready } = useAssistantStatus(open);
  const [busy, setBusy] = useState(false), [error, setError] = useState("");
  const [suggestion, setSuggestion] = useState<Suggestion<CardOutput> | null>(null);
  const controller = useRef<AbortController | null>(null);
  // Each target starts a fresh drafting session.
  // eslint-disable-next-line react-hooks/set-state-in-effect
  useEffect(() => { setSuggestion(null); setError(""); }, [target?.concept, target?.suggestionIds.join(",")]);
  useEffect(() => () => controller.current?.abort(), []);
  async function generate() {
    if (!target) return;
    controller.current?.abort();
    const next = new AbortController(); controller.current = next;
    setBusy(true); setError(""); setSuggestion(null);
    try {
      const result = await requestSuggestion<CardOutput>("extraction.card", { concept: target.concept, ...(target.suggestionIds.length ? { suggestion_ids: target.suggestionIds } : {}) }, next.signal);
      if (!next.signal.aborted) setSuggestion(result);
    } catch (cause) { if (!next.signal.aborted) setError(cause instanceof Error ? cause.message : "起草失败"); }
    finally { if (!next.signal.aborted) setBusy(false); }
  }
  const counts = suggestion ? resolvedCount(suggestion.check) : null;
  const resolves = (suggestion?.check.analysis.resolves ?? []) as { id: string; text: string; kind: string }[];
  return <AssistantSheet open={open} onOpenChange={value => { if (!value) { controller.current?.abort(); onClose(); } }} title={`助手起草说明卡 · ${target?.label ?? ""}`}
    description="根据已提案抽取的反馈，起草说明卡修改；KnowledgeOS 试算规范化并计算能解决哪些建议。">
    <AssistantStatusNotice status={status} error={statusError} sends={`该概念的属性定义、当前说明卡与待处理建议${status?.send_document_samples === false ? "（不含原文样例）" : "，以及建议附带的原文样例片段"}`} />
    <p className="assistant-caption">{target?.suggestionIds.length ? `处理所选的 ${target.suggestionIds.length} 条建议。` : "处理该概念全部待处理建议（最多 40 条）。"}</p>
    <div className="flex flex-wrap gap-2"><Button disabled={busy || !ready || !canPropose} onClick={() => void generate()}><Sparkles />{busy ? "起草中…" : "生成说明卡草稿"}</Button>{busy && <Button variant="ghost" onClick={() => { controller.current?.abort(); setBusy(false); }}>取消</Button>}</div>
    {busy && <Busy label="助手正在起草，KnowledgeOS 会试算每条写法…" />}
    {error && <p role="alert" className="assistant-notice" data-tone="error">{error}</p>}
    {suggestion && <>
      <SuggestionSummary suggestion={suggestion} summary={suggestion.output.summary} questions={suggestion.output.open_questions} />
      <section className="assistant-section"><h3>修改内容 <small>{suggestion.output.changes.length} 项</small></h3><ul className="assistant-list">{suggestion.output.changes.map((item, index) => <li key={index}><code>{item.field}</code> {item.description}</li>)}</ul></section>
      <section className="assistant-section"><h3>确定性检查</h3><IssueList issues={suggestion.check.issues} />
        {counts && counts.open > 0 && <div className="assistant-callout"><strong>此草稿可解决 {counts.resolved} / {counts.open} 条待处理建议</strong>{resolves.length > 0 && <ul>{resolves.map(item => <li key={item.id}>「{item.text}」</li>)}</ul>}<p>只有当前配置在试算中确实能规范化的写法才计为已解决。</p></div>}</section>
      <details className="change-json"><summary>完整说明卡草稿</summary><pre>{JSON.stringify(suggestion.output.card, null, 2)}</pre></details>
      <div className="assistant-adopt"><p>采纳后在说明卡编辑器中核对，再检查并提交审核。</p>
        <Button disabled={!suggestion.check.submitted || !target} onClick={() => { if (!target) return; onAdopt(target.concept, suggestion.check.submitted as Record<string, unknown>, withAccepted(suggestion.assistance, suggestion.output.changes.length)); onClose(); }}><Check />采纳到说明卡草稿</Button></div>
    </>}
  </AssistantSheet>;
}
