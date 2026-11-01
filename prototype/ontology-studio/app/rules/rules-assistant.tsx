"use client";

import { useEffect, useRef, useState } from "react";
import { Check, Sparkles } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { AssistantSheet, AssistantStatusNotice, Busy, IssueList, SuggestionSummary, useAssistantStatus } from "../assistant/assistant-panel";
import { adoptLogic, regionText, requestSuggestion, type Assistance, type BoundaryCase, type Coverage, type IntentResult, type LogicDraftOutput, type Suggestion } from "../assistant/assistant-api";
import { logicKindLabels, pretty, records, object, type LogicDocument, type LogicKind } from "./logic-api";

type AssistKind = "decision_table" | "business_rule" | "business_constraint";
const assistKinds: AssistKind[] = ["decision_table", "business_rule", "business_constraint"];
const operators: Record<string, string> = { eq: "=", ne: "≠", gt: ">", gte: "≥", lt: "<", lte: "≤", in: "属于", between: "介于" };

function cellText(cell: unknown) {
  const value = object(cell);
  if (!cell) return "任意";
  const raw = value.value;
  if (value.op === "between" && Array.isArray(raw)) return `${raw[0]} ~ ${raw[1]}`;
  return `${operators[String(value.op)] ?? String(value.op)} ${Array.isArray(raw) ? raw.join("、") : String(raw)}`;
}

function valueText(value: unknown): string {
  if (value == null) return "—";
  if (typeof value !== "object") return String(value);
  const item = object(value);
  if ("literal" in item && "unit" in item) return `${item.literal} ${item.unit}`;
  if ("value" in item && "unit" in item) return `${item.value} ${item.unit}`;
  return Object.entries(item).map(([key, entry]) => `${key}: ${valueText(entry)}`).join("，");
}

function DefinitionPreview({ kind, definition }: { kind: AssistKind; definition: Record<string, unknown> }) {
  if (kind === "decision_table") {
    const inputs = records(definition.inputs), outputs = records(definition.outputs);
    return <div className="assistant-table-wrap"><table className="assistant-table"><thead><tr><th>规则</th>{inputs.map(column => <th key={String(column.id)}>{String(column.label ?? column.id)}</th>)}{outputs.map(column => <th key={String(column.id)}>→ {String(column.label ?? column.id)}</th>)}</tr></thead>
      <tbody>{records(definition.rules).map(rule => <tr key={String(rule.id)}><td><code>{String(rule.id)}</code></td>{inputs.map(column => <td key={String(column.id)}>{cellText(object(rule.when)[String(column.id)])}</td>)}{outputs.map(column => <td key={String(column.id)}>{valueText(object(rule.then)[String(column.id)])}</td>)}</tr>)}
        {definition.default_output != null && <tr><td>其他情况</td>{inputs.map(column => <td key={String(column.id)}>—</td>)}{outputs.map(column => <td key={String(column.id)}>{valueText(object(definition.default_output)[String(column.id)])}</td>)}</tr>}</tbody></table>
      <p className="assistant-caption">命中方式：{({ UNIQUE: "互斥（唯一命中）", FIRST: "按顺序首条命中", COLLECT: "收集全部命中" } as Record<string, string>)[String(definition.hit_policy)] ?? String(definition.hit_policy)}</p></div>;
  }
  const inputs = Object.fromEntries(records(definition.inputs).map(item => [String(item.id), item]));
  return <ul className="assistant-list">{records(definition.checks).map((check, index) => {
    const right = object(check.right);
    const left = inputs[String(check.left)];
    return <li key={index}>{String(left?.role === "candidate" ? "候选" : "主体")}的 <code>{String(left?.predicate ?? check.left)}</code> {operators[String(check.operator)] ?? String(check.operator)} {"input" in right ? <code>{String(inputs[String(right.input)]?.predicate ?? right.input)}</code> : valueText(right.value)}{check.message ? `（${String(check.message)}）` : ""}</li>;
  })}</ul>;
}

export function RulesAssistant({ open, onOpenChange, current, canAuthor, onAdopt }: {
  open: boolean; onOpenChange: (open: boolean) => void;
  current: { kind: LogicKind; definition: LogicDocument; label: string } | null;
  canAuthor: (kind: LogicKind) => boolean;
  onAdopt: (kind: AssistKind, definition: LogicDocument, assistance: Assistance, refine: boolean) => void;
}) {
  const { status, error: statusError, ready } = useAssistantStatus(open);
  const refinable = Boolean(current && assistKinds.includes(current.kind as AssistKind));
  const [refine, setRefine] = useState(false);
  const [kind, setKind] = useState<"" | AssistKind>("");
  const [description, setDescription] = useState("");
  const [busy, setBusy] = useState(false), [error, setError] = useState("");
  const [suggestion, setSuggestion] = useState<Suggestion<LogicDraftOutput> | null>(null);
  const [acceptedIntent, setAcceptedIntent] = useState<string[]>([]), [confirmed, setConfirmed] = useState<string[]>([]);
  const controller = useRef<AbortController | null>(null);
  // A different selected asset starts a fresh assistant session.
  // eslint-disable-next-line react-hooks/set-state-in-effect
  useEffect(() => { setRefine(refinable); setSuggestion(null); setError(""); }, [current?.definition.id, refinable]);
  useEffect(() => () => controller.current?.abort(), []);
  const targetKind = refine && current ? current.kind as AssistKind : kind;
  const allowed = targetKind ? canAuthor(targetKind) : assistKinds.some(canAuthor);

  async function generate() {
    controller.current?.abort();
    const next = new AbortController(); controller.current = next;
    setBusy(true); setError(""); setSuggestion(null);
    try {
      const input: Record<string, unknown> = { description: description.trim(), ...(targetKind ? { kind: targetKind } : {}) };
      if (refine && current) input.current = { kind: current.kind, definition: current.definition };
      const result = await requestSuggestion<LogicDraftOutput>("logic.draft", input, next.signal);
      if (next.signal.aborted) return;
      setSuggestion(result);
      const intent = (result.check.analysis.intent_tests ?? []) as IntentResult[];
      setAcceptedIntent(intent.filter(item => item.passed).map(item => item.id)); setConfirmed([]);
    } catch (cause) { if (!next.signal.aborted) setError(cause instanceof Error ? cause.message : "起草失败"); }
    finally { if (!next.signal.aborted) setBusy(false); }
  }
  function adopt() {
    if (!suggestion) return;
    try {
      const adopted = adoptLogic(suggestion, acceptedIntent, confirmed);
      if (!canAuthor(adopted.kind)) throw new Error(`当前账户没有编制${logicKindLabels[adopted.kind]}的权限`);
      onAdopt(adopted.kind, adopted.definition as LogicDocument, adopted.assistance, refine);
      onOpenChange(false); setSuggestion(null); setDescription("");
    } catch (cause) { setError(cause instanceof Error ? cause.message : "无法采纳"); }
  }

  const coverage = suggestion?.check.analysis.coverage as Coverage | undefined;
  const intent = (suggestion?.check.analysis.intent_tests ?? []) as IntentResult[];
  const definition = suggestion?.check.submitted as Record<string, unknown> | null | undefined;
  const labels = Object.fromEntries(records(definition?.inputs).map(column => [String(column.id), String(column.label ?? column.id)]));
  const caseCount = intent.filter(item => item.passed && acceptedIntent.includes(item.id)).length + confirmed.length + records(definition?.test_cases).length;
  const toggle = (list: string[], id: string, on: boolean) => on ? [...new Set([...list, id])] : list.filter(item => item !== id);

  return <AssistantSheet open={open} onOpenChange={value => { if (!value) controller.current?.abort(); onOpenChange(value); }} title="助手起草业务逻辑" description="用业务语言描述规则；助手起草决策表或规则，KnowledgeOS 用确定性引擎检查覆盖、矛盾与案例。">
    <AssistantStatusNotice status={status} error={statusError} sends={`控制定义目录（概念、属性、单位、已登记逻辑名称）与您填写的说明${refine ? "，以及当前草稿" : ""}`} />
    {refinable && current && <div className="logic-segment" role="group" aria-label="起草方式"><button aria-pressed={!refine} onClick={() => setRefine(false)}>新建</button><button aria-pressed={refine} onClick={() => setRefine(true)}>修改「{current.label}」</button></div>}
    {!refine && <label className="logic-field">逻辑类型<select aria-label="助手起草的逻辑类型" value={kind} onChange={event => setKind(event.target.value as AssistKind | "")}><option value="">由助手判断</option>{assistKinds.filter(canAuthor).map(value => <option key={value} value={value}>{logicKindLabels[value]}</option>)}</select></label>}
    <label className="logic-field">{refine ? "要怎么修改？" : "业务规则怎么说？"}<Textarea aria-label="业务说明" rows={5} value={description} disabled={busy} maxLength={8000} onChange={event => setDescription(event.target.value)}
      placeholder={refine ? "例如：600 以上改为大型；补充边界值 300 与 600 的案例" : "例如：零件尺寸不超过 300 mm 用小型注塑机，300 到 600 用中型，超过 600 用大型。"} /></label>
    <div className="flex flex-wrap gap-2"><Button disabled={busy || !ready || !description.trim() || !allowed} onClick={() => void generate()}><Sparkles />{busy ? "起草中…" : "生成草稿"}</Button>{busy && <Button variant="ghost" onClick={() => { controller.current?.abort(); setBusy(false); }}>取消</Button>}</div>
    {!allowed && <p className="assistant-caption">当前账户没有编制该类逻辑的权限。</p>}
    {busy && <Busy label="助手正在起草，KnowledgeOS 会逐轮校验并要求修正（通常需要几十秒）…" />}
    {error && <p role="alert" className="assistant-notice" data-tone="error">{error}</p>}
    {suggestion && <>
      <SuggestionSummary suggestion={suggestion} summary={suggestion.output.summary} assumptions={suggestion.output.assumptions} questions={suggestion.output.open_questions} />
      <section className="assistant-section"><h3>确定性检查</h3><IssueList issues={suggestion.check.issues} /></section>
      {definition && <section className="assistant-section"><h3>{logicKindLabels[suggestion.output.kind]} · {String(definition.label ?? definition.id)} <small>v{String(definition.version)}</small></h3><DefinitionPreview kind={suggestion.output.kind} definition={definition} />
        {coverage && coverage.gap_count > 0 && <div className="assistant-callout"><strong>以下输入组合没有规则命中：</strong><ul>{coverage.gaps.map((region, index) => <li key={index}>{regionText(region, labels)}</li>)}</ul>{coverage.gap_count > coverage.gaps.length && <p>另有 {coverage.gap_count - coverage.gaps.length} 类未列出。</p>}</div>}
        {coverage && coverage.unreachable_rules.length > 0 && <div className="assistant-callout">规则 {coverage.unreachable_rules.join("、")} 被前面的规则完全覆盖，永远不会生效。</div>}
        <details className="change-json"><summary>完整定义</summary><pre>{pretty(definition)}</pre></details></section>}
      {intent.length > 0 && <section className="assistant-section"><h3>意图案例 <small>助手根据说明独立写出预期，引擎执行比对</small></h3><ul className="assistant-cases">{intent.map(item => <li key={item.id} data-passed={item.passed}>
        <label><input type="checkbox" disabled={!item.passed} checked={item.passed && acceptedIntent.includes(item.id)} onChange={event => setAcceptedIntent(list => toggle(list, item.id, event.target.checked))} /><span><strong>{item.label}</strong>
          <small>输入：{valueText(object(item.input).facts ?? item.input)} · 预期：{valueText(object(item.expected).result ?? item.expected)}</small>
          {item.passed ? <small className="assistant-pass"><Check size={12} aria-hidden="true" />引擎结果一致</small> : <small className="assistant-fail">{item.error ? `执行失败：${item.error}` : `实际：${valueText(object(item.actual).result ?? object(item.actual).status)}——请判断定义还是预期有误`}</small>}</span></label></li>)}</ul></section>}
      {coverage && coverage.boundary_cases.length > 0 && <section className="assistant-section"><h3>边界情况确认 <small>引擎在每个边界计算出的结果，确认后成为回归案例</small></h3><ul className="assistant-cases">{coverage.boundary_cases.map((item: BoundaryCase) => <li key={item.id} data-passed={Boolean(item.actual)}>
        <label><input type="checkbox" disabled={!item.actual} checked={confirmed.includes(item.id)} onChange={event => setConfirmed(list => toggle(list, item.id, event.target.checked))} /><span><strong>{item.label}</strong>
          <small>输入：{valueText(object(item.input).facts)}{object(item.input).item ? ` · 条目：${valueText(object(item.input).item)}` : ""}</small>
          {item.actual ? <small>结果：{valueText(object(item.actual).result)}{item.used_default ? "（默认结果）" : ""}</small> : <small className="assistant-fail">没有规则命中：运行时会报错</small>}</span></label></li>)}</ul></section>}
      <div className="assistant-adopt">
        <p>采纳后在白板中继续编辑；{caseCount ? `将带入 ${caseCount} 个回归案例。` : "提交审核前至少需要一个确认过的案例。"}</p>
        <Button disabled={!suggestion.check.submitted || !canAuthor(suggestion.output.kind)} onClick={adopt}><Check />采纳为{refine ? "修改后的" : "新"}草稿</Button>
      </div>
    </>}
  </AssistantSheet>;
}
