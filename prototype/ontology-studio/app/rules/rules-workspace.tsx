"use client";

import Link from "next/link";
import { useSearchParams, useRouter } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import { ArrowUpRight, BookOpen, Check, ChevronDown, Code2, Copy, MoreHorizontal, PanelLeftClose, PanelLeftOpen, FlaskConical, GitBranch, History, LibraryBig, Loader2, Plus, RefreshCw, Search, Send, ShieldCheck, Sparkles, Table2, Trash2, X } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuLabel, DropdownMenuSeparator, DropdownMenuTrigger } from "@/components/ui/dropdown-menu";
import { NavigationToggle } from "../workspace-navigation-context";
import { useWorkspaceSession } from "../workspace-session";
import { loadStudioSnapshot } from "../knowledgeos-api";
import type { OntologyDefinition } from "../studio-data";
import { loadInstances, defaultInstanceFilters, displayDate } from "../instances/instances-api";
import { LogicBoard } from "./logic-board";
import { RulesAssistant } from "./rules-assistant";
import type { Assistance } from "../assistant/assistant-api";
import { LogicToolHint } from "./logic-tool-hint";
import { ScenarioForm, LogicCases, BusinessResult } from "./logic-validation";
import { BusinessData } from "./business-values";
import { businessLogicGuides } from "./logic-business-guide";
import { assetLabel, completeScenario, displayValue, expectedShape, fieldLabel, normalizeCaseNumbers } from "./logic-visual";
import { JsonField, LogicEditor } from "./logic-editor";
import { LogicGuideContent, LogicGuideDialog, LogicTypeSummary } from "./logic-guide";
import { guideExample, logicGuides } from "./logic-guides";
import { bumpVersion, filterLogic, loadLogic, loadLogicRuns, logicHref, logicKindLabels, logicStatusLabels, newLogic, object, pretty, previewLogic, proposeLogic, records, replayLogic, runLogic, sampleInput, type EntityBindings, type LogicAsset, type LogicCatalog, type LogicDocument, type LogicKind, type LogicPreview, type LogicRun } from "./logic-api";

type Draft = { kind: LogicKind; document: LogicDocument; baseline: string; originalRef?: string; submittedId?: string; revision: number; assistance?: Assistance };
type InstanceOption = { id: string; label: string };
const emptyCatalog: LogicCatalog = { assets: [], registry_fingerprint: "", capabilities: {} };
const kinds = Object.keys(logicKindLabels) as LogicKind[];
const auxiliaryViews = [{ id: "validation", label: "结果验证", Icon: FlaskConical }, { id: "guide", label: "说明与样例", Icon: BookOpen }, { id: "relations", label: "关联与使用", Icon: LibraryBig }, { id: "history", label: "运行记录", Icon: History }];

function InstancePicker({ label, concept, value, onChange }: { label: string; concept: string; value: string; onChange: (value: string) => void }) {
  const [query, setQuery] = useState("");
  const [items, setItems] = useState<InstanceOption[]>([]);
  const [error, setError] = useState("");
  useEffect(() => {
    const controller = new AbortController();
    const timer = setTimeout(() => { void loadInstances({ ...defaultInstanceFilters, kind: "concept", type: concept, query, limit: 50 }, controller.signal).then(page => { if (!controller.signal.aborted) { setItems(page.items); setError(""); } }).catch(cause => { if (!controller.signal.aborted) setError(cause instanceof Error ? cause.message : "无法读取实例"); }); }, 200);
    return () => { clearTimeout(timer); controller.abort(); };
  }, [concept, query]);
  return <div className="logic-field"><label>{label}<Input aria-label={`${label} 搜索`} placeholder="搜索名称或实例标识" value={query} onChange={event => setQuery(event.target.value)} /></label><select aria-label={label} value={value} onChange={event => onChange(event.target.value)}><option value="">选择实例…</option>{value && !items.some(item => item.id === value) && <option value={value}>{value}</option>}{items.map(item => <option key={item.id} value={item.id}>{item.label} · {item.id}</option>)}</select>{error && <span role="alert" className="text-xs text-destructive">{error}</span>}</div>;
}

function Result({ value, expert = false, definitions = [], document }: { value: Record<string, unknown>; expert?: boolean; definitions?: OntologyDefinition[]; document?: LogicDocument }) {
  const verdicts: Record<string, string> = { eligible: "符合条件", condition_failed: "候选条件未通过", not_matched: "不适用", unknown: "信息不足", valid: "约束通过", invalid: "约束未通过" };
  return <div className="logic-result"><div className="logic-result-heading"><ShieldCheck size={17} /><strong>{logicStatusLabels[String(value.status)] ?? "试算结果"}</strong>{Array.isArray(value.eligible_candidate_ids) && <span>{value.eligible_candidate_ids.length} 个合格候选</span>}</div>
    {records(value.results).map((row, index) => <article key={index} className="logic-verdict" data-verdict={String(row.verdict)}><div><strong>{String(row.candidate_id ?? row.entity_id ?? "主体")}</strong><span>{verdicts[String(row.verdict)] ?? String(row.verdict)}</span></div>{records(row.checks).map((check, i) => <p key={i}>{check.passed === true ? "✓" : check.passed === false ? "×" : "?"} {String(check.message || fieldLabel(String(check.left), definitions))} · {displayValue(check.left_value)} / {displayValue(check.right_value)}</p>)}</article>)}
    {value.output != null && (expert ? <pre>{pretty(value.output)}</pre> : <BusinessResult value={object(value.output)} definitions={definitions} document={document} />)}
    {value.result != null && (expert ? <pre>{pretty(value.result)}</pre> : <BusinessData value={value.result} definitions={definitions} labels={Object.fromEntries(records(document?.outputs).map(column => [String(column.id), fieldLabel(String(column.id), definitions, column.label)]))} />)}
    {Array.isArray(value.matched_rules) && <p className="logic-note">{value.used_default ? "本次使用默认结果" : value.matched_rules.length ? `本次有 ${value.matched_rules.length} 条规则成立` : "没有规则命中"}</p>}
    {records(value.trace).filter(row => row.node).map((row, index) => <details key={String(row.node)}><summary>{expert ? `节点 ${row.node} · ${row.ref}` : `步骤 ${index + 1} · ${logicStatusLabels[String(object(row.result).status)] ?? "执行结果"}`}</summary>{expert ? <pre>{pretty(row)}</pre> : <><h4 className="logic-note">本步骤输入</h4><BusinessData value={row.input} definitions={definitions} /><h4 className="logic-note">本步骤结果</h4><BusinessData value={object(row.result).output ?? object(row.result).result ?? object(row.result).results ?? { status: object(row.result).status }} definitions={definitions} /></>}</details>)}
    {expert && <details><summary>完整结果与计算过程</summary><pre>{pretty(value)}</pre></details>}
  </div>;
}

export default function RulesWorkspace({ active }: { active: boolean }) {
  const { session } = useWorkspaceSession();
  const params = useSearchParams(), router = useRouter();
  const [catalog, setCatalog] = useState<LogicCatalog>(emptyCatalog);
  const [definitions, setDefinitions] = useState<OntologyDefinition[]>([]);
  const [drafts, setDrafts] = useState<Record<string, Draft>>({});
  const [selectedKey, setSelectedKey] = useState("");
  const [query, setQuery] = useState("");
  const [kind, setKind] = useState("");
  const [lifecycle, setLifecycle] = useState("");
  const [concept, setConcept] = useState("");
  const [tab, setTab] = useState("definition"), [auxiliaryClosed, setAuxiliaryClosed] = useState(false);
  const [expert, setExpert] = useState(false), [catalogCollapsed, setCatalogCollapsed] = useState(false);
  const [createName, setCreateName] = useState("");
  const [guideOpen, setGuideOpen] = useState(false), [guideKind, setGuideKind] = useState<LogicKind>("business_rule");
  const [busy, setBusy] = useState(false), [loading, setLoading] = useState(false), [error, setError] = useState("");
  const [refresh, setRefresh] = useState(0);
  const [createOpen, setCreateOpen] = useState(false), [createKind, setCreateKind] = useState<LogicKind>("business_rule"), [createId, setCreateId] = useState("");
  const [reason, setReason] = useState(""), [submitOpen, setSubmitOpen] = useState(false), [assistantOpen, setAssistantOpen] = useState(false);
  const [sampleText, setSampleText] = useState("{}"), [inputMode, setInputMode] = useState("scenario");
  const [subjectId, setSubjectId] = useState(""), [candidateText, setCandidateText] = useState(""), [contextIds, setContextIds] = useState<Record<string, string>>({});
  const [preview, setPreview] = useState<LogicPreview | null>(null), [runs, setRuns] = useState<LogicRun[]>([]);
  const searchRef = useRef<HTMLInputElement>(null), selectedRef = useRef("");
  const rootRef = useRef<HTMLDivElement>(null), auxiliaryRef = useRef<HTMLDivElement>(null);
  function hasInvalidInput() { return Boolean(rootRef.current?.querySelector('[aria-invalid="true"]') || auxiliaryRef.current?.querySelector('[aria-invalid="true"]')); }
  const routeApplied = useRef<string | null>(null);
  const runKey = useRef<{ signature: string; key: string } | null>(null);
  const draft = drafts[selectedKey];
  const asset = catalog.assets.find(item => item.ref === selectedKey || item.ref === draft?.originalRef);
  const document = draft?.document ?? asset?.definition;
  const currentKind = draft?.kind ?? asset?.kind;
  const permissionFor = (value: LogicKind) => Boolean(session?.permissions.includes(value === "decision_table" ? "template_edit" : "propose"));
  const editable = Boolean(draft && !draft.submittedId && currentKind && permissionFor(currentKind));
  const canCreateLogic = Boolean(session?.permissions.some(value => ["propose", "template_edit"].includes(value)));
  const selectedSignature = document ? pretty(document) : "";
  const [validationSignature, setValidationSignature] = useState("");
  const previewRequest = useRef(0);

  // Discard the previous principal's workspace when the shared session changes.
  // eslint-disable-next-line react-hooks/set-state-in-effect
  useEffect(() => { setCatalog(emptyCatalog); setDefinitions([]); setSelectedKey(""); setDrafts({}); setPreview(null); setRuns([]); setError(""); routeApplied.current = null; }, [session?.principal]);
  useEffect(() => {
    if (!active || !session) return;
    const controller = new AbortController();
    // Reset feedback at the start of this abortable external request.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setLoading(true); setError("");
    void Promise.all([loadLogic(controller.signal), loadStudioSnapshot()]).then(([next, snapshot]) => { if (!controller.signal.aborted) { setCatalog(next); setDefinitions(snapshot.definitions); } }).catch(cause => { if (!controller.signal.aborted) setError(cause instanceof Error ? cause.message : "无法加载业务逻辑"); }).finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [active, session?.principal, refresh]);
  const routeBinding = params.get("binding") ?? "", routeKind = params.get("kind") ?? "";
  const routeAsset = params.get("asset") ?? "", routeDefinition = params.get("definition") ?? "", routeConcept = params.get("concept") ?? "", routeSubject = params.get("subject") ?? "";
  useEffect(() => {
    if (!active) return;
    const route = pretty([routeAsset, routeDefinition, routeConcept, routeBinding, routeKind, routeSubject]);
    if (routeApplied.current === route || !catalog.registry_fingerprint) return;
    if (routeAsset && !catalog.assets.some(item => item.ref === routeAsset) || routeDefinition && !catalog.assets.some(item => `${item.kind}:${item.id}` === routeDefinition)) return;
    routeApplied.current = route;
    // Apply each external deep link once, after its referenced assets are available.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    if (routeConcept || routeBinding) setConcept(routeConcept || routeBinding);
    if (kinds.includes(routeKind as LogicKind)) setKind(routeKind);
    const target = routeAsset || catalog.assets.find(item => `${item.kind}:${item.id}` === routeDefinition)?.ref;
    if (target && catalog.assets.some(item => item.ref === target)) setSelectedKey(target);
    if (routeSubject) { setSubjectId(routeSubject); setInputMode("instances"); setTab("validation"); }
  }, [active, routeAsset, routeDefinition, routeConcept, routeBinding, routeKind, routeSubject, catalog]);
  useEffect(() => {
    if (!active) return;
    function shortcut(event: KeyboardEvent) { if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "k") { event.preventDefault(); setCatalogCollapsed(false); requestAnimationFrame(() => searchRef.current?.focus()); } }
    window.addEventListener("keydown", shortcut); return () => window.removeEventListener("keydown", shortcut);
  }, [active]);
  useEffect(() => {
    // Initialize an asset's editing session only when its identity changes.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    selectedRef.current = selectedKey; setAuxiliaryClosed(false); if (selectedKey) setCatalogCollapsed(true); setPreview(null); setRuns([]); setError(""); runKey.current = null;
    const selected = drafts[selectedKey]?.document ?? catalog.assets.find(item => item.ref === selectedKey)?.definition;
    const selectedKind = drafts[selectedKey]?.kind ?? catalog.assets.find(item => item.ref === selectedKey)?.kind;
    setSampleText(pretty(selected && selectedKind ? sampleInput(selectedKind, selected, definitions) : {}));
    if (!routeSubject) setSubjectId("");
    setCandidateText(""); setContextIds({});
  }, [selectedKey]);
  const contextSignature = pretty(contextIds);
  // Invalidate pending external results when their input snapshot changes.
  // eslint-disable-next-line react-hooks/set-state-in-effect
  useEffect(() => { previewRequest.current += 1; setPreview(null); }, [selectedSignature, sampleText, inputMode, subjectId, candidateText, contextSignature]);
  useEffect(() => {
    if (!active || !session || tab !== "history") return;
    const controller = new AbortController();
    void loadLogicRuns(asset?.ref, routeSubject || undefined, controller.signal).then(value => { if (!controller.signal.aborted) setRuns(value); }).catch(cause => { if (!controller.signal.aborted) setError(String(cause)); });
    return () => controller.abort();
  }, [active, session?.principal, tab, asset?.ref, routeSubject, refresh]);

  function update(next: LogicDocument) { if (!editable || busy) return; setDrafts(previous => ({ ...previous, [selectedKey]: { ...previous[selectedKey], document: next, revision: previous[selectedKey].revision + 1 } })); setPreview(null); }
  function addDraft(value: LogicDocument, kind: LogicKind, originalRef?: string, assistance?: Assistance) { const key = `draft:${crypto.randomUUID()}`; setDrafts(previous => ({ ...previous, [key]: { kind, document: value, baseline: catalog.registry_fingerprint, originalRef, revision: 0, assistance } })); setSelectedKey(key); setTab("definition"); }
  function adoptSuggestion(kind: LogicKind, value: LogicDocument, assistance: Assistance, refine: boolean) {
    if (refine && draft && editable && draft.kind === kind) { setDrafts(previous => ({ ...previous, [selectedKey]: { ...previous[selectedKey], document: value, revision: previous[selectedKey].revision + 1, assistance } })); setPreview(null); }
    else addDraft(value, kind, refine && !draft ? asset?.ref : undefined, assistance);
    setKind(""); setTab("validation"); setAuxiliaryClosed(false);
    toast.success("已采纳助手草稿：请在白板中核对，并在“结果验证”中复核案例");
  }
  function openGuide(value: LogicKind = currentKind ?? (kinds.includes(kind as LogicKind) ? kind as LogicKind : "business_rule")) { setGuideKind(value); setGuideOpen(true); }
  function createExample(value: LogicKind) {
    if (!permissionFor(value) || loading || busy || !catalog.registry_fingerprint) return;
    const example = guideExample(value, catalog.assets, definitions);
    if (example.missing.length) { setError(`创建示例前需要：${example.missing.join("、")}`); return; }
    const document = { ...example.definition, id: `${example.definition.id}_${crypto.randomUUID().slice(0, 8)}` };
    addDraft(document, value); setInputMode("scenario"); setGuideOpen(false); setCreateOpen(false); setTab("validation");
  }
  function openCreate() {
    if (loading || busy || !canCreateLogic) return;
    setCreateKind(session?.permissions.includes("propose") ? "business_rule" : "decision_table");
    setCreateId(`logic_${crypto.randomUUID().slice(0, 8)}`); setCreateName(""); setCreateOpen(true);
  }
  function create() {
    let value = newLogic(createKind, createId, catalog.assets, definitions.filter(item => item.kind === "concept").map(item => item.id), definitions.filter(item => item.kind === "predicate").map(item => item.id));
    if (!expert && createKind === "decision_table") value = { ...value, inputs: [{ id: "condition_1", label: "业务条件", field: "condition_1", source: "facts", type: "number" }], outputs: [{ id: "result_1", label: "业务结果", type: "string" }], rules: [{ id: "rule_1", when: { condition_1: null }, then: { result_1: "" } }], test_cases: [] };
    if (createKind === "decision_model") value = { ...value, nodes: [], outputs: {}, test_cases: [] };
    addDraft({ ...value, label: createName.trim() || value.label }, createKind); setKind(""); setCreateOpen(false); setCreateId(""); setCreateName("");
  }
  function clone() { if (!asset) return; let version = bumpVersion(asset.version); const used = new Set([...catalog.assets.filter(item => item.kind === asset.kind && item.id === asset.id).map(item => item.version), ...Object.values(drafts).filter(item => item.kind === asset.kind && item.document.id === asset.id).map(item => item.document.version)]); while (used.has(version)) version = bumpVersion(version); addDraft({ ...structuredClone(asset.definition), version }, asset.kind, asset.ref); }
  function parseFacts() { const value = JSON.parse(sampleText); if (!value || typeof value !== "object" || Array.isArray(value)) throw new Error("试算输入须为 JSON 对象"); return !expert && document && currentKind ? completeScenario(currentKind, document, catalog.assets, definitions, value as Record<string, unknown>) : value as Record<string, unknown>; }
  function bindings(): EntityBindings { return { subject_id: subjectId, candidate_ids: candidateText.trim() ? candidateText.split(/[,，\n]/).map(value => value.trim()).filter(Boolean) : undefined, context_ids: contextIds }; }
  const canBind = currentKind === "business_rule" || currentKind === "business_constraint";
  const mode = canBind ? inputMode : "scenario";
  async function perform(action: () => Promise<void>) { setBusy(true); setError(""); try { if (hasInvalidInput()) throw new Error("请先修正尚未完整的 JSON 字段，再验证或提交。"); await action(); } catch (cause) { setError(cause instanceof Error ? cause.message : "操作失败"); } finally { setBusy(false); } }
  async function validate(withFacts = true) {
    if (!document || !currentKind) return;
    const key = selectedKey, signature = selectedSignature, request = ++previewRequest.current;
    const result = await previewLogic(currentKind, expert ? document : normalizeCaseNumbers(currentKind, document, catalog.assets), withFacts && mode === "scenario" ? parseFacts() : undefined, withFacts && mode === "instances" ? bindings() : undefined);
    if (selectedRef.current === key && request === previewRequest.current) { setPreview(previous => !withFacts && previous?.result ? { ...result, result: previous.result, source_refs: previous.source_refs } : result); setValidationSignature(signature); if (!withFacts) toast.success(result.tests.passed ? "回归案例全部通过" : "已完成校验，请检查案例结果"); }
  }
  async function submit() {
    if (!draft || !currentKind) return;
    const submittedDefinition = expert ? draft.document : normalizeCaseNumbers(currentKind, draft.document, catalog.assets);
    const check = await previewLogic(currentKind, submittedDefinition);
    setPreview(check);
    if (!check.tests.passed) throw new Error("提交前需要至少一个案例，且所有案例通过。请在“验证与试算”维护预期结果。");
    const key = selectedKey;
    const result = await proposeLogic(currentKind, submittedDefinition, reason.trim(), draft.baseline, `logic:${key}:${draft.revision}`, draft.assistance);
    setDrafts(previous => ({ ...previous, [key]: { ...previous[key], submittedId: result.id } })); setSubmitOpen(false); setReason(""); toast.success("已提交独立审核"); router.push(`/changesets?${new URLSearchParams({ id: result.id })}`);
  }
  async function recordRun() {
    if (!asset || draft) return;
    const facts = mode === "scenario" ? parseFacts() : undefined, instanceBindings = mode === "instances" ? bindings() : undefined;
    const signature = pretty([asset.ref, facts, instanceBindings]);
    if (runKey.current?.signature !== signature) runKey.current = { signature, key: crypto.randomUUID() };
    const result = await runLogic(asset.ref, runKey.current!.key, facts, instanceBindings);
    runKey.current = null;
    setRuns(previous => [result, ...previous.filter(item => item.id !== result.id)]); setTab("history"); toast.success("已记录决策运行，可按快照回放"); setRefresh(value => value + 1);
  }
  const visible = filterLogic(catalog.assets, query, kind, lifecycle, concept);
  const boundScope = object(document?.scope);
  const scenario = (() => { try { return object(JSON.parse(sampleText)); } catch { return {}; } })();
  function toggleExpert() { if (hasInvalidInput()) { setError("请先修正尚未完整的配置，再切换模式。"); return; } setExpert(value => !value); }
  function openTab(value: string) { if (hasInvalidInput()) { setError("请先修正尚未完整的配置。"); return; } setTab(value); setAuxiliaryClosed(false); setCatalogCollapsed(true); }
  const auxiliaryOpen = tab !== "definition" && !auxiliaryClosed;
  const sideOpen = tab === "definition" && expert;
  function closeAuxiliary() { if (hasInvalidInput()) { setError("请先修正尚未完整的配置。"); return; } setAuxiliaryClosed(true); }
  function toggleAuxiliary(value: string) { if (auxiliaryOpen && tab === value) closeAuxiliary(); else openTab(value); }
  const selectedLabel = asset && !draft ? assetLabel(asset) : String(document?.label ?? document?.id ?? "");
  const selectedStatus = draft ? draft.submittedId ? "已提交审核" : draft.assistance ? "助手起草的会话草稿" : "会话草稿" : logicStatusLabels[asset?.lifecycle ?? "active"];
  const panelTitle = { definition: "定义与配置", guide: "说明与样例", validation: "结果验证", relations: "关联与使用", history: "运行记录" }[tab] ?? "业务逻辑";
  const selectedDrafts = Object.entries(drafts).filter(([, value]) => !kind || value.kind === kind);
  const renderPanelBody = (document: LogicDocument, currentKind: LogicKind) => (
            <div className="logic-detail-body">
            {tab === "definition" && expert && <><LogicTypeSummary kind={currentKind} onRead={() => setTab("guide")} /><fieldset disabled={!editable || busy} className="logic-edit-fields"><div className="logic-two-columns"><label className="logic-field">稳定标识<Input aria-label="业务逻辑标识" value={document.id} disabled={Boolean(draft?.originalRef)} onChange={event => update({ ...document, id: event.target.value })} /></label><label className="logic-field">版本<Input aria-label="业务逻辑版本" value={document.version} onChange={event => update({ ...document, version: event.target.value })} /></label></div><label className="logic-field">名称<Input aria-label="业务逻辑名称" value={document.label ?? ""} onChange={event => update({ ...document, label: event.target.value })} /></label><label className="logic-field">业务目的<Textarea aria-label="业务逻辑目的" value={document.description ?? ""} onChange={event => update({ ...document, description: event.target.value })} /></label>{currentKind !== "decision_table" && <label className="logic-field">发布状态<select aria-label="业务逻辑发布状态" value={String(object(document.status).lifecycle ?? "active")} onChange={event => update({ ...document, status: { lifecycle: event.target.value } })}>{["active", "draft", "deprecated"].map(value => <option key={value} value={value}>{logicStatusLabels[value]}</option>)}</select></label>}<LogicEditor kind={currentKind} value={document} definitions={definitions} assets={catalog.assets} onChange={update} /><details><summary>完整规范定义（专家）</summary><JsonField label="完整规范定义" value={document} rows={16} onChange={next => update(object(next) as LogicDocument)} /></details></fieldset>{!draft && <p className="logic-note mt-5">已登记资产通过“创建新版本”维护；已发布的引用和运行记录保留原有版本。</p>}<details className="logic-source"><summary>规范定义与来源</summary><p>{asset?.source_path ?? "提交后由服务端确定真源路径"}</p><pre>{pretty(document)}</pre></details></>}
            {tab === "guide" && <LogicGuideContent expert={expert} kind={currentKind} assets={catalog.assets} definitions={definitions} canCreate={permissionFor(currentKind) && !loading && !busy} onCreate={() => createExample(currentKind)} />}
            {tab === "validation" && <><p className="logic-input-help"><BookOpen size={14} /><span>{(expert ? logicGuides : businessLogicGuides)[currentKind].input}</span><button onClick={() => setTab("guide")}>查看输入示例</button></p><div className="logic-section-heading"><div><h3>场景与实例试算</h3><p>预览不写入事实或关系。设备适用、推荐选择与实际指派分别记录。</p></div><FlaskConical size={22} /></div>{canBind && <div className="logic-segment"><button aria-pressed={inputMode === "scenario"} onClick={() => setInputMode("scenario")}>场景参数</button><button aria-pressed={inputMode === "instances"} onClick={() => setInputMode("instances")}>绑定真实实例</button></div>}
              {mode === "scenario" ? <ScenarioForm expert={expert} kind={currentKind} value={document} assets={catalog.assets} definitions={definitions} input={sampleText} onChange={setSampleText} /> : <div className="logic-instance-bindings"><InstancePicker label="主体实例" concept={String(boundScope.subject_concept)} value={subjectId} onChange={setSubjectId} />{currentKind === "business_rule" && <label className="logic-field">候选实例 ID（留空评估全部，最多 500 个）<Textarea aria-label="候选实例 ID" value={candidateText} placeholder="以逗号或换行分隔" onChange={event => setCandidateText(event.target.value)} /></label>}{Object.entries(object(boundScope.context_concepts)).map(([role, concept]) => <InstancePicker key={role} label={`上下文实例 ${role}`} concept={String(concept)} value={contextIds[role] ?? ""} onChange={id => setContextIds(previous => { const next = { ...previous }; if (id) next[role] = id; else delete next[role]; return next; })} />)}{subjectId && <Link className="logic-link" href={`/instances?${new URLSearchParams({ id: subjectId })}`}>查看主体事实与来源<ArrowUpRight size={14} /></Link>}</div>}
              <div className="flex flex-wrap gap-2"><Button size="sm" disabled={busy || mode === "instances" && !subjectId} onClick={() => void perform(() => validate())}>{busy ? <Loader2 className="animate-spin" /> : <FlaskConical />}试算当前定义</Button>{!draft && asset?.lifecycle === "active" && session?.permissions.includes("agent") && <Button size="sm" variant="outline" disabled={busy || mode === "instances" && !subjectId} onClick={() => void perform(recordRun)}><History />记录本次运行</Button>}</div>
              {preview?.result && validationSignature === selectedSignature && <Result value={preview.result} expert={expert} definitions={definitions} document={document} />}{expert && preview?.source_refs != null && Object.keys(object(preview.source_refs)).length > 0 && <details className="logic-source"><summary>本次输入的事实来源与证据</summary><pre>{pretty(preview.source_refs)}</pre></details>}
              <LogicCases kind={currentKind} value={document} assets={catalog.assets} definitions={definitions} editable={editable} busy={busy} expert={expert} preview={preview} onChange={update} onLoad={input => { setSampleText(pretty(input)); setInputMode("scenario"); }} onValidate={() => void perform(() => validate(false))} onAdd={() => void perform(async () => update({ ...document, test_cases: [...(document.test_cases ?? []), { id: `case_${crypto.randomUUID().slice(0, 8)}`, label: `业务案例 ${(document.test_cases?.length ?? 0) + 1}`, input: parseFacts(), expected: expectedShape(currentKind, document, catalog.assets) }] }))} />
            </>}
            {tab === "relations" && <><div className="logic-section-heading"><div><h3>语义与实例</h3><p>从规则追溯到本体定义，或查看关联实例。</p></div><LibraryBig size={21} /></div>{(asset?.bindings ?? []).map(id => <div className="logic-related" key={id}><strong>{definitions.find(item => item.id === id)?.label ?? id}</strong>{expert && <code>{id}</code>}<Link href={`/?${new URLSearchParams({ definition: id })}`}>本体定义<ArrowUpRight size={13} /></Link>{definitions.find(item => item.id === id)?.kind === "concept" && <Link href={`/instances?${new URLSearchParams({ type: id })}`}>浏览实例<ArrowUpRight size={13} /></Link>}</div>)}<div className="logic-section-heading"><div><h3>被谁使用</h3><p>已发布决策与工艺模板引用固定版本；更新后由使用方显式升级。</p></div></div>{asset?.used_by.length ? asset.used_by.map(use => <Link className="logic-related" key={use.id} href={use.href}><GitBranch size={17} /><strong>{use.label}</strong>{expert && <code>{use.id}</code>}<ArrowUpRight size={15} /></Link>) : <p className="logic-note">尚无已发布的决策模型或工艺模板引用此版本。</p>}{currentKind === "decision_model" && records(document.nodes).map(node => catalog.assets.some(item => item.ref === node.ref) ? <Link className="logic-related" key={String(node.id)} href={logicHref({ asset: String(node.ref) })}><strong>{String(node.label ?? (catalog.assets.find(item => item.ref === node.ref) ? assetLabel(catalog.assets.find(item => item.ref === node.ref)!) : fieldLabel(String(node.id))))}</strong>{expert && <code>{String(node.ref)}</code>}<ArrowUpRight size={15} /></Link> : <details className="logic-source" key={String(node.id)}><summary>{expert ? `${node.id} · ${node.ref}` : String(node.label ?? fieldLabel(String(node.id)))} · 已保存的引用版本</summary><p>当前目录已更新，此决策仍使用发布时的版本。</p>{expert ? <pre>{pretty(node.snapshot)}</pre> : <p className="logic-note">此步骤保留原发布版本，需升级引用才能使用新版。</p>}</details>)}<Link className="logic-link mt-5" href="/templates">前往工艺工作台引用决策表<ArrowUpRight size={14} /></Link></>}
            {tab === "history" && <><div className="logic-section-heading"><div><h3>运行记录与回放</h3><p>保留规则、单位、输入与来源快照。场景测算与真实实例评估均不产生实际设备指派。</p></div><History size={21} /></div>{runs.map(run => <article className="logic-run-card" key={run.id}><div className="flex flex-wrap items-center gap-2"><strong>{logicStatusLabels[String(run.record.result.status)]}</strong><span className="neutral-badge">{run.record.mode === "instance_facts" ? "实例事实评估" : "场景测算"}</span><small>{displayDate(run.recorded_at)}</small></div>{expert && <><code>{run.asset_ref}</code><p className="logic-note">{run.actor} · {run.id}</p></>}<Button size="sm" variant="outline" disabled={busy} onClick={() => void perform(async () => { const result = await replayLogic(run.id); if (!result.matches_recorded) throw new Error("回放结果与原记录不一致"); toast.success("快照回放与原始记录一致"); })}><History />按快照回放</Button><Result value={run.record.result} expert={expert} definitions={definitions} document={run.record.definition} />{expert && <details><summary>输入、定义与来源快照</summary><pre>{pretty(run.record)}</pre></details>}{run.record.entity_bindings?.subject_id && <Link className="logic-link" href={`/instances?${new URLSearchParams({ id: run.record.entity_bindings.subject_id })}`}>查看主体实例<ArrowUpRight size={14} /></Link>}</article>)}{!runs.length && <p className="logic-note">暂无已记录运行。可在已启用版本的“验证与试算”中记录。</p>}</>}
          </div>
  );
  return <div ref={rootRef} className="rules-workspace" data-expert={expert} aria-label="业务规则与决策工作台">
    {!session ? <><div className="workspace-navigation-access"><NavigationToggle /></div><div className="logic-empty"><GitBranch size={32} strokeWidth={1.3} /><h1>业务规则与决策</h1><Button variant="outline" onClick={() => openGuide("business_rule")}><BookOpen />工作台说明与样例</Button><p>从左下角账户入口连接知识库，维护实例匹配、约束、公式与决策。</p><div className="logic-empty-chain"><span>定义语义</span><ArrowUpRight /><span>维护逻辑</span><ArrowUpRight /><span>实例试算</span><ArrowUpRight /><span>审核发布</span></div></div></> : <>
      {error && !auxiliaryOpen && <div role="alert" className="instance-error"><p>{error}</p><Button size="sm" variant="ghost" onClick={() => setError("")}>关闭提示</Button></div>}
      <div className="logic-layout" data-catalog-collapsed={catalogCollapsed} data-detail-open={Boolean(document)}><aside id="logic-directory" className="logic-directory"><div className="logic-directory-head"><div className="directory-heading"><LogicToolHint label="收起规则与决策侧边栏"><Button variant="ghost" size="icon-sm" aria-label="收起规则与决策侧边栏" onClick={() => setCatalogCollapsed(true)}><PanelLeftClose /></Button></LogicToolHint><h1>规则与决策</h1><Button variant="ghost" size="icon-sm" aria-label="刷新业务逻辑目录" disabled={loading || busy} onClick={() => setRefresh(value => value + 1)}><RefreshCw className={loading ? "animate-spin" : ""} /></Button></div><p>维护业务逻辑，连接事实与行动</p><div className="instance-search-input"><Search size={16} /><Input ref={searchRef} aria-label="搜索业务逻辑" placeholder="名称、标识、业务目的…" value={query} onChange={event => setQuery(event.target.value)} /></div>
        <div className="logic-categories"><button aria-pressed={!kind} onClick={() => setKind("")}>全部<span>{catalog.assets.length}</span></button>{kinds.map(value => <button key={value} aria-pressed={kind === value} onClick={() => setKind(value)}>{logicKindLabels[value]}<span>{catalog.assets.filter(item => item.kind === value).length}</span></button>)}</div>
        <div className="logic-two-columns"><select aria-label="逻辑生命周期" value={lifecycle} onChange={event => setLifecycle(event.target.value)}><option value="">全部状态</option>{["active", "draft", "deprecated", "retired"].map(value => <option key={value} value={value}>{logicStatusLabels[value]}</option>)}</select><select aria-label="逻辑关联概念" value={concept} onChange={event => setConcept(event.target.value)}><option value="">全部概念</option>{definitions.filter(item => item.kind === "concept").map(item => <option key={item.id} value={item.id}>{item.label}</option>)}</select></div>
        <div className="logic-two-columns"><Button size="sm" disabled={loading || busy || !canCreateLogic} onClick={openCreate}><Plus />新建业务逻辑</Button><Button size="sm" variant="outline" disabled={loading || busy || !canCreateLogic} onClick={() => setAssistantOpen(true)}><Sparkles />助手起草</Button></div></div>
        <div className="logic-asset-list">{selectedDrafts.length > 0 && <div className="logic-list-label">当前会话草稿 · {selectedDrafts.length}</div>}{selectedDrafts.map(([key, value]) => <button key={key} aria-pressed={selectedKey === key} onClick={() => { setSelectedKey(key); setTab("definition"); }}><span><i data-kind={value.kind} />{logicKindLabels[value.kind]}<small>{value.submittedId ? "已提交" : "待提交"}{value.assistance ? " · 助手起草" : ""}</small></span><strong>{String(value.document.label ?? value.document.id)}</strong>{expert && <code>{value.document.id} · {value.document.version}</code>}</button>)}
          <div className="logic-list-label">已登记资产 · {visible.length}</div>{visible.map(item => <button key={item.ref} aria-pressed={selectedKey === item.ref} onClick={() => { setSelectedKey(item.ref); setTab(routeSubject ? "validation" : "definition"); }}><span><i data-kind={item.kind} />{logicKindLabels[item.kind]}<small>{logicStatusLabels[item.lifecycle] ?? item.lifecycle}</small></span><strong>{assetLabel(item)}</strong>{expert && <code>{item.id} · {item.version}</code>}<span className="logic-asset-meta">{item.used_by.length} 处引用 · {item.definition.test_cases?.length ?? 0} 个案例</span></button>)}{!loading && !visible.length && <p className="logic-list-label">没有匹配的业务逻辑</p>}</div>
        <div className="logic-directory-foot"><button className="logic-help-entry" aria-pressed={expert} onClick={toggleExpert}><Code2 size={15} />{expert ? "返回业务模式" : "开启专家模式"}</button><button className="logic-help-entry" onClick={() => openGuide()}><BookOpen size={15} />工作台说明与样例<ArrowUpRight size={14} /></button><Link href="/changesets"><ShieldCheck size={15} />统一变更审核<ArrowUpRight size={14} /></Link><span>草稿保留在当前页面会话</span></div></aside>
        {document && currentKind ? <main className="logic-detail" data-panel-open={sideOpen} aria-label={selectedLabel}>
          <LogicBoard key={selectedKey} boardId={`${session.principal}:${selectedKey}`} kind={currentKind} value={document} assets={catalog.assets} definitions={definitions} editable={editable && !busy && !expert} onChange={update} scenario={scenario} onScenarioChange={next => { setSampleText(pretty(next)); setInputMode("scenario"); }} result={preview?.result} />
          <div className="canvas-dock logic-workspace-tools" role="toolbar" aria-label="规则白板工具" aria-orientation="vertical">
            <LogicToolHint label={catalogCollapsed ? "规则白板 · 展开规则与决策" : "规则白板 · 收起规则与决策"}><Button size="icon-sm" variant={!catalogCollapsed ? "secondary" : "ghost"} aria-label="规则白板" aria-pressed={!catalogCollapsed} aria-expanded={!catalogCollapsed} aria-controls="logic-directory" onClick={() => setCatalogCollapsed(value => !value)}><GitBranch /></Button></LogicToolHint>
            <LogicToolHint label="新建业务逻辑" disabled={loading || busy || !canCreateLogic}><Button size="icon-sm" variant="ghost" aria-label="新建业务逻辑" disabled={loading || busy || !canCreateLogic} onClick={openCreate}><Plus /></Button></LogicToolHint>
            <LogicToolHint label="助手起草" disabled={loading || busy || !canCreateLogic}><Button size="icon-sm" variant={assistantOpen ? "secondary" : "ghost"} aria-label="助手起草" disabled={loading || busy || !canCreateLogic} onClick={() => setAssistantOpen(true)}><Sparkles /></Button></LogicToolHint>
            <span className="canvas-dock-separator" />
            {auxiliaryViews.map(({ id, label, Icon }) => <LogicToolHint key={id} label={label}><Button size="icon-sm" variant={auxiliaryOpen && tab === id ? "secondary" : "ghost"} aria-label={label} aria-pressed={auxiliaryOpen && tab === id} aria-expanded={auxiliaryOpen && tab === id} aria-controls="logic-auxiliary-content" aria-haspopup="dialog" onClick={() => toggleAuxiliary(id)}><Icon /></Button></LogicToolHint>)}
            <span className="canvas-dock-separator" />
            {!draft && permissionFor(currentKind) && <LogicToolHint label="创建新版本" disabled={busy}><Button size="icon-sm" variant="ghost" aria-label="创建新版本" disabled={busy} onClick={clone}><Copy /></Button></LogicToolHint>}
            {editable && <LogicToolHint label="提交审核" disabled={busy || loading}><Button size="icon-sm" variant="ghost" aria-label="提交审核" disabled={busy || loading} onClick={() => { setReason(""); setSubmitOpen(true); }}><Send /></Button></LogicToolHint>}
            <DropdownMenu><LogicToolHint label="更多规则工作台操作"><DropdownMenuTrigger asChild><Button size="icon-sm" variant="ghost" aria-label="更多规则工作台操作"><MoreHorizontal /></Button></DropdownMenuTrigger></LogicToolHint><DropdownMenuContent side="right" align="end" className="w-56">
              <DropdownMenuLabel>{expert ? "专家模式" : "业务模式"}</DropdownMenuLabel>
              <DropdownMenuItem onSelect={toggleExpert}><Code2 />{expert ? "切换为业务模式" : "切换为专家模式"}</DropdownMenuItem>
              {expert && <DropdownMenuItem onSelect={() => openTab("definition")}><Code2 />定义与配置</DropdownMenuItem>}
              <DropdownMenuItem onSelect={() => openGuide()}><BookOpen />工作台说明与样例</DropdownMenuItem>
              <DropdownMenuSeparator /><DropdownMenuItem disabled={busy || loading} onSelect={() => setRefresh(value => value + 1)}><RefreshCw />刷新目录</DropdownMenuItem>
              <DropdownMenuItem asChild><Link href="/changesets"><ShieldCheck />统一变更审核</Link></DropdownMenuItem>
              {draft?.submittedId && <DropdownMenuItem asChild><Link href={`/changesets?${new URLSearchParams({ id: draft.submittedId })}`}><ArrowUpRight />查看当前提案</Link></DropdownMenuItem>}
            </DropdownMenuContent></DropdownMenu>
          </div>
          <DropdownMenu><DropdownMenuTrigger asChild><button className="logic-canvas-context" aria-label="当前逻辑与业务说明" title={selectedLabel}><span><strong>{selectedLabel}</strong><small>{logicKindLabels[currentKind]} · {selectedStatus} · v{document.version}{expert ? " · 专家模式" : ""}</small></span><ChevronDown size={15} /></button></DropdownMenuTrigger><DropdownMenuContent side="bottom" align="end" className="logic-context-menu">
            <DropdownMenuLabel>{selectedLabel}</DropdownMenuLabel><p className="logic-context-description">{document.description ?? "独立版本的业务逻辑"}</p>{expert && <p className="logic-context-description">{draft ? document.id : asset?.ref}</p>}
            <DropdownMenuSeparator /><DropdownMenuItem onSelect={() => setCatalogCollapsed(false)}><PanelLeftOpen />切换业务逻辑</DropdownMenuItem><DropdownMenuItem onSelect={() => openTab("guide")}><BookOpen />说明与样例</DropdownMenuItem>
          </DropdownMenuContent></DropdownMenu>
          {sideOpen && <section className="logic-task-panel" data-placement="side" aria-label={panelTitle}>
            <header className="logic-task-panel-head"><div><h2>{panelTitle}</h2><p>{selectedLabel}</p></div><LogicToolHint side="top" label="返回规则白板"><Button size="icon-sm" variant="ghost" aria-label="返回规则白板" onClick={() => { if (hasInvalidInput()) { setError("请先修正尚未完整的配置。"); return; } setExpert(false); }}><X /></Button></LogicToolHint></header>
            {renderPanelBody(document, currentKind)}
          </section>}
          <Dialog open={auxiliaryOpen} onOpenChange={open => { if (!open) closeAuxiliary(); }}>
            <DialogContent id="logic-auxiliary-content" ref={auxiliaryRef} className="logic-auxiliary-dialog" showCloseButton={false} onCloseAutoFocus={event => { event.preventDefault(); rootRef.current?.querySelector<HTMLButtonElement>(`.logic-workspace-tools button[aria-label="${panelTitle}"]`)?.focus(); }}>
              <header className="logic-auxiliary-dialog-head"><DialogHeader><DialogTitle>{panelTitle}</DialogTitle><DialogDescription>{selectedLabel}</DialogDescription></DialogHeader><Button size="icon-sm" variant="ghost" aria-label="关闭窗口" title="关闭窗口" onClick={closeAuxiliary}><X /></Button></header>
              <div className="logic-auxiliary-dialog-tabs" role="group" aria-label="切换辅助内容">{auxiliaryViews.map(({ id, label, Icon }) => <button key={id} aria-pressed={tab === id} onClick={() => openTab(id)}><Icon size={15} /><span>{label}</span></button>)}</div>
              {error && <div className="logic-auxiliary-dialog-error" role="alert">{error}</div>}
              {renderPanelBody(document, currentKind)}
            </DialogContent>
          </Dialog>
        </main> : <div className="logic-empty"><div className="canvas-dock" role="toolbar" aria-label="规则目录工具"><LogicToolHint label={catalogCollapsed ? "规则白板 · 展开规则与决策" : "规则白板 · 收起规则与决策"}><Button variant={!catalogCollapsed ? "secondary" : "ghost"} size="icon-sm" aria-label="规则白板" aria-pressed={!catalogCollapsed} aria-expanded={!catalogCollapsed} aria-controls="logic-directory" onClick={() => setCatalogCollapsed(value => !value)}><GitBranch /></Button></LogicToolHint><LogicToolHint label="新建业务逻辑" disabled={loading || busy || !canCreateLogic}><Button variant="ghost" size="icon-sm" aria-label="新建业务逻辑" disabled={loading || busy || !canCreateLogic} onClick={openCreate}><Plus /></Button></LogicToolHint><LogicToolHint label="助手起草" disabled={loading || busy || !canCreateLogic}><Button variant="ghost" size="icon-sm" aria-label="助手起草" disabled={loading || busy || !canCreateLogic} onClick={() => setAssistantOpen(true)}><Sparkles /></Button></LogicToolHint></div><div className="logic-welcome-icons"><GitBranch /><Table2 /><ShieldCheck /></div><h2>{loading ? "正在连接业务逻辑…" : "让每次决策都有依据"}</h2><p>选择一项规则、约束或决策模型，查看适用条件、<br />验证实例差异，并追溯版本和使用关系。</p><div className="logic-empty-chain"><span>本体定义</span><ArrowUpRight /><span>业务逻辑</span><ArrowUpRight /><span>工艺与实例</span></div><Button variant="outline" onClick={() => openGuide("business_rule")}><BookOpen />五类逻辑怎么选</Button><Button variant="outline" disabled={loading || !session.permissions.includes("template_edit")} onClick={() => { const id = `size_selection_${Date.now().toString(36)}`; addDraft(newLogic("decision_table", id, catalog.assets, [], []), "decision_table"); }}>体验不同尺寸选设备</Button><p className="logic-note">示例为虚构场景参数，提交前可自由调整。</p></div>}
      </div>
    </>}
    <Dialog open={createOpen} onOpenChange={setCreateOpen}><DialogContent className="logic-create-dialog sm:max-w-2xl"><DialogHeader><DialogTitle>新建业务逻辑</DialogTitle><DialogDescription>选择要解决的问题，填写业务名称，即可开始可视化编制。</DialogDescription></DialogHeader><div className="business-kind-choices" role="group" aria-label="选择业务逻辑类型">{kinds.filter(permissionFor).map(value => <button key={value} aria-pressed={createKind === value} onClick={() => setCreateKind(value)}><strong>{logicKindLabels[value]}</strong><span>{logicGuides[value].question}</span></button>)}</div><label className="business-value-field"><span>业务名称</span><Input autoFocus aria-label="新建业务逻辑名称" value={createName} onChange={event => setCreateName(event.target.value)} placeholder="例如：按尺寸选择注塑机" /></label>{expert && <label className="logic-field">稳定标识<Input aria-label="新建业务逻辑标识" value={createId} onChange={event => setCreateId(event.target.value)} /></label>}<LogicTypeSummary kind={createKind} onRead={() => { setCreateOpen(false); openGuide(createKind); }} /><Button disabled={!createName.trim() || !/^[a-z][a-z0-9_]{0,63}$/.test(createId) || catalog.assets.some(item => item.kind === createKind && item.id === createId)} onClick={create}>开始编制</Button></DialogContent></Dialog>
    {session && <RulesAssistant open={assistantOpen} onOpenChange={setAssistantOpen} current={document && currentKind && (editable || !draft) ? { kind: currentKind, definition: document, label: selectedLabel } : null} canAuthor={permissionFor} onAdopt={adoptSuggestion} />}
    <LogicGuideDialog expert={expert} open={guideOpen} onOpenChange={setGuideOpen} kind={guideKind} onKindChange={setGuideKind} assets={catalog.assets} definitions={definitions} canCreate={permissionFor(guideKind) && !loading && !busy} onCreate={() => createExample(guideKind)} />
    <Dialog open={submitOpen} onOpenChange={open => { if (!busy) setSubmitOpen(open); }}><DialogContent><DialogHeader><DialogTitle>提交业务逻辑审核</DialogTitle><DialogDescription>服务端重新校验版本、引用和全部案例。高风险变更需要另一位审核人批准。</DialogDescription></DialogHeader><label className="logic-field">变更原因<Textarea aria-label="业务逻辑变更原因" value={reason} onChange={event => setReason(event.target.value)} /></label><Button disabled={busy || !reason.trim()} onClick={() => void perform(submit)}>{busy ? <Loader2 className="animate-spin" /> : <Send />}校验并提交</Button>{error && <p role="alert" className="text-sm text-destructive">{error}</p>}</DialogContent></Dialog>
  </div>;
}
