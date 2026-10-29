'use client';
import { useRef, useState } from 'react';
import { Check, RefreshCw, Sparkles } from 'lucide-react';
import { toast } from 'sonner';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Textarea } from '@/components/ui/textarea';
import { AssistantStatusNotice, Busy, IssueList, SuggestionSummary, useAssistantStatus } from './assistant/assistant-panel';
import { checkDraft, requestSuggestion, withAccepted, type AuthoringCheck, type OntologyOutput, type Suggestion } from './assistant/assistant-api';
import { ontologyPropose, type JsonObject, type OntologyRequest } from './semantic-api';

export type SimilarEntry = { target_kind: string; target_id: string; label?: string; matches: { id: string; label: string; score: number; verdict: string; existing_name: { name: string; origin: string } }[] };
const verdicts: Record<string, string> = { reuse: '建议复用', similar: '名称相近', dimension_conflict: '名称相近但量纲不同' };
const kinds: Record<string, string> = { concept: '概念', predicate: '属性', relation: '关系' };

/** Deterministic reuse hints from KnowledgeOS: shown for manual drafts as well as assistant drafts. */
export function SimilarDefinitions({ entries }: { entries: SimilarEntry[] }) {
  if (!entries.length) return null;
  return <div className="assistant-callout" role="note"><strong>已有相近定义，请优先复用：</strong><ul>{entries.map(entry => <li key={entry.target_id}>新{kinds[entry.target_kind] ?? ''}「{entry.label ?? entry.target_id}」：{entry.matches.slice(0, 3).map(match => `${match.label}（${match.id}，${verdicts[match.verdict] ?? match.verdict}，依据“${match.existing_name.name}”·${match.existing_name.origin}）`).join('；')}</li>)}</ul></div>;
}

type Operation = { operation: { type: string; targetKind: string; targetId: string; after?: JsonObject }; evidence: { quote: string }[] };

export function OntologyDraftTab({ canPropose, onSubmitted, onClose }: { canPropose: boolean; onSubmitted: () => Promise<unknown>; onClose: () => void }) {
  const { status, error: statusError, ready } = useAssistantStatus(true);
  const [goal, setGoal] = useState(''); const [material, setMaterial] = useState('');
  const [busy, setBusy] = useState(false); const [error, setError] = useState('');
  const [suggestion, setSuggestion] = useState<Suggestion<OntologyOutput> | null>(null);
  const [selected, setSelected] = useState<number[]>([]);
  const [check, setCheck] = useState<{ selection: string; result: AuthoringCheck } | null>(null);
  const [reason, setReason] = useState(''); const key = useRef('');
  const operations = ((suggestion?.output.model_output.operations ?? []) as Operation[]);
  const selection = selected.join(',');
  const filtered = suggestion ? { ...suggestion.output.model_output, operations: operations.filter((_, index) => selected.includes(index)) } : null;
  const current = check?.selection === selection ? check.result : null;

  const run = async (action: () => Promise<void>) => { setBusy(true); setError(''); try { await action(); } catch (e) { setError(e instanceof Error ? e.message : '操作失败，请重试'); } finally { setBusy(false); } };
  const generate = () => run(async () => {
    setSuggestion(null); setCheck(null); key.current = '';
    const result = await requestSuggestion<OntologyOutput>('ontology.model', { goal: goal.trim(), material: material.trim() });
    const all = ((result.output.model_output.operations ?? []) as unknown[]).map((_, index) => index);
    setSuggestion(result); setSelected(all); setCheck({ selection: all.join(','), result: result.check });
  });
  const recheck = () => run(async () => {
    if (!suggestion || !filtered) return;
    setCheck({ selection, result: await checkDraft('ontology.model', { ontology_request: suggestion.ontology_request!, model_output: filtered }) });
    key.current = '';
  });
  const submit = () => run(async () => {
    if (!suggestion || !filtered || !current?.valid) return;
    key.current ||= crypto.randomUUID();
    const assistance = withAccepted(suggestion.assistance, selected.length);
    await ontologyPropose(suggestion.ontology_request as OntologyRequest, filtered, reason.trim() || `助手起草：${goal.trim()}`, key.current, assistance);
    toast.success('已提交审核，另一位审核人批准后生效'); setSuggestion(null); setCheck(null); await onSubmitted(); onClose();
  });
  const notes = Object.fromEntries((suggestion?.output.notes ?? []).map(note => [note.target_id, note]));

  return <div className="space-y-4">
    <AssistantStatusNotice status={status} error={statusError} sends="控制定义目录（概念、属性、关系、单位及其中文叫法）与您粘贴的建模依据" />
    <div className="space-y-2"><Label htmlFor="assistant-goal">要建模什么？</Label><Input id="assistant-goal" disabled={busy} value={goal} maxLength={4000} placeholder="例如：建模检验报告及其检验结论" onChange={e => setGoal(e.target.value)} /></div>
    <div className="space-y-2"><Label htmlFor="assistant-material">建模依据</Label><Textarea id="assistant-material" disabled={busy} rows={6} maxLength={30000} value={material} placeholder="粘贴业务规范、表单说明或制度条款；每项定义都必须引用这里的原文" onChange={e => setMaterial(e.target.value)} /></div>
    <Button disabled={busy || !ready || !canPropose || !goal.trim() || !material.trim()} onClick={() => void generate()}><Sparkles />{busy && !suggestion ? '起草中…' : '生成建模草稿'}</Button>
    {busy && !suggestion && <Busy label="助手正在起草；KnowledgeOS 会编译候选定义并检查重复…" />}
    {error && <p role="alert" className="rounded-lg bg-rose-50 p-3 text-sm text-rose-800 break-words">{error}</p>}
    {suggestion && <>
      <SuggestionSummary suggestion={suggestion} summary={suggestion.output.summary} questions={suggestion.output.open_questions} />
      <fieldset className="space-y-2" disabled={busy}><legend className="text-sm font-medium">定义变更（取消勾选可排除）</legend>
        <ul className="assistant-cases">{operations.map((item, index) => { const after = item.operation.after ?? {}; return <li key={index} data-passed><label><input type="checkbox" checked={selected.includes(index)} onChange={e => setSelected(list => e.target.checked ? [...list, index].sort((a, b) => a - b) : list.filter(value => value !== index))} /><span>
          <strong>{({ create: '新建', update: '修改', deprecate: '弃用' } as Record<string, string>)[item.operation.type] ?? item.operation.type}{kinds[item.operation.targetKind] ?? item.operation.targetKind}「{String(after.label ?? item.operation.targetId)}」<code className="ml-1">{item.operation.targetId}</code></strong>
          {notes[item.operation.targetId] && <small>{notes[item.operation.targetId].explanation}{notes[item.operation.targetId].reused?.length ? ` · 复用 ${notes[item.operation.targetId].reused!.join('、')}` : ''}</small>}
          <small>依据：「{item.evidence.map(quote => quote.quote).join('」「')}」</small></span></label></li>; })}</ul>
      </fieldset>
      {current ? <><IssueList issues={current.issues} /><SimilarDefinitions entries={(current.analysis.similar ?? []) as SimilarEntry[]} /></> : <div className="flex items-center gap-2 text-sm"><span>选择已变化，请重新检查。</span><Button size="sm" variant="outline" disabled={busy || !selected.length} onClick={() => void recheck()}><RefreshCw />重新检查所选</Button></div>}
      <details className="rounded-lg border p-3"><summary className="cursor-pointer text-sm">查看提交内容</summary><pre className="mt-2 max-h-48 overflow-auto text-xs">{JSON.stringify(filtered, null, 2)}</pre></details>
      <Label htmlFor="assistant-reason">变更原因（可选）</Label><Input id="assistant-reason" disabled={busy} value={reason} onChange={e => { setReason(e.target.value); key.current = ''; }} placeholder="例如：按检验规范补充检验报告建模" />
      <div className="flex justify-end"><Button disabled={busy || !canPropose || !current?.valid || !selected.length} onClick={() => void submit()}><Check />提交审核</Button></div>
    </>}
  </div>;
}
