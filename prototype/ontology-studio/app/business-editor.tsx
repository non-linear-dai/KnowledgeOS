"use client";

import { useState } from "react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { evaluateBusiness, impactBusiness, previewBusiness } from "./knowledgeos-api";
import type { ConfigValue, OntologyDefinition } from "./studio-data";

type BusinessKind = "business_constraint" | "business_rule";
type BusinessInput = { id: string; role: "subject" | "candidate"; predicate: string };
type BusinessCheck = { left: string; operator: string; right: { input?: string; value?: unknown }; message: string };
type PreviewResult = { results: { verdict: string; candidate_id: string | null; entity_id?: string; checks?: { passed: boolean | null; stage?: string; message: string; left_value: string | null; right_value: string | null }[] }[]; eligible_candidate_ids?: string[]; verdict_counts?: Record<string, number>; source_refs?: unknown };
const verdictLabels: Record<string, string> = { eligible: "符合条件", not_matched: "未命中规则", condition_failed: "适用后条件失败", valid: "有效", invalid: "无效", unknown: "信息不足" };

function object(value: unknown): Record<string, unknown> { return value && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : {}; }
function inputsOf(value: unknown): BusinessInput[] { return Array.isArray(value) ? value as BusinessInput[] : []; }
function checksOf(value: unknown): BusinessCheck[] { return Array.isArray(value) ? value as BusinessCheck[] : []; }
function literalText(value: unknown) { return typeof value === "string" ? value : JSON.stringify(value ?? ""); }
function parseLiteral(value: string) { try { return JSON.parse(value); } catch { return value; } }
function SelectField({ label, value, options, onChange }: { label: string; value: string; options: { value: string; label: string }[]; onChange: (value: string) => void }) {
  return <div className="space-y-1"><Label>{label}</Label><select className="h-9 w-full rounded-md border border-slate-200 bg-white px-2 text-xs" value={value} onChange={(event) => onChange(event.target.value)}>{options.map((item) => <option key={item.value} value={item.value}>{item.label}</option>)}</select></div>;
}

export function BusinessEditor({ definition, definitions, onUpdate }: { definition: OntologyDefinition; definitions: OntologyDefinition[]; onUpdate: (next: OntologyDefinition) => void }) {
  const kind = definition.kind as BusinessKind;
  const [config, setConfig] = useState<Record<string, ConfigValue>>({ ...definition.config });
  const [sample, setSample] = useState(JSON.stringify({ subject: { rated_power: { value: 10, unit: "kW" } }, candidates: [{ id: "equipment-a", rated_power: { value: 12, unit: "kW" } }, { id: "equipment-b", rated_power: { value: 8, unit: "kW" } }] }, null, 2));
  const [literalDrafts, setLiteralDrafts] = useState<Record<number, string>>({});
  const [preview, setPreview] = useState<PreviewResult | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [subjectId, setSubjectId] = useState("");
  const [candidateIds, setCandidateIds] = useState("");
  const scope = object(config.scope);
  const inputs = inputsOf(config.inputs);
  const checks = checksOf(config.checks);
  const concepts = definitions.filter((item) => item.kind === "concept").map((item) => ({ value: item.id, label: item.label }));
  const predicates = definitions.filter((item) => item.kind === "predicate").map((item) => ({ value: item.id, label: item.label }));
  const relations = definitions.filter((item) => item.kind === "relation" && (item.endpoints ?? []).some((edge) => edge.sourceConceptId === scope.subject_concept && edge.targetConceptId === scope.candidate_concept)).map((item) => ({ value: item.id, label: item.label }));
  const setField = (key: string, value: ConfigValue) => setConfig((current) => ({ ...current, [key]: value }));
  const setScope = (key: string, value: string) => setField("scope", { ...scope, [key]: value } as Record<string, ConfigValue>);
  const setInput = (index: number, patch: Partial<BusinessInput>) => setField("inputs", inputs.map((item, i) => i === index ? { ...item, ...patch } : item));
  const setCheck = (index: number, patch: Partial<BusinessCheck>) => setField("checks", checks.map((item, i) => i === index ? { ...item, ...patch } : item) as unknown as ConfigValue);
  const document = () => ({ ...config, format: kind === "business_rule" ? "knowledgeos.business-rule.v1" : "knowledgeos.business-constraint.v1",
    id: definition.id.replace(/^business_(rule|constraint):/, ""), label: definition.label, description: definition.description,
    status: { lifecycle: definition.lifecycle } });
  const save = () => { onUpdate({ ...definition, config }); setError(""); toast.success("业务定义草稿已暂存；提交后进入独立审核"); };
  const runPreview = async () => {
    setBusy(true);
    try {
      const facts = JSON.parse(sample);
      const result = await previewBusiness(kind, document(), facts);
      setPreview(result.data); setError("");
    } catch (cause) { setError(cause instanceof Error ? cause.message : "试算失败"); setPreview(null); }
    finally { setBusy(false); }
  };
  const runEntities = async () => {
    setBusy(true);
    try {
      const selectedCandidates = candidateIds.split(",").map((item) => item.trim()).filter(Boolean);
      const result = await impactBusiness(kind, document(), subjectId.trim(), selectedCandidates.length ? selectedCandidates : null);
      setPreview(result.data); setError("");
    } catch (cause) { setError(cause instanceof Error ? cause.message : "实体评估失败"); setPreview(null); }
    finally { setBusy(false); }
  };
  const runPublished = async () => {
    setBusy(true);
    try {
      const selectedCandidates = candidateIds.split(",").map((item) => item.trim()).filter(Boolean);
      const result = await evaluateBusiness(kind, definition.id.replace(/^business_(rule|constraint):/, ""), subjectId.trim(), selectedCandidates.length ? selectedCandidates : null);
      setPreview(result.data); setError("");
    } catch (cause) { setError(cause instanceof Error ? cause.message : "已发布版本评估失败"); setPreview(null); }
    finally { setBusy(false); }
  };
  return <section className="space-y-4 rounded-2xl border border-teal-200 bg-white p-4">
    <div><p className="text-sm font-semibold text-teal-950">{kind === "business_rule" ? "业务规则" : "业务约束"}定义</p><p className="mt-1 text-xs leading-5 text-slate-500">{kind === "business_rule" ? "主体条件限定规则适用范围；候选条件筛选符合的实体。事实缺失时返回信息不足。" : "校验主体事实；启用后编译器会阻止不符合约束的知识。"} 修改后递增版本并提交审核。</p></div>
    <div><Label>语义版本</Label><Input value={String(config.version ?? "1.0.0")} onChange={(event) => setField("version", event.target.value)} placeholder="1.0.1" /></div>
    <div className="grid gap-2 sm:grid-cols-2"><SelectField label="主体概念" value={String(scope.subject_concept ?? "")} options={concepts} onChange={(value) => setScope("subject_concept", value)} />{kind === "business_rule" && <SelectField label="候选概念" value={String(scope.candidate_concept ?? "")} options={concepts} onChange={(value) => setScope("candidate_concept", value)} />}</div>
    {kind === "business_rule" && <SelectField label="关联关系类型（可选）" value={String(scope.relation_type ?? "")} options={[{ value: "", label: "仅返回候选子集" }, ...relations]} onChange={(value) => setScope("relation_type", value)} />}
    <div className="space-y-2 border-t pt-3"><div className="flex items-center justify-between"><p className="text-xs font-semibold">输入事实</p><Button size="sm" variant="outline" onClick={() => setField("inputs", [...inputs, { id: `input_${inputs.length + 1}`, role: "subject", predicate: predicates[0]?.value ?? "" }])}>添加输入</Button></div>{inputs.map((item, index) => <div key={index} className="grid gap-2 rounded-xl border p-2"><Input aria-label={`输入 ${index + 1} 变量名`} value={item.id} onChange={(event) => setInput(index, { id: event.target.value })} placeholder="变量名" /><div className="grid grid-cols-2 gap-2"><SelectField label="来自" value={item.role} options={kind === "business_rule" ? [{ value: "subject", label: "主体" }, { value: "candidate", label: "候选实体" }] : [{ value: "subject", label: "主体" }]} onChange={(value) => setInput(index, { role: value as BusinessInput["role"] })} /><SelectField label="判断类型" value={item.predicate} options={predicates} onChange={(value) => setInput(index, { predicate: value })} /></div><Button size="sm" variant="ghost" className="justify-self-end text-rose-600" disabled={inputs.length <= 1} onClick={() => setField("inputs", inputs.filter((_, i) => i !== index))}>移除</Button></div>)}</div>
    <div className="space-y-2 border-t pt-3"><div className="flex items-center justify-between"><p className="text-xs font-semibold">必须同时满足的条件</p><Button size="sm" variant="outline" onClick={() => setField("checks", [...checks, { left: inputs[0]?.id ?? "", operator: "eq", right: { input: inputs[0]?.id ?? "" }, message: "" }] as unknown as ConfigValue)}>添加条件</Button></div>{checks.map((check, index) => <div key={index} className="space-y-2 rounded-xl border p-2"><div className="grid grid-cols-2 gap-2"><SelectField label="左侧输入" value={check.left} options={inputs.map((item) => ({ value: item.id, label: item.id }))} onChange={(value) => setCheck(index, { left: value })} /><SelectField label="比较" value={check.operator} options={["eq", "ne", "gt", "gte", "lt", "lte"].map((value) => ({ value, label: ({ eq: "等于", ne: "不等于", gt: "大于", gte: "大于等于", lt: "小于", lte: "小于等于" } as Record<string, string>)[value] }))} onChange={(value) => setCheck(index, { operator: value })} /></div><SelectField label="右侧类型" value={"input" in check.right ? "input" : "value"} options={[{ value: "input", label: "另一项输入" }, { value: "value", label: "固定值" }]} onChange={(value) => { setLiteralDrafts((current) => { const next = { ...current }; delete next[index]; return next; }); setCheck(index, { right: value === "input" ? { input: inputs[0]?.id ?? "" } : { value: 0 } }); }} />{"input" in check.right ? <SelectField label="右侧输入" value={String(check.right.input ?? "")} options={inputs.map((item) => ({ value: item.id, label: item.id }))} onChange={(value) => setCheck(index, { right: { input: value } })} /> : <div><Label>固定值（数字、文本或带单位 JSON）</Label><Input value={literalDrafts[index] ?? literalText(check.right.value)} onChange={(event) => { setLiteralDrafts((current) => ({ ...current, [index]: event.target.value })); setCheck(index, { right: { value: parseLiteral(event.target.value) } }); }} placeholder={'{"value": 0, "unit": "W"}'} /></div>}<div><Label>条件未通过时说明</Label><Input value={check.message ?? ""} onChange={(event) => setCheck(index, { message: event.target.value })} /></div><Button size="sm" variant="ghost" className="w-full text-rose-600" disabled={checks.length <= 1} onClick={() => setField("checks", checks.filter((_, i) => i !== index) as unknown as ConfigValue)}>移除此条件</Button></div>)}</div>
    <Button size="sm" onClick={save}>保存业务定义草稿</Button>
    <div className="space-y-2 border-t pt-3"><Label>用样例事实试算（只读，不写入事实）</Label><Textarea className="min-h-36 font-mono text-xs" value={sample} onChange={(event) => setSample(event.target.value)} /><Button size="sm" variant="outline" disabled={busy} onClick={() => void runPreview()}>试算当前定义</Button></div>
    <div className="space-y-2 border-t pt-3"><p className="text-xs font-semibold">评估现有实体影响（当前草稿）</p>{kind === "business_rule" && <Input value={subjectId} onChange={(event) => setSubjectId(event.target.value)} placeholder="主体实体 ID" />}{kind === "business_rule" && <Input value={candidateIds} onChange={(event) => setCandidateIds(event.target.value)} placeholder="候选实体 ID，以逗号分隔；留空评估全部候选概念实体" />}<div className="flex flex-wrap gap-2"><Button size="sm" variant="outline" disabled={busy || (kind === "business_rule" && !subjectId.trim())} onClick={() => void runEntities()}>评估当前草稿影响</Button>{definition.lifecycle === "active" && <Button size="sm" variant="outline" disabled={busy || !subjectId.trim()} onClick={() => void runPublished()}>按已发布版本执行</Button>}</div></div>
    {preview && <div className="space-y-2"><p className="text-xs font-semibold">结果与命中条件</p>{preview.verdict_counts && <p className="text-xs text-slate-600">影响统计：{Object.entries(preview.verdict_counts).map(([key, count]) => `${verdictLabels[key] ?? key} ${count}`).join(" · ")}</p>}{kind === "business_rule" && <p className="text-xs text-teal-800">符合条件：{preview.eligible_candidate_ids?.length ?? 0} 个候选实体{preview.eligible_candidate_ids?.length ? ` · ${preview.eligible_candidate_ids.join("、")}` : ""}</p>}{preview.results.length ? preview.results.map((item, index) => <div key={`${item.entity_id ?? item.candidate_id ?? "subject"}-${index}`} className="rounded-xl border bg-slate-50 p-3"><div className="flex items-center justify-between gap-2"><span className="truncate text-xs font-semibold">{item.entity_id ?? item.candidate_id ?? (kind === "business_rule" ? `候选 ${index + 1}` : "主体")}</span><span className={`rounded-full px-2 py-0.5 text-[10px] font-semibold ${item.verdict === "eligible" || item.verdict === "valid" ? "bg-emerald-100 text-emerald-800" : item.verdict === "unknown" ? "bg-amber-100 text-amber-800" : item.verdict === "not_matched" ? "bg-slate-200 text-slate-700" : "bg-rose-100 text-rose-800"}`}>{verdictLabels[item.verdict] ?? item.verdict}</span></div>{item.checks?.map((check, checkIndex) => <p key={checkIndex} className="mt-2 text-[11px] leading-5 text-slate-600">{check.passed === true ? "✓" : check.passed === false ? "×" : "?"} {check.stage === "subject_match" && kind === "business_rule" ? "主体筛选 · " : check.stage === "candidate_condition" && kind === "business_rule" ? "候选条件 · " : ""}{check.message || "比较条件"}：{check.left_value ?? "缺失"} / {check.right_value ?? "缺失"}</p>)}</div>) : <p className="text-xs text-slate-500">当前范围内没有可评估的实体。</p>}{preview.source_refs != null && <details className="text-[11px] text-slate-500"><summary className="cursor-pointer">查看事实来源与证据</summary><pre className="mt-2 max-h-48 overflow-auto rounded-lg bg-slate-950 p-2 text-cyan-100">{JSON.stringify(preview.source_refs, null, 2)}</pre></details>}</div>}
    {error && <p role="alert" className="text-xs text-rose-700">{error}</p>}
  </section>;
}
