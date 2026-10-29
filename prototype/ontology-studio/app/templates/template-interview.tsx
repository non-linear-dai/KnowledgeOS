"use client";

import { useEffect, useRef, useState } from "react";
import { Check, RotateCcw, Send } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { AssistantSheet, AssistantStatusNotice, Busy, IssueList, useAssistantStatus } from "../assistant/assistant-panel";
import { nextTurns, normalizeTemplate, requestSuggestion, withAccepted, type Assistance, type InterviewOutput, type InterviewTurn, type Suggestion } from "../assistant/assistant-api";
import type { ExpertTemplate } from "./template-api";

export function TemplateInterview({ open, onOpenChange, current, editable, onApply }: {
  open: boolean; onOpenChange: (open: boolean) => void; current: ExpertTemplate; editable: boolean;
  onApply: (template: ExpertTemplate, assistance: Assistance) => void;
}) {
  const { status, error: statusError, ready } = useAssistantStatus(open);
  const [turns, setTurns] = useState<InterviewTurn[]>([]);
  const [text, setText] = useState("");
  const [continueCurrent, setContinueCurrent] = useState(current.steps.length > 0);
  const [busy, setBusy] = useState(false), [error, setError] = useState("");
  const [latest, setLatest] = useState<Suggestion<InterviewOutput> | null>(null);
  const controller = useRef<AbortController | null>(null);
  const transcript = useRef<HTMLOListElement>(null);
  useEffect(() => () => controller.current?.abort(), []);
  // Opening a fresh interview defaults to continuing whatever is on the board.
  // eslint-disable-next-line react-hooks/set-state-in-effect
  useEffect(() => { if (open && !turns.length) setContinueCurrent(current.steps.length > 0); }, [open]);
  useEffect(() => { transcript.current?.lastElementChild?.scrollIntoView({ block: "nearest" }); }, [turns.length]);

  async function send() {
    const message = text.trim();
    if (!message) return;
    const conversation: InterviewTurn[] = [...turns, { role: "expert", content: message }];
    controller.current?.abort();
    const next = new AbortController(); controller.current = next;
    setTurns(conversation); setText(""); setBusy(true); setError("");
    try {
      const result = await requestSuggestion<InterviewOutput>("template.interview", { conversation, ...(continueCurrent ? { current } : {}) }, next.signal);
      if (next.signal.aborted) return;
      setLatest(result); setTurns(nextTurns(conversation, result.output));
    } catch (cause) {
      if (!next.signal.aborted) { setError(cause instanceof Error ? cause.message : "访谈请求失败"); setTurns(turns); setText(message); }
    } finally { if (!next.signal.aborted) setBusy(false); }
  }
  function reset() { controller.current?.abort(); setTurns([]); setLatest(null); setError(""); setBusy(false); }
  function apply() {
    if (!latest?.output.template) return;
    const draft = normalizeTemplate((latest.check.submitted ?? latest.output.template) as Record<string, unknown>) as unknown as ExpertTemplate;
    onApply(draft, withAccepted(latest.assistance, (latest.output.changes ?? []).length));
  }
  const output = latest?.output;
  return <AssistantSheet open={open} onOpenChange={value => { if (!value) controller.current?.abort(); onOpenChange(value); }} title="工艺访谈助手"
    description="像访谈一样描述工艺；助手追问缺失的计价基准、重复条件与样例，并整理成路线草稿。工序数量等关键数据始终以专家回答为准。">
    <AssistantStatusNotice status={status} error={statusError} sends={`已发布工序与时间模型目录、您的回答${continueCurrent ? "，以及当前白板中的模板草稿" : ""}`} />
    {current.steps.length > 0 && <label className="assistant-toggle"><input type="checkbox" checked={continueCurrent} disabled={busy} onChange={event => setContinueCurrent(event.target.checked)} />基于当前白板「{current.label || current.id}」继续完善</label>}
    <ol ref={transcript} className="assistant-transcript" aria-label="访谈记录" aria-live="polite">
      {!turns.length && <li data-role="assistant"><p>请先介绍这条工艺：适用于什么产品？大致有哪些工序、哪些需要按层/按面重复？</p></li>}
      {turns.map((turn, index) => <li key={index} data-role={turn.role}><span className="sr-only">{turn.role === "expert" ? "专家" : "助手"}：</span><p>{turn.content}</p></li>)}
    </ol>
    {busy && <Busy label="助手正在整理并检查草稿…" />}
    {error && <p role="alert" className="assistant-notice" data-tone="error">{error}</p>}
    {output && output.questions.some(item => item.options?.length) && <div className="assistant-options" aria-label="快捷回答">{output.questions.flatMap(item => (item.options ?? []).map(option => <button key={`${item.id}:${option}`} type="button" onClick={() => setText(value => `${value}${value ? "；" : ""}${item.question.replace(/[？?]$/, "")}：${option}`)}>{option}</button>))}</div>}
    <form className="assistant-compose" onSubmit={event => { event.preventDefault(); void send(); }}>
      <Textarea aria-label="专家回答" rows={3} value={text} disabled={busy || !ready} maxLength={4000} placeholder="例如：多层板先做内层蚀刻，每个内层一次，按板计价；然后统一压合。" onChange={event => setText(event.target.value)}
        onKeyDown={event => { if (event.key === "Enter" && (event.metaKey || event.ctrlKey)) { event.preventDefault(); void send(); } }} />
      <div className="flex flex-wrap gap-2"><Button type="submit" disabled={busy || !ready || !text.trim()}><Send />发送 <kbd className="assistant-kbd">⌘↵</kbd></Button><Button type="button" variant="ghost" disabled={!turns.length} onClick={reset}><RotateCcw />重新开始</Button></div>
    </form>
    {latest && output?.template && <section className="assistant-section"><h3>模板草稿 · {String(output.template.label ?? output.template.id)} <small>{Array.isArray(output.template.steps) ? output.template.steps.length : 0} 道工序</small></h3>
      {output.changes && output.changes.length > 0 && <ul className="assistant-list">{output.changes.map(item => <li key={item}>{item}</li>)}</ul>}
      <IssueList issues={latest.check.issues} empty="模板通过校验，样例展开与专家预期一致。" />
      <div className="assistant-adopt"><p>{editable ? "应用后可撤销；在白板中继续编辑，再提交审核。" : "当前模板为只读，请先新建模板或创建新版本。"}</p><Button disabled={!editable} onClick={apply}><Check />应用到白板</Button></div>
    </section>}
  </AssistantSheet>;
}
