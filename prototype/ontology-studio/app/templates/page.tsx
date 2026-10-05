"use client";

import { useMemo, useRef, useState } from "react";
import Link from "next/link";
import { ArrowLeft, ArrowRight, Check, CheckCheck, CircleAlert, ClipboardPaste, Copy, FilePlus2, FlaskConical,
  Focus, GitBranch, GripVertical, Layers3, LibraryBig, Loader2, LockKeyhole, PanelLeftClose, Plus,
  Redo2, RefreshCw, Save, Search, Settings2, ShieldCheck, SlidersHorizontal, Trash2, Undo2, Workflow, X, Table2, Calculator, Diamond, ChevronDown, ChevronLeft, ChevronRight, ListTree } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Toaster } from "@/components/ui/sonner";
import { applyChangeSet, loadSession, publishChangeSet, reviewChangeSet, setSessionToken, type UserSession } from "../knowledgeos-api";
import { BatchProperties } from "./batch-properties";
import { ConditionEditor, FactRows, OperationEditor } from "./editor-fields";
import {DecisionEditor,DecisionBindingEditor} from './decision-editor';
import {emptyDecision} from './decision-editing';
import {workbenchExample} from './workbench-example';
import { RouteCanvas, type RouteFlow, type RoutePositions } from "./route-canvas";
import { connectRoute, duplicateRouteAfter, duplicateRouteItems, insertRoutePlan, parseRoutePlan, patchRouteSteps, routeOutline, selectedRouteSteps, insertRouteSequence, moveRouteStep, nextRouteKey, removeRouteItems, routeScope } from "./route-editing";
import { loadTemplate, loadTemplateCatalog, loadTemplateChangeSets, previewTemplate, submitTemplate, calculateTemplate,
  type ExpertTemplate, type OperationSummary, type Preview, type RouteGroup, type RouteStep,
  type SampleCase, type TemplateChangeSet, type TemplateSummary, type DecisionTable, type TimeModel, type CostScenario, type RouteCost } from "./template-api";

type Panel = "outline" | "templates" | "operations" | "properties" | "cases" | "governance" | "settings" | "sequence" | "decisions" | "costs";
type Catalog = { templates: TemplateSummary[]; operations: OperationSummary[]; units: string[]; models: TimeModel[]; quantity_models:TimeModel[]; decisions:DecisionTable[];currencies:string[] };
const emptyCatalog: Catalog = { templates: [], operations: [], units: [], models: [],quantity_models:[],decisions:[],currencies:[] };
const panelNames: Record<Panel, string> = { outline: "工艺目录", templates: "路线模板", operations: "标准工序库", properties: "工序属性", cases: "案例试展开", governance: "审核发布", settings: "模板设置", sequence: "批量添加工序", decisions:"决策表",costs:"成本试算" };
const statusNames: Record<string, string> = { proposed: "已提交", review_required: "待审核", approved: "已批准", changes_requested: "需修改", published: "已发布", rejected: "已退回" };
type HistoryEntry = { template: ExpertTemplate; positions: RoutePositions };

function blankTemplate(catalog?: Catalog, reserved: string[] = []): ExpertTemplate {
  return { id: nextRouteKey("route", [...(catalog?.templates.map((item) => item.key) ?? []), ...reserved]), version: "1.0.0", label: "新标准路线", family: "general", output_basis: "piece", summary: "",
    groups: [], steps: [], edges: [], operations: [],
    cases: [{ id: "basic", facts: {}, sets: {}, expected_operations: 0 }] };
}

function sameIds(a: string[], b: string[]) { return a.length === b.length && a.every((id) => b.includes(id)); }

export default function ExpertTemplatePage() {
  const [session, setSession] = useState<UserSession | null>(null);
  const [tokenInput, setTokenInput] = useState("");
  const [catalog, setCatalog] = useState<Catalog>(emptyCatalog);
  const [changesets, setChangesets] = useState<TemplateChangeSet[]>([]);
  const [template, setTemplate] = useState<ExpertTemplate>(blankTemplate);
  const templateRef = useRef(template);
  const outlineInput = useRef<HTMLInputElement>(null);
  const [sourceTemplate, setSourceTemplate] = useState<ExpertTemplate | null>(null);
  const [readOnly, setReadOnly] = useState(false);
  const [proposalView, setProposalView] = useState(false);
  const [panel, setPanel] = useState<Panel | null>(null);
  const [selectedIds, setSelectedIds] = useState<string[]>([]);
  const [selectedEdges, setSelectedEdges] = useState<string[]>([]);
  const [caseId, setCaseId] = useState("basic");
  const [preview, setPreview] = useState<Preview | null>(null);
  const [decisionIndex,setDecisionIndex]=useState(0);
  const [costScenario,setCostScenario]=useState<CostScenario>({quantity:'100',currency:'CNY',margin:'0',operating_ratio:'0',rates:{}});
  const [costResult,setCostResult]=useState<RouteCost|null>(null);
  const [reason, setReason] = useState("专家确认的标准工艺路线");
  const [newSet, setNewSet] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [draftId, setDraftId] = useState<string | null>(null);
  const [operationSearch, setOperationSearch] = useState("");
  const [libraryMode, setLibraryMode] = useState<"insert" | "replace" | "batch">("insert");
  const [operationEditor, setOperationEditor] = useState<number | null>(null);
  const [sequenceText, setSequenceText] = useState("");
  const [collapsedGroups, setCollapsedGroups] = useState<string[]>([]);
  const [scopeSearch, setScopeSearch] = useState("");
  const [outlineSearch, setOutlineSearch] = useState("");
  const [layoutRevision, setLayoutRevision] = useState(0);
  const [flow, setFlow] = useState<RouteFlow | null>(null);
  const history = useRef<{ past: HistoryEntry[]; future: HistoryEntry[]; key?: string }>({ past: [], future: [] });
  const dragStart = useRef<HistoryEntry | null>(null);
  const [restoredPositions, setRestoredPositions] = useState<RoutePositions | null>(null);
  const [historyCounts, setHistoryCounts] = useState({ past: 0, future: 0 });

  const canEdit = Boolean(session?.permissions.includes("template_edit"));
  const canReview = Boolean(session?.permissions.includes("template_review"));
  const canPublish = Boolean(session?.permissions.includes("template_publish"));
  const editable = canEdit && !readOnly;
  const selected = selectedIds.at(-1) ?? "";
  const selectedGroup = template.groups.find((item) => item.id === selected);
  const selectedStep = template.steps.find((item) => item.id === selected);
  const currentCase = template.cases.find((item) => item.id === caseId) ?? template.cases[0];
  const allItems = [...template.groups, ...template.steps];
  const opChoices = useMemo(() => {
    const options = new Map(catalog.operations.map((item) => [item.id, item]));
    for (const item of template.operations) options.set(`operation:${item.id}:${item.version}`, { ...item, id: `operation:${item.id}:${item.version}` });
    return [...options.values()];
  }, [catalog.operations, template.operations]);
  const filteredOperations = opChoices.filter((item) => `${item.label} ${item.method ?? ""} ${item.id}`.toLowerCase().includes(operationSearch.toLowerCase()));
  const decisionChoices=useMemo(()=>{const tables=new Map(catalog.decisions.map(t=>[`decision:${t.id}:${t.version}`,t]));for(const t of template.decisions??[])tables.set(`decision:${t.id}:${t.version}`,t);return[...tables.values()];},[catalog.decisions,template.decisions]);
  const resourceChoices=useMemo(()=>{const refs=new Map<string,string>();const refsUsed=new Set(template.steps.map(s=>s.operation_ref));for(const step of template.steps){const binding=step.decision_binding;if(binding?.purpose!=='operation')continue;const table=decisionChoices.find(t=>`decision:${t.id}:${t.version}`===binding.ref);for(const output of [...(table?.rules.map(r=>r.then)??[]),...(table?.default_output?[table.default_output]:[])]){const ref=output[binding.output];if(typeof ref==='string')refsUsed.add(ref);}}for(const op of opChoices){if(!refsUsed.has(op.id))continue;for(const r of op.resources??[])if(r.ref&&r.unit)refs.set(r.ref,r.unit);}return [...refs].map(([ref,unit])=>({ref,unit}));},[opChoices,template.steps,decisionChoices]);
  const sequencePlan = useMemo(() => { try { return { plan: parseRoutePlan(sequenceText), error: "" }; } catch (cause) { return { plan: null, error: cause instanceof Error ? cause.message : String(cause) }; } }, [sequenceText]);
  const outline = useMemo(() => routeOutline(template), [template]);
  const outlineMatches = outline.filter(item => `${item.label} ${item.id}`.toLowerCase().includes(outlineSearch.toLowerCase()));
  const visibleOutline = outline.filter(item => outlineSearch ? outlineMatches.includes(item) || outlineMatches.some(match => match.parent === item.id) : !item.parent || !collapsedGroups.includes(item.parent));
  const batchSteps = selectedRouteSteps(template, selectedIds);
  const showInspector = Boolean(selectedStep || selectedGroup);
  const navigationIndex = outline.findIndex(item => item.id === selected);
  const stageCounts = useMemo(() => {
    const counts: Record<string, number> = {};
    for (const item of preview?.instances ?? []) counts[item.step_key] = (counts[item.step_key] ?? 0) + 1;
    return counts;
  }, [preview]);
  const resolvedOperations=useMemo(()=>{const refs:Record<string,string[]>={};for(const item of preview?.instances??[])refs[item.step_key]=[...new Set([...(refs[item.step_key]??[]),item.operation_ref])];return refs;},[preview]);

  function resetHistory(next: ExpertTemplate) {
    templateRef.current = next; setTemplate(next);
    history.current = { past: [], future: [] }; setHistoryCounts({ past: 0, future: 0 });
    setRestoredPositions(null); dragStart.current = null;
    setLayoutRevision((value) => value + 1); setPreview(null); setCostResult(null); setDraftId(null); setSelectedEdges([]); setSelectedIds([]); setCollapsedGroups([]); setOutlineSearch(""); setScopeSearch(""); setOperationEditor(null); setLibraryMode("insert");
  }
  function snapshot(): HistoryEntry {
    return { template: templateRef.current, positions: Object.fromEntries((flow?.getNodes() ?? []).map((node) =>
      [node.id, { x: node.position.x, y: node.position.y, parent: node.parentId ?? null }])) };
  }
  function commit(next: ExpertTemplate, key?: string) {
    if (!editable || JSON.stringify(next) === JSON.stringify(templateRef.current)) return;
    const timeline = history.current;
    if (!key || key !== timeline.key) timeline.past.push(dragStart.current ?? snapshot());
    if (timeline.past.length > 100) timeline.past.shift();
    timeline.future = []; timeline.key = key;
    dragStart.current = null; setRestoredPositions(null);
    templateRef.current = next; setTemplate(next); setPreview(null); setCostResult(null); setDraftId(null); setError("");
    setHistoryCounts({ past: timeline.past.length, future: 0 });
  }
  function update(change: (copy: ExpertTemplate) => void, key?: string) {
    if (!editable) return;
    const copy = structuredClone(templateRef.current); change(copy); commit(copy, key);
  }
  function undo(redo = false) {
    if (!editable) return;
    const timeline = history.current, from = redo ? timeline.future : timeline.past, to = redo ? timeline.past : timeline.future;
    const next = from.pop(); if (!next) return;
    to.push(snapshot()); timeline.key = undefined;
    templateRef.current = next.template; setTemplate(next.template); setRestoredPositions(next.positions); setPreview(null); setCostResult(null); setDraftId(null);
    setSelectedIds((ids) => ids.filter((id) => [...next.template.groups, ...next.template.steps].some((item) => item.id === id)));
    setLayoutRevision((value) => value + 1); setHistoryCounts({ past: timeline.past.length, future: timeline.future.length });
  }
  function finishDrag() {
    const before = dragStart.current; dragStart.current = null;
    if (!before || JSON.stringify(before.positions) === JSON.stringify(snapshot().positions)) return;
    history.current.past.push(before); history.current.future = []; history.current.key = undefined;
    if (history.current.past.length > 100) history.current.past.shift();
    setHistoryCounts({ past: history.current.past.length, future: 0 });
  }
  function arrange() {
    if (editable) { history.current.past.push(snapshot()); history.current.future = []; history.current.key = undefined;
      setHistoryCounts({ past: history.current.past.length, future: 0 }); }
    setRestoredPositions(null); setLayoutRevision((value) => value + 1);
    setTimeout(() => void flow?.fitView({ padding: 0.2, maxZoom: 1, duration: 300 }), 150);
  }
  function attempt(action: () => void) { try { action(); } catch (cause) { toast.error(cause instanceof Error ? cause.message : "操作失败"); } }
  function togglePanel(value: Panel) {
    if (value === "properties") { if (panel && window.matchMedia("(max-width: 1099px)").matches && selected) setPanel(null); else clearSelection(); return; }
    setPanel((old) => old === value ? null : value);
  }
  function chooseSelection(ids: string[], edges: string[]) {
    setSelectedIds(old => sameIds(old, ids) ? old : ids); setSelectedEdges(old => sameIds(old, edges) ? old : edges);
    if (ids.length && window.matchMedia("(max-width: 1099px)").matches) setPanel(null);
  }
  function openOutline() { setPanel("outline"); requestAnimationFrame(() => { outlineInput.current?.focus(); outlineInput.current?.select(); }); }
  function clearSelection() { setSelectedIds([]); setSelectedEdges([]); }
  function inspectCard(id: string) { chooseSelection([id], []); }
  function focusCard(id: string) {
    const parent = template.steps.find(step => step.id === id)?.parent;
    if (parent) setCollapsedGroups(old => old.filter(group => group !== parent));
    inspectCard(id);
    setTimeout(() => void flow?.fitView({ nodes: [{ id }], padding: 0.7, minZoom: 0.55, maxZoom: 1, duration: 250 }), 160);
  }
  function toggleGroup(id: string) {
    if (!collapsedGroups.includes(id) && template.steps.some(step => step.parent === id && selectedIds.includes(step.id))) inspectCard(id);
    setCollapsedGroups(old => old.includes(id) ? old.filter(group => group !== id) : [...old, id]);
  }
  function duplicateAfter(id: string) {
    if (!editable) return;
    attempt(() => { const result = duplicateRouteAfter(templateRef.current, id); commit(result.template); inspectCard(result.ids[0]); });
  }
  function applyBatch(patch: Parameters<typeof patchRouteSteps>[2]) {
    try { commit(patchRouteSteps(templateRef.current, selectedIds, patch)); toast.success(`已统一配置 ${batchSteps.length} 道工序`); return true; }
    catch (cause) { toast.error(cause instanceof Error ? cause.message : String(cause)); return false; }
  }
  function importSequence() {
    if (!editable || !sequencePlan.plan) return;
    attempt(() => {
      const result = insertRoutePlan(templateRef.current, sequencePlan.plan!, { after: selected || undefined, library: opChoices });
      commit(result.template); inspectCard(result.selected);
      if (sequencePlan.plan!.step_count > 12) setCollapsedGroups(old => [...new Set([...old, ...result.groupIds])]);
      setSequenceText(""); setPanel(window.matchMedia("(max-width: 1099px)").matches ? null : "outline");
      toast.success(`已添加 ${sequencePlan.plan!.group_count} 个阶段、${sequencePlan.plan!.step_count} 道工序`);
    });
  }
  async function refresh() {
    const [nextCatalog, nextChanges] = await Promise.all([loadTemplateCatalog(), loadTemplateChangeSets()]);
    setCatalog(nextCatalog); setChangesets(nextChanges);
    return { catalog: nextCatalog, changesets: nextChanges };
  }
  async function login() {
    setBusy(true); setError("");
    try { setSessionToken(tokenInput); const user = await loadSession(); setSession(user); setTokenInput(""); const loaded = await refresh();
      if (!history.current.past.length && !readOnly) resetHistory(blankTemplate(loaded.catalog, loaded.changesets.flatMap((change) => change.patch?.template?.id ? [change.patch.template.id] : []))); }
    catch (cause) { setSessionToken(""); setSession(null); setError(cause instanceof Error ? cause.message : "登录失败"); }
    finally { setBusy(false); }
  }
  async function openTemplate(id: string) {
    setBusy(true); setError("");
    try { const loaded = await loadTemplate(id); resetHistory(loaded); setSourceTemplate(loaded); setReadOnly(true); setProposalView(false);
      setSelectedIds([]); setCaseId(loaded.cases[0]?.id ?? ""); setPanel(null); }
    catch (cause) { setError(cause instanceof Error ? cause.message : "加载失败"); }
    finally { setBusy(false); }
  }
  function createNew() { const next = blankTemplate(catalog, changesets.flatMap((change) => change.patch?.template?.id ? [change.patch.template.id] : [])); resetHistory(next); setSourceTemplate(null); setReadOnly(false); setProposalView(false);
    setSelectedIds([]); setCaseId("basic"); setPanel(null); }
  function loadExample(){const result=workbenchExample([...catalog.templates.map(t=>t.key),...changesets.flatMap(c=>c.patch?.template?.id?[c.patch.template.id]:[])],catalog.operations.map(o=>o.id.split(':')[1]),catalog.decisions.map(t=>t.id));resetHistory(result.template);setCostScenario(result.scenario);setReadOnly(false);setProposalView(false);setSourceTemplate(null);setSelectedIds(['mill']);setCaseId('standard');setPanel(null);toast.info('已创建示例草稿；价格为虚构试算参数');}
  function cloneVersion() { const copy = structuredClone(templateRef.current), [major, minor] = copy.version.split(".").map(Number);
    copy.version = `${major}.${minor + 1}.0`; copy.operations = []; copy.decisions=[]; delete copy.author;
    resetHistory(copy); setReadOnly(false); setProposalView(false); setPanel(null); toast.info("已创建新版本草稿"); }

  function addSequence(names: string[], after?: string, parent?: string | null, operation?: OperationSummary) {
    if (!editable) return;
    let addedId: string | undefined;
    attempt(() => { const result = insertRouteSequence(templateRef.current, names, { after, parent, operation, library: opChoices });
      commit(result.template); addedId = result.ids[result.ids.length - 1];
      const scope = result.template.steps.find(step => step.id === addedId)?.parent;
      if (scope) setCollapsedGroups(old => old.filter(group => group !== scope));
      inspectCard(addedId); });
    return addedId;
  }
  function append(id?: string, operation?: OperationSummary) { return addSequence([operation?.label ?? "新工序"], id || undefined, null, operation); }
  function addInside(id: string) {
    const children = templateRef.current.steps.filter((step) => step.parent === id);
    const tail = children.filter((step) => !templateRef.current.edges.some((edge) => edge.from === step.id)).at(-1);
    addSequence(["新工序"], tail?.id, id);
  }
  function addGroup(mode:'repeat'|'conditional'='repeat', conditional=true) {
    if (!editable) return;
    const id = nextRouteKey("group", allItems.map((item) => item.id));
    const scope = selected ? routeScope(templateRef.current, selected) : null;
    if (scope) { toast.info("重复组创建在路线主层级"); }
    attempt(() => { const copy = structuredClone(templateRef.current); copy.groups.push({ id, label: mode==='conditional'?conditional?"条件工序组":"新工艺阶段":"重复工序组", iteration_set: "items", join_policy: "all",group_mode:mode,execution_mode:'parallel',...(mode==='conditional'&&conditional?{when:{source:'facts',field:`enabled_${id}`,op:'eq',value:true} as const}:{}) });
      for (const sample of copy.cases) {if(mode==='repeat'&&!("items" in sample.sets)) sample.sets.items = [{ id: "item_1" }];if(mode==='conditional'&&conditional)sample.facts[`enabled_${id}`]=true;}
      if (selected && !scope) {
        const outgoing = copy.edges.filter((edge) => edge.from === selected); copy.edges = copy.edges.filter((edge) => edge.from !== selected);
        copy.edges.push({ from: selected, to: id }, ...outgoing.map((edge) => ({ from: id, to: edge.to })));
      }
      const result = insertRouteSequence(copy, ["组内首道工序"], { parent: id, library: opChoices });
      commit(result.template); inspectCard(id);
    });
  }
  function duplicate(ids: string[]) {
    if (!editable || !ids.length) return;
    attempt(() => { const result = duplicateRouteItems(templateRef.current, ids); commit(result.template); chooseSelection(result.ids, []); });
  }
  function removeSelected(ids = selectedIds) {
    if (!editable) return;
    let next = removeRouteItems(templateRef.current, ids);
    next = { ...next, edges: next.edges.filter((edge) => !selectedEdges.includes(`${edge.from}>>${edge.to}`)) };
    commit(next); setSelectedIds([]); setSelectedEdges([]);
  }
  function rename(id: string, label: string) { update((copy) => { const item = [...copy.groups, ...copy.steps].find((entry) => entry.id === id); if (item) item.label = label; }, `label:${id}`); }
  function updateGroup(id: string, values: Partial<RouteGroup>) { update((copy) => {
    const group = copy.groups.find((item) => item.id === id); if (!group) return;
    if (values.iteration_set && values.iteration_set !== group.iteration_set) {
      for (const sample of copy.cases) if (group.iteration_set in sample.sets && !(values.iteration_set in sample.sets)) {
        sample.sets[values.iteration_set] = sample.sets[group.iteration_set];
        if (!copy.groups.some((other) => other.id !== id && other.iteration_set === group.iteration_set)) delete sample.sets[group.iteration_set];
      }
    }
    Object.assign(group, values);
  }, `group:${id}`); }
  function updateStep(id: string, values: Partial<RouteStep>) { update((copy) => { const item = copy.steps.find((step) => step.id === id); if (item) Object.assign(item, values); }, `step:${id}`); }
  function updateOperation(index: number, values: Partial<ExpertTemplate["operations"][number]>) { update((copy) => {
    const operation = copy.operations[index]; if (!operation) return;
    const old = `operation:${operation.id}:${operation.version}`; Object.assign(operation, values);
    for (const step of copy.steps) if (step.operation_ref === old) step.operation_ref = `operation:${operation.id}:${operation.version}`;
  }, `operation:${index}`); }
  function updateCase(change: (sample: SampleCase) => void) { update((copy) => { const sample = copy.cases.find((item) => item.id === currentCase?.id); if (sample) change(sample); }); }
  function updateDecision(index:number,next:DecisionTable){update(copy=>{const old=copy.decisions?.[index];if(!old)return;const oldRef=`decision:${old.id}:${old.version}`;copy.decisions![index]=next;for(const item of [...copy.groups,...copy.steps])if(item.decision_binding?.ref===oldRef){item.decision_binding.ref=`decision:${next.id}:${next.version}`;const index=old.outputs.findIndex(o=>o.id===item.decision_binding!.output);if(index>=0&&old.outputs.length===next.outputs.length)item.decision_binding.output=next.outputs[index].id;}});}
  function editRate(ref:string,changes:Partial<CostScenario['rates'][string]>,unit:string){setCostResult(null);setCostScenario(old=>({...old,rates:{...old.rates,[ref]:{value:'',currency:old.currency,unit,...old.rates[ref],...changes}}}));}
  async function runCost(){if(!currentCase)return;setBusy(true);setError('');try{const result=await calculateTemplate(templateRef.current,currentCase,costScenario);setCostResult(result);setPreview(result.route);}catch(cause){setError(cause instanceof Error?cause.message:String(cause));setCostResult(null);}finally{setBusy(false);}}
  function chooseOperation(operation: OperationSummary) {
    if (window.matchMedia("(max-width: 1099px)").matches) setPanel(null);
    if (libraryMode === "replace" && selectedStep) { updateStep(selectedStep.id, { operation_ref: operation.id, cost_basis: operation.cost_basis }); }
    else if (libraryMode === "batch" && batchSteps.length) applyBatch({ operation_ref: operation.id, cost_basis: operation.cost_basis });
    else append(selected || undefined, operation);
  }
  function connectSelection() { if (selectedIds.length < 2) return;
    attempt(() => { let next = templateRef.current; for (let index = 1; index < selectedIds.length; index++) next = connectRoute(next, selectedIds[index - 1], selectedIds[index]); commit(next); }); }
  async function runPreview() {
    if (!currentCase) return; setBusy(true); setError("");
    try { const result = await previewTemplate(templateRef.current, currentCase); setPreview(result); setPanel("cases");
      if (!result.matches_expected) toast.warning("展开数量与专家预期不一致"); }
    catch (cause) { setPreview(null); setError(cause instanceof Error ? cause.message : "预览失败"); }
    finally { setBusy(false); }
  }
  async function submit() { setBusy(true); setError("");
    try { const result = await submitTemplate(templateRef.current, reason, crypto.randomUUID()); setDraftId(result.id); await refresh(); setPanel("governance"); toast.success("已提交独立审核"); }
    catch (cause) { setError(cause instanceof Error ? cause.message : "提交失败"); } finally { setBusy(false); } }
  async function govern(change: TemplateChangeSet, action: "approved" | "changes_requested" | "apply" | "publish") {
    setBusy(true); setError("");
    try { if (action === "approved" || action === "changes_requested") await reviewChangeSet(change.id, action, action === "approved" ? "已审核模板结构与专家案例" : "请修订模板后重新提交");
      if (action === "apply") await applyChangeSet(change.id);
      if (action === "publish") { if (!change.source_revision) throw new Error("请先应用到真源"); await publishChangeSet(change.id, change.source_revision); }
      await refresh(); toast.success("治理状态已更新");
    } catch (cause) { setError(cause instanceof Error ? cause.message : "操作失败"); } finally { setBusy(false); }
  }
  function inspectProposal(change: TemplateChangeSet) { const proposed = change.patch?.template;
    if (!proposed) { toast.error("此变更没有可预览的模板内容"); return; }
    resetHistory(structuredClone(proposed)); setSourceTemplate(null); setReadOnly(true); setProposalView(true);
    setSelectedIds([]); setCaseId(proposed.cases[0]?.id ?? ""); setPanel("governance"); }

  const semanticChanges = sourceTemplate && editable ? [
    ...template.groups.filter((item) => !sourceTemplate.groups.some((old) => old.id === item.id)).map((item) => `新增工序组：${item.label}`),
    ...template.steps.filter((item) => !sourceTemplate.steps.some((old) => old.id === item.id)).map((item) => `新增工序：${item.label}`),
    ...(JSON.stringify(template.edges) !== JSON.stringify(sourceTemplate.edges) ? ["调整前置关系"] : []),
  ] : [];
  const rail = [{ id: "outline" as const, icon: ListTree }, { id: "templates" as const, icon: Workflow }, { id: "operations" as const, icon: LibraryBig },
    {id:"decisions" as const,icon:Table2},{id:"costs"as const,icon:Calculator},
    { id: "properties" as const, icon: SlidersHorizontal }, { id: "cases" as const, icon: FlaskConical },
    { id: "governance" as const, icon: ShieldCheck }, { id: "settings" as const, icon: Settings2 }];
  const undoAvailable = historyCounts.past > 0;
  const redoAvailable = historyCounts.future > 0;

  const cardProperties = (<>
            {!selectedStep && !selectedGroup && <p className="text-sm text-slate-400">选择白板中的工序或重复组，查看其属性。</p>}
            {(selectedStep || selectedGroup) && <>
              <div className="flex items-center gap-2"><span className={`grid size-8 place-items-center rounded-lg ${selectedGroup ? "bg-amber-100 text-amber-700" : "bg-indigo-50 text-indigo-600"}`}>{selectedGroup ? <Layers3 className="size-4" /> : <GitBranch className="size-4" />}</span><p className="min-w-0 flex-1 truncate text-sm font-semibold">{selectedGroup?.label ?? selectedStep?.label}</p>{editable && <button aria-label="删除所选工序" onClick={() => removeSelected()} className="grid size-7 place-items-center rounded-lg text-slate-400 hover:bg-rose-50 hover:text-rose-600"><Trash2 className="size-4" /></button>}</div>
              <label className="block text-xs text-slate-500">工序名称<Input className="mt-1.5" disabled={!editable} value={selectedGroup?.label ?? selectedStep?.label ?? ""} onChange={(event) => rename(selected, event.target.value)} /></label>
              {selectedGroup && <>{selectedGroup.group_mode!=='conditional'&&<><label className="block text-xs text-slate-500">逐个加工的对象集合<Input className="mt-1.5" disabled={!editable} value={selectedGroup.iteration_set} onChange={(event) => updateGroup(selected, { iteration_set: event.target.value })} /></label><p className="text-xs leading-5 text-slate-500">每个对象执行组内工序；全部完成后放行后续工序。</p></>}<details open={Boolean(selectedGroup.when)} className="rounded-xl border border-slate-200 p-3"><summary className="cursor-pointer text-xs text-slate-500">启用条件{selectedGroup.when ? " · 已配置" : " · 可选"}</summary><div className="mt-3"><ConditionEditor condition={selectedGroup.when} allowItem={false} editable={editable} onChange={(when) => updateGroup(selected, { when })} /></div></details></>}
              {selectedStep && <>
                <div><p className="mb-1.5 text-xs text-slate-500">标准工序</p><button className="flex w-full items-center justify-between rounded-xl border border-slate-200 p-3 text-left text-sm hover:border-indigo-300 disabled:cursor-default" disabled={!editable} onClick={() => { setLibraryMode("replace"); setPanel("operations"); }}><span className="min-w-0 truncate">{opChoices.find((item) => item.id === selectedStep.operation_ref)?.label ?? selectedStep.operation_ref}</span><Search className="ml-2 size-4 shrink-0 text-slate-400" /></button></div>
                {editable && template.operations.some(operation => `operation:${operation.id}:${operation.version}` === selectedStep.operation_ref) && <button className="text-xs text-indigo-600" onClick={() => { setOperationEditor(template.operations.findIndex(operation => `operation:${operation.id}:${operation.version}` === selectedStep.operation_ref)); setPanel('operations'); }}>配置此标准工序的参数与资源 →</button>}
                <label className="block text-xs text-slate-500">工序计费对象（说明）<Input className="mt-1.5" disabled={!editable} value={selectedStep.cost_basis} onChange={(event) => updateStep(selected, { cost_basis: event.target.value })} /></label>
                <details className="rounded-xl border border-slate-200 p-3"><summary className="cursor-pointer text-xs text-slate-500">所属阶段 · {template.groups.find(group => group.id === selectedStep.parent)?.label ?? "主路线"}</summary><div className="mt-3 space-y-2"><Input aria-label="搜索目标阶段" placeholder="搜索目标阶段" value={scopeSearch} onChange={event => setScopeSearch(event.target.value)} /><div className="flex max-h-40 flex-wrap gap-1.5 overflow-y-auto">{[{ id: "", label: "路线主层级" }, ...template.groups.filter(group => `${group.label} ${group.id}`.toLowerCase().includes(scopeSearch.toLowerCase()))].map(group => <button key={group.id} disabled={!editable} onClick={() => attempt(() => { commit(moveRouteStep(templateRef.current, selected, group.id || null)); if (group.id) setCollapsedGroups(old => old.filter(id => id !== group.id)); })} className={`rounded-lg border px-2.5 py-1.5 text-xs ${selectedStep.parent === (group.id || null) ? "border-indigo-300 bg-indigo-50 text-indigo-700" : "border-slate-200 text-slate-500"}`}>{group.label}</button>)}</div></div></details>
                <details open={Boolean(selectedStep.when)} className="rounded-xl border border-slate-200 p-3"><summary className="cursor-pointer text-xs text-slate-500">启用条件{selectedStep.when ? " · 已配置" : " · 可选"}</summary><div className="mt-3"><ConditionEditor condition={selectedStep.when} allowItem={selectedStep.parent !== null && template.groups.find(g=>g.id===selectedStep.parent)?.group_mode!=='conditional'} editable={editable} onChange={(when) => updateStep(selected, { when })} /></div></details>
              </>}
              <details open={Boolean(selectedGroup?.decision_binding??selectedStep?.decision_binding)} className="rounded-xl border border-slate-200 p-3"><summary className="cursor-pointer text-xs text-slate-500">决策引用{selectedGroup?.decision_binding||selectedStep?.decision_binding ? " · 已配置" : " · 可选"}</summary><div className="mt-3"><DecisionBindingEditor tables={decisionChoices} binding={selectedGroup?.decision_binding??selectedStep?.decision_binding} editable={editable} operation={Boolean(selectedStep)} onOpen={()=>setPanel('decisions')} onChange={decision_binding=>selectedGroup?updateGroup(selected,{decision_binding}):updateStep(selected,{decision_binding})}/></div></details>
              {selectedGroup&&<div className="space-y-2"><div className="flex gap-1">{(['repeat','conditional']as const).map(group_mode=><button key={group_mode} disabled={!editable} className={`rounded border px-2 py-1 text-xs ${(selectedGroup.group_mode??'repeat')===group_mode?'border-indigo-400 bg-indigo-50':''}`} onClick={()=>updateGroup(selected,{group_mode})}>{group_mode==='repeat'?'逐对象重复':'整组执行一次'}</button>)}</div>{selectedGroup.group_mode!=='conditional'&&<div className="flex gap-1">{(['parallel','sequential']as const).map(execution_mode=><button key={execution_mode} disabled={!editable} className={`rounded border px-2 py-1 text-xs ${(selectedGroup.execution_mode??'parallel')===execution_mode?'border-indigo-400 bg-indigo-50':''}`} onClick={()=>updateGroup(selected,{execution_mode})}>{execution_mode==='parallel'?'对象间独立':'对象间顺序'}</button>)}</div>}</div>}
              <details className="rounded-xl border border-slate-200 p-3"><summary className="cursor-pointer text-xs text-slate-500">前置关系 · {template.edges.filter((edge) => edge.to === selected).length} 项</summary><p className="my-2 text-xs text-slate-400">可直接拖动白板两侧的连接点。</p>{allItems.filter((item) => item.id !== selected && routeScope(template, item.id) === routeScope(template, selected)).map((item) => <label key={item.id} className="flex items-center gap-2 py-1 text-xs"><input type="checkbox" disabled={!editable} checked={template.edges.some((edge) => edge.from === item.id && edge.to === selected)} onChange={(event) => attempt(() => { if (event.target.checked) commit(connectRoute(templateRef.current, item.id, selected)); else update((copy) => { copy.edges = copy.edges.filter((edge) => edge.from !== item.id || edge.to !== selected); }); })} />{item.label}</label>)}</details>
            </>}
          </>);

  return <main className="flex h-dvh min-h-[560px] flex-col overflow-hidden bg-[#f7f8fb] text-slate-900"
    onKeyDown={(event) => {
      const modifier = event.metaKey || event.ctrlKey;
      if (modifier && event.key.toLowerCase() === "k") { event.preventDefault(); openOutline(); return; }
      if (event.altKey && ["ArrowUp", "ArrowDown"].includes(event.key)) {
        const index = navigationIndex + (event.key === "ArrowUp" ? -1 : 1);
        if (index >= 0 && index < outline.length) { event.preventDefault(); focusCard(outline[index].id); } return;
      }
      if (event.key === "Escape") { event.preventDefault(); setPanel(null); clearSelection(); return; }
      const target = event.target as HTMLElement; if (target.closest("input,textarea,select,[contenteditable=true]")) return;
      if (modifier && event.key.toLowerCase() === "z") { event.preventDefault(); undo(event.shiftKey); }
      else if (modifier && event.key.toLowerCase() === "d") { event.preventDefault(); if (event.shiftKey && selectedIds.length === 1) duplicateAfter(selected); else duplicate(selectedIds); }
      else if (event.key === "Delete" || event.key === "Backspace") { event.preventDefault(); removeSelected(); }
      else if (!modifier && event.key.toLowerCase() === "n") { event.preventDefault(); append(selected || undefined); }
      else if (!modifier && event.key.toLowerCase() === "g" && editable) { event.preventDefault(); addGroup(); }
    }}
    onPaste={(event) => { if (!editable || (event.target as HTMLElement).closest("input,textarea")) return;
      const value = event.clipboardData.getData("text"); if (value.trim()) { event.preventDefault(); setSequenceText(value); setPanel("sequence"); } }}>
    <Toaster richColors />
    <header className="z-20 flex h-16 shrink-0 items-center gap-4 border-b border-slate-200 bg-white px-4">
      <Link href="/" title="返回本体白板" aria-label="本体白板" className="grid size-8 shrink-0 place-items-center rounded-lg text-slate-500 hover:bg-slate-100"><ArrowLeft className="size-4" /></Link>
      <div className="grid size-9 shrink-0 place-items-center rounded-xl bg-indigo-600 text-white"><Layers3 className="size-5" /></div>
      <div className="min-w-0"><h1 className="text-xs text-slate-500">专家工艺工作台</h1>{session ? <input aria-label="模板名称" className="w-[min(32vw,360px)] truncate bg-transparent text-sm font-semibold outline-none" readOnly={!editable}
        value={template.label} onChange={(event) => update((copy) => { copy.label = event.target.value; }, "template-label")} /> : <p className="text-sm font-semibold">可视化工艺编排</p>}</div>
      {session && <><span className="hidden rounded-full bg-slate-100 px-2.5 py-1 text-[11px] text-slate-500 sm:inline">{proposalView ? "审核提案" : readOnly ? "已发布" : "草稿"} · v{template.version}</span>
        <div className="ml-auto flex items-center gap-2"><span className="hidden text-xs text-slate-400 lg:inline">{template.steps.length} 道工序 · {template.groups.length} 个工序组</span>
          <Button variant="ghost" size="icon-sm" aria-label="刷新目录" disabled={busy} onClick={() => void refresh().catch((cause) => setError(String(cause)))}><RefreshCw className="size-4" /></Button>
          {readOnly && !proposalView && canEdit && <Button variant="outline" size="sm" onClick={cloneVersion}><Copy className="size-3.5" /> 新版本</Button>}
          <Button variant="outline" size="sm" disabled={busy} onClick={() => setPanel("governance")}><ShieldCheck className="size-3.5" /><span className="hidden sm:inline">审核发布</span></Button>
          <button className="px-2 text-xs text-slate-400 hover:text-slate-700" onClick={() => { setSessionToken(""); setSession(null); setCatalog(emptyCatalog); setChangesets([]); }}>退出</button>
        </div></>}
    </header>
    {!session ? <section className="m-auto w-[min(440px,90vw)] rounded-2xl border border-slate-200 bg-white p-7 shadow-sm">
      <LockKeyhole className="mb-4 size-8 text-indigo-600" /><h2 className="text-xl font-semibold">登录专家工作台</h2>
      <p className="mt-2 text-sm leading-6 text-slate-500">用工艺白板编排标准路线。模板编写、独立审核和发布按角色授权。</p>
      <div className="mt-5 flex gap-2"><Input type="password" autoComplete="off" aria-label="个人访问凭证" placeholder="个人访问凭证" value={tokenInput} onChange={(event) => setTokenInput(event.target.value)} onKeyDown={(event) => { if (event.key === "Enter") void login(); }} /><Button disabled={!tokenInput.trim() || busy} onClick={() => void login()}>{busy ? <Loader2 className="size-4 animate-spin" /> : "登录"}</Button></div>
      {error && <p role="alert" className="mt-3 text-sm text-rose-600">{error}</p>}
    </section> : <div className="relative flex min-h-0 flex-1">
      <nav aria-label="工作台功能" className="z-20 flex w-16 shrink-0 flex-col items-center gap-2 border-r border-slate-200 bg-white py-4">
        {rail.map(({ id, icon: Icon }) => <button key={id} aria-label={panelNames[id]} aria-pressed={id === "properties" ? showInspector : panel === id} title={panelNames[id]} onClick={() => { if (id === "operations") setLibraryMode("insert"); togglePanel(id); }}
          className={`relative flex size-12 flex-col items-center justify-center gap-1 rounded-xl transition-colors ${(id === "properties" ? showInspector : panel === id) ? "bg-indigo-50 text-indigo-600" : "text-slate-500 hover:bg-slate-100"}`}><Icon className="size-5" /><span className="text-[9px]">{{ outline: "目录", templates: "模板", operations: "工序库", properties: "属性", cases: "案例", governance: "审核", settings: "设置", decisions:"决策",costs:"成本" }[id]}</span>
          {id === "governance" && changesets.some((change) => change.status === "review_required") && <span className="absolute right-2 top-1.5 size-1.5 rounded-full bg-amber-500" />}</button>)}
        <div className="flex-1" />{editable && <button aria-label="批量添加工序" title="粘贴一段工序清单" onClick={() => togglePanel("sequence")} className="grid size-11 place-items-center rounded-xl text-slate-500 hover:bg-slate-100"><ClipboardPaste className="size-5" /></button>}
      </nav>
      {panel && panel !== "properties" && <aside aria-label={panelNames[panel]} className={`absolute inset-y-0 left-16 z-20 flex shrink-0 ${panel==='decisions'?'w-[620px]':'w-[288px]'} max-w-[calc(100vw-80px)] flex-col border-r border-slate-200 bg-white shadow-lg min-[768px]:static min-[768px]:shadow-none`}>
        <div className="flex h-14 shrink-0 items-center justify-between border-b border-slate-100 px-4"><h2 className="text-sm font-semibold">{panelNames[panel]}</h2><button aria-label="收起侧栏" title="收起 · Esc" className="grid size-7 place-items-center rounded-lg text-slate-400 hover:bg-slate-100" onClick={() => setPanel(null)}><PanelLeftClose className="size-4" /></button></div>
        <div className="min-h-0 flex-1 space-y-4 overflow-y-auto p-4">
          {panel === "templates" && <>
            {canEdit && <Button className="w-full" onClick={createNew}><FilePlus2 className="size-4" /> 新建路线模板</Button>}
            {canEdit&&<Button className="w-full" variant="outline" onClick={loadExample}><FlaskConical className="size-4"/>体验决策与成本示例</Button>}
            <p className="text-xs leading-5 text-slate-500">选择路线查看白板。已发布模板可复制为新版本。</p>
            {catalog.templates.map((item) => <button key={item.id} className="w-full rounded-xl border border-slate-200 p-3 text-left hover:border-indigo-300 hover:bg-indigo-50" onClick={() => void openTemplate(item.id)}><p className="text-sm font-medium">{item.label}</p><p className="mt-1 text-xs text-slate-400">{item.family} · v{item.version}</p></button>)}
            {!catalog.templates.length && <p className="rounded-xl border border-dashed p-4 text-sm text-slate-400">还没有已发布的路线模板。</p>}
          </>}
          {panel === "settings" && <>
            <p className="text-xs text-slate-500">这些信息适用于整条路线。</p>
            {([ ["id", "稳定标识"], ["version", "版本"], ["family", "制造类别"], ["output_basis", "产出基准"] ] as const).map(([key, label]) => <label key={key} className="block text-xs text-slate-500">{label}<Input className="mt-1.5" disabled={!editable} value={template[key]} onChange={(event) => update((copy) => { copy[key] = event.target.value; }, `template:${key}`)} /></label>)}
            <label className="block text-xs text-slate-500">说明<Textarea className="mt-1.5" disabled={!editable} value={template.summary} onChange={(event) => update((copy) => { copy.summary = event.target.value; }, "template-summary")} /></label>
            <div className="rounded-xl bg-slate-50 p-3 text-xs text-slate-500">{session.principal}<div className="mt-2 flex gap-1">{[canEdit && "编写", canReview && "审核", canPublish && "发布"].filter(Boolean).map((label) => <span key={String(label)} className="rounded bg-white px-2 py-1">{label}</span>)}</div></div>
          </>}
          {panel==='decisions'&&<>
            <p className="text-xs text-slate-500">决策表独立版本化，随模板包提交审核。白板工序可引用输出，控制是否加工或选择工艺方法。</p>
            <div className="flex flex-wrap gap-2">{(template.decisions??[]).map((table,index)=><button key={index} className={`rounded-lg border px-3 py-2 text-xs ${decisionIndex===index?'border-indigo-400 bg-indigo-50':''}`} onClick={()=>setDecisionIndex(index)}>{table.label}</button>)}{editable&&<Button size="sm" onClick={()=>{const index=template.decisions?.length??0;update(copy=>{copy.decisions??=[];copy.decisions.push(emptyDecision(nextRouteKey('decision',decisionChoices.map(d=>d.id))));});setDecisionIndex(index);}}>＋新建决策表</Button>}</div>
            {template.decisions?.[decisionIndex]&&<DecisionEditor table={template.decisions[decisionIndex]} editable={editable} onChange={next=>updateDecision(decisionIndex,next)} onRemove={()=>{update(copy=>{copy.decisions?.splice(decisionIndex,1);});setDecisionIndex(0);}}/>}
            {catalog.decisions.length>0&&<section className="space-y-2 border-t pt-3"><h3 className="text-xs font-semibold">已发布的可复用决策</h3>{catalog.decisions.map(table=><div key={`${table.id}:${table.version}`} className="flex items-center justify-between text-xs"><span>{table.label} · v{table.version}</span>{editable&&<button className="text-indigo-600" onClick={()=>{const copy=structuredClone(table);const[major,minor]=copy.version.split('.').map(Number);copy.version=`${major}.${minor+1}.0`;const index=template.decisions?.length??0;update(t=>{t.decisions??=[];t.decisions.push(copy);});setDecisionIndex(index);}}>复制为新版本</button>}</div>)}</section>}
          </>}
          {panel==='costs'&&<>
            <div className="flex flex-wrap gap-1.5">{template.cases.map(sample=><button key={sample.id} onClick={()=>{setCaseId(sample.id);setPreview(null);setCostResult(null);}} className={`rounded-lg border px-2.5 py-1.5 text-xs ${caseId===sample.id?'border-indigo-300 bg-indigo-50 text-indigo-700':'border-slate-200 text-slate-500'}`}>{sample.id}</button>)}</div>
            <p className="text-xs leading-5 text-slate-500">按当前案例展开路线。加工次数按批量容量向上取整，准备时长计一次。价格仅用于本次试算，不写入模板。</p>
            <label className="block text-xs">生产数量<Input aria-label="生产数量" value={costScenario.quantity} onChange={e=>{setCostResult(null);setCostScenario(s=>({...s,quantity:e.target.value}));}}/></label>
            <div className="flex flex-wrap gap-1">{catalog.currencies.map(currency=><button key={currency} className={`rounded-lg border px-2 py-1 text-xs ${costScenario.currency===currency?'border-indigo-400 bg-indigo-50':''}`} onClick={()=>{setCostResult(null);setCostScenario(s=>({...s,currency}));}}>{currency}</button>)}</div>
            <div className="grid grid-cols-2 gap-2">{([['margin','利润占收入比例'],['operating_ratio','运营费占收入比例']]as const).map(([key,label])=><label key={key} className="text-xs">{label}<Input aria-label={label} value={costScenario[key]} onChange={e=>{setCostResult(null);setCostScenario(s=>({...s,[key]:e.target.value}));}}/></label>)}</div><p className="text-[10px] text-slate-400">0.1 表示 10%；合理单价＝单位成本 ÷（1－利润率－运营费率）。</p>
            <h3 className="text-xs font-semibold">资源单价／实例观测</h3>{resourceChoices.map(({ref,unit})=>{const rate=costScenario.rates[ref];return <section key={ref} className="space-y-2 rounded-xl border p-3"><div className="flex items-center justify-between text-xs"><b>{ref}</b><button className="text-indigo-600" onClick={()=>editRate(ref,{source:rate?.source?undefined:{node_id:'',predicate:''}},unit)}>{rate?.source?'改用试算参数':'引用实例观测'}</button></div>{rate?.source?<><Input aria-label={`价格实例 ${ref}`} placeholder="实例 ID" value={rate.source.node_id} onChange={e=>editRate(ref,{source:{...rate.source!,node_id:e.target.value}},unit)}/><Input aria-label={`价格谓词 ${ref}`} placeholder="单价谓词" value={rate.source.predicate} onChange={e=>editRate(ref,{source:{...rate.source!,predicate:e.target.value}},unit)}/><Input aria-label={`价格时点 ${ref}`} placeholder="业务时点（含时区，可留空）" value={rate.source.as_of??''} onChange={e=>editRate(ref,{source:{...rate.source!,as_of:e.target.value||undefined}},unit)}/></>:<div className="flex items-center gap-2"><Input aria-label={`单价 ${ref}`} placeholder="未配置" value={rate?.value??''} onChange={e=>editRate(ref,{value:e.target.value},unit)}/><span className="text-[10px]">{rate?.currency??costScenario.currency} /</span><Input aria-label={`单价单位 ${ref}`} className="w-20" value={rate?.unit??unit} onChange={e=>editRate(ref,{unit:e.target.value},unit)}/></div>}</section>;})}
            {!resourceChoices.length&&<p className="text-xs text-amber-600">请在标准工序中维护资源标识、数量、单位与计费基准。</p>}
            <Button className="w-full" disabled={busy||!(canEdit||canReview||canPublish)} onClick={()=>void runCost()}><Calculator className="size-4"/>计算当前路线</Button>
            {costResult&&<section className="space-y-3"><p className={`rounded-lg p-2 text-xs ${costResult.status==='complete'?'bg-emerald-50 text-emerald-700':'bg-amber-50 text-amber-700'}`}>{costResult.status==='complete'?'输入完整，已确定性计算':'输入未完整，暂不提供总成本'} · {costResult.currency}</p><div className="grid grid-cols-2 gap-2">{[['总成本',costResult.total_cost],['已知成本小计',costResult.known_cost],['总材料成本',costResult.material_cost],['总制造成本',costResult.manufacturing_cost],['单位成本',costResult.unit_cost],['合理单价',costResult.reasonable_unit_price]].map(([label,value])=><div key={label} className="rounded-xl border border-slate-200 p-3"><p className="text-[10px] text-slate-400">{label}</p><b className="text-sm">{value??'—'}</b></div>)}</div>
              {costResult.model_traces.length>0&&<details className="rounded-xl border border-slate-200 p-3"><summary className="cursor-pointer text-xs font-semibold">模型计算记录 · {costResult.model_traces.length} 项</summary>{costResult.model_traces.map((trace,index)=><section key={index} className="mt-3 border-t pt-2 text-[10px]"><p>{trace.instance_id}{trace.resource_ref?` / ${trace.resource_ref}`:''}</p><p className="text-indigo-600">{trace.model_ref} → {trace.output.value} {trace.output.unit}</p><pre className="mt-1 max-h-52 overflow-auto whitespace-pre-wrap break-all text-slate-500">{JSON.stringify(trace.trace,null,2)}</pre></section>)}</details>}
              {costResult.missing.map((gap,index)=><p key={index} className="text-xs text-amber-700">{gap.instance_id} · {gap.field}：{gap.message}</p>)}
              {costResult.rows.map(row=><details key={row.id} className="rounded-lg border p-2"><summary className="cursor-pointer text-xs">{row.label} · {row.known_cost} · {row.cycles} 次加工</summary><p className="mt-2 text-[10px] text-slate-500">{row.id} · 总时长 {row.duration_seconds??'未配置'} 秒</p>{row.resources.map((r,index)=><p key={index} className="mt-1 text-[10px]">{r.ref}：{r.quantity} {r.unit} × {r.rate}＝{r.cost} · {r.provenance.source_backed?`实例观测 ${r.provenance.assertion_id}`:'场景试算参数'}</p>)}</details>)}
            </section>}
          </>}
          {panel === "operations" && <>
            <p className="rounded-xl bg-indigo-50 p-3 text-xs leading-5 text-indigo-700">{libraryMode === "batch" ? `为所选 ${batchSteps.length} 道工序统一选择标准工序` : libraryMode === "replace" && selectedStep ? `为“${selectedStep.label}”选择标准工序` : selected ? `点击工序，接在“${selectedGroup?.label ?? selectedStep?.label ?? "所选节点"}”后面` : "点击或拖入白板，添加标准工序"}</p>
            {operationEditor !== null && template.operations[operationEditor] && <section className="space-y-2 border-t border-slate-200 pt-4"><div className="flex items-center justify-between"><h3 className="text-xs font-semibold text-slate-500">标准工序配置</h3><button aria-label="关闭标准工序配置" onClick={() => setOperationEditor(null)}><X className="size-3.5 text-slate-400" /></button></div><OperationEditor operation={template.operations[operationEditor]} editable={editable} units={catalog.units} models={catalog.models} quantityModels={catalog.quantity_models} onChange={(values) => updateOperation(operationEditor, values)} onRemove={() => { update((copy) => { copy.operations.splice(operationEditor, 1); }); setOperationEditor(null); }} /></section>}
            <div className="relative"><Search className="absolute left-3 top-2.5 size-4 text-slate-400" /><Input aria-label="搜索标准工序" placeholder="搜索名称或工艺方法" className="pl-9" value={operationSearch} onChange={(event) => setOperationSearch(event.target.value)} /></div>
            <div className="space-y-2">{filteredOperations.map((item) => <div key={item.id} draggable={editable && libraryMode === "insert"} onDragStart={(event) => { event.dataTransfer.setData("application/knowledgeos-operation", item.id); event.dataTransfer.effectAllowed = "copy"; }} className="flex items-center gap-1 rounded-xl border border-slate-200 bg-white hover:border-indigo-300">
              <GripVertical className="ml-2 size-3.5 shrink-0 text-slate-300" /><button disabled={!editable} className="min-w-0 flex-1 py-3 text-left" onClick={() => chooseOperation(item)}><p className="truncate text-sm font-medium">{item.label}</p><p className="mt-0.5 truncate text-[11px] text-slate-400">{item.method ?? item.cost_basis} · v{item.id.split(":").at(-1)}</p></button>
              {template.operations.some((operation) => `operation:${operation.id}:${operation.version}` === item.id) && <button aria-label={`编辑标准工序 ${item.label}`} onClick={() => setOperationEditor(template.operations.findIndex((operation) => `operation:${operation.id}:${operation.version}` === item.id))} className="mr-2 grid size-7 place-items-center rounded-lg text-slate-400 hover:bg-slate-100"><Settings2 className="size-3.5" /></button>}
            </div>)}</div>
            {editable && <Button variant="outline" className="w-full" onClick={() => { const index = template.operations.length; update((copy) => { copy.operations.push({ id: nextRouteKey("operation", opChoices.map((item) => item.id.split(":")[1])), version: "1.0.0", label: "新标准工序", cost_basis: template.output_basis }); }); setOperationEditor(index); }}><Plus className="size-4" /> 新建标准工序</Button>}

          </>}
          {panel === "sequence" && <>
            <p className="text-xs leading-5 text-slate-500">每行一道工序，以 [阶段名称] 分组。可从专家清单或表格直接粘贴；生成前检查下方结构。</p>
            <Button size="sm" variant="outline" onClick={() => setSequenceText("[前处理]\n清洗\n涂膜\n[内层线路 | 重复 | inner_surfaces]\n曝光\n显影\n蚀刻\n[整板加工]\n压合\n钻孔\n检验")}>填入分阶段示例</Button>
            <Textarea aria-label="批量工序清单" className="min-h-52 font-normal leading-7" placeholder={"[前处理]\n清洗\n[对象加工 | 重复 | items]\n加工\n[整体加工]\n装配\n检验"} value={sequenceText} onChange={event => setSequenceText(event.target.value)} onKeyDown={event => { if ((event.ctrlKey || event.metaKey) && event.key === "Enter") { event.preventDefault(); importSequence(); } }} />
            {sequencePlan.error && <p role="alert" className="text-xs text-rose-600">{sequencePlan.error}</p>}
            <p className="text-xs text-slate-500">{sequencePlan.plan?.group_count ?? 0} 个阶段 · {sequencePlan.plan?.step_count ?? 0} 道工序{selected ? ` · 接在“${selectedGroup?.label ?? selectedStep?.label}”后` : " · 添加到主路线"}</p>
            {selectedStep?.parent && sequencePlan.plan?.group_count ? <p className="text-xs text-amber-700">当前选中组内工序。请点击空白取消选择，或选中主路线上的阶段后再创建阶段。</p> : null}
            <div className="max-h-44 space-y-2 overflow-y-auto">{sequencePlan.plan?.blocks.map((block, index) => <div key={index} className="rounded-lg border p-2 text-xs"><b>{block.label ?? "主路线"}</b><span className="ml-2 text-slate-400">{block.mode === "repeat" ? `重复 · ${block.iteration_set}` : "执行一次"}</span><p className="mt-1 text-slate-500">{block.steps.map(step => step.label).join(" → ")}</p></div>)}</div>
            <Button className="w-full" disabled={!editable || !sequencePlan.plan?.step_count || Boolean(selectedStep?.parent && sequencePlan.plan?.group_count)} onClick={importSequence}><Plus className="size-4" /> 生成阶段和工序 <span className="ml-auto text-[10px] opacity-60">⌘↵</span></Button>
            <details className="text-xs leading-5 text-slate-500"><summary className="cursor-pointer">粘贴格式与版本匹配</summary><p className="mt-2">[名称]：整组执行一次；[名称 | 重复 | items | 顺序]：按对象顺序重复，省略“顺序”则对象间独立。[主路线] 回到主层级。每行工序可带第二列表格数据：operation:标识:版本。</p><p className="mt-2">名称唯一时复用标准工序；同名多版本需指定引用。新名称创建待配置的标准工序草稿。每份模板最多 200 道工序，支持一层工序组。</p></details>
          </>}
          {panel === "outline" && <>
            <Input ref={outlineInput} autoFocus aria-label="搜索工艺目录" placeholder="搜索工序或阶段 · ⌘K" value={outlineSearch} onChange={event => setOutlineSearch(event.target.value)} onKeyDown={event => { if (event.key === "Enter" && outlineMatches[0]) focusCard(outlineMatches[0].id); }} />
            <div className="flex gap-2"><Button size="sm" variant="outline" onClick={() => { setCollapsedGroups(template.groups.map(group => group.id)); if (selectedStep?.parent) inspectCard(selectedStep.parent); }}>折叠全部阶段</Button><Button size="sm" variant="ghost" onClick={() => setCollapsedGroups([])}>展开全部</Button></div>
            <p className="text-xs leading-5 text-slate-500">按工艺先后定位；折叠只改变视图，案例展开与计算仍保留全部工序。</p>
            <div className="space-y-1">{visibleOutline.map(item => <div key={item.id} className={`flex items-center rounded-lg ${item.parent ? "ml-4" : ""} ${selectedIds.includes(item.id) ? "bg-indigo-50 text-indigo-700" : "hover:bg-slate-50"}`}>
              {item.isGroup ? <button className="grid size-7 shrink-0 place-items-center text-amber-600" aria-label={`${collapsedGroups.includes(item.id) ? "展开" : "折叠"}目录阶段 ${item.id}`} onClick={() => toggleGroup(item.id)}>{collapsedGroups.includes(item.id) ? <ChevronRight className="size-3.5" /> : <ChevronDown className="size-3.5" />}</button> : <span className="w-7 shrink-0 text-center text-[10px] text-slate-400">{outline.filter(entry => !entry.isGroup).findIndex(entry => entry.id === item.id) + 1}</span>}
              <button className="min-w-0 flex-1 truncate py-2 text-left text-xs" aria-label={`定位 ${item.label} ${item.id}`} onClick={() => focusCard(item.id)}>{item.label}{item.isGroup && <span className="ml-2 text-[10px] text-slate-400">{template.steps.filter(step => step.parent === item.id).length} 道</span>}</button>
            </div>)}</div>
          </>}
          {panel === "cases" && <>
            <div className="flex flex-wrap gap-1.5">{template.cases.map((sample) => <button key={sample.id} onClick={() => { setCaseId(sample.id); setPreview(null); setCostResult(null); }} className={`rounded-lg border px-2.5 py-1.5 text-xs ${caseId === sample.id ? "border-indigo-300 bg-indigo-50 text-indigo-700" : "border-slate-200 text-slate-500"}`}>{sample.id}</button>)}{editable && <button aria-label="新增案例" className="grid size-7 place-items-center rounded-lg border border-dashed text-slate-400" onClick={() => { const id = nextRouteKey("case", template.cases.map((sample) => sample.id)); update((copy) => { copy.cases.push({ id, facts: {}, sets: Object.fromEntries(copy.groups.filter(g=>g.group_mode!=='conditional').map((group) => [group.iteration_set, [{ id: "item_1" }]])), expected_operations: copy.steps.length }); }); setCaseId(id); }}><Plus className="size-3.5" /></button>}</div>
            {currentCase && <>
              <div className="grid grid-cols-2 gap-2"><label className="text-xs text-slate-500">案例标识<Input className="mt-1" disabled={!editable} value={currentCase.id} onChange={(event) => { updateCase((sample) => { sample.id = event.target.value; }); setCaseId(event.target.value); }} /></label><label className="text-xs text-slate-500">预期工序数<Input className="mt-1" type="number" min="0" disabled={!editable} value={currentCase.expected_operations} onChange={(event) => updateCase((sample) => { sample.expected_operations = Number(event.target.value); })} /></label></div>
              <details className="rounded-xl border border-slate-200 p-3"><summary className="cursor-pointer text-sm font-medium">场景事实</summary><div className="mt-3"><FactRows facts={currentCase.facts} editable={editable} onChange={(facts) => updateCase((sample) => { sample.facts = facts; })} /></div></details>
              {Object.entries(currentCase.sets).map(([name, items]) => <section key={name} className="space-y-2 rounded-xl border p-3"><div className="flex items-center gap-2"><h3 className="min-w-0 flex-1 truncate text-xs font-semibold">{name} · {items.length} 个对象</h3>{editable && <><button aria-label={`添加对象 ${name}`} onClick={() => updateCase((sample) => { sample.sets[name].push({ id: nextRouteKey("item", sample.sets[name].map((item) => String(item.id))) }); })} className="grid size-6 place-items-center rounded hover:bg-slate-100"><Plus className="size-3.5" /></button><button aria-label={`删除集合 ${name}`} onClick={() => updateCase((sample) => { delete sample.sets[name]; })} className="grid size-6 place-items-center rounded text-slate-400 hover:bg-rose-50"><Trash2 className="size-3.5" /></button></>}</div>
                {items.map((item, index) => <details key={index} className="rounded-lg bg-slate-50 p-2"><summary className="cursor-pointer text-xs text-slate-600">{String(item.id)}</summary><div className="mt-2 space-y-2"><div className="flex items-center gap-1"><Input aria-label="对象标识" disabled={!editable} value={String(item.id)} onChange={(event) => updateCase((sample) => { sample.sets[name][index].id = event.target.value; })} />{editable && <button aria-label={`删除对象 ${name} ${index}`} onClick={() => updateCase((sample) => { sample.sets[name].splice(index, 1); })}><Trash2 className="size-3.5 text-slate-400" /></button>}</div><FactRows editable={editable} facts={Object.fromEntries(Object.entries(item).filter(([key]) => key !== "id"))} onChange={(facts) => updateCase((sample) => { sample.sets[name][index] = { id: item.id, ...facts }; })} /></div></details>)}
              </section>)}
              {editable && <div className="flex gap-2"><Input aria-label="新集合名称" placeholder="对象集合名称" value={newSet} onChange={(event) => setNewSet(event.target.value)} /><Button variant="outline" size="sm" disabled={!newSet.trim() || newSet.trim() in currentCase.sets} onClick={() => { updateCase((sample) => { sample.sets[newSet.trim()] = [{ id: "item_1" }]; }); setNewSet(""); }}>添加</Button></div>}
              <Button className="w-full" disabled={busy || !template.steps.length || !(canEdit || canReview || canPublish)} onClick={() => void runPreview()}>{busy ? <Loader2 className="size-4 animate-spin" /> : <FlaskConical className="size-4" />} 运行当前案例</Button>
            </>}
            {preview && <section className={`space-y-3 rounded-xl border p-3 ${preview.matches_expected ? "border-emerald-200 bg-emerald-50" : "border-amber-200 bg-amber-50"}`}><p className="flex items-center gap-2 text-sm font-medium">{preview.matches_expected ? <CheckCheck className="size-4 text-emerald-600" /> : <CircleAlert className="size-4 text-amber-600" />}展开 {preview.operation_count} 道工序 · 预期 {currentCase?.expected_operations}</p><p className="text-xs text-slate-500">次数已标注在白板工序上。</p>{Object.entries(preview.operation_counts).map(([id, count]) => <p key={id} className="flex justify-between gap-2 text-xs"><span className="truncate">{opChoices.find((item) => item.id === id)?.label ?? id}</span><b>× {count}</b></p>)}{editable && !preview.matches_expected && <Button variant="outline" size="sm" onClick={() => updateCase((sample) => { sample.expected_operations = preview.operation_count; })}>以此结果更新预期</Button>}</section>}
            {preview?.decisions?.length ? <section className="space-y-2 border-t pt-3"><h3 className="text-xs font-semibold">决策命中记录</h3>{preview.decisions.map((trace,index)=><div key={index} className="rounded-lg bg-violet-50 p-2 text-xs"><p>{trace.route_key}{trace.item_id?` / ${trace.item_id}`:''}</p><p className="mt-1 text-[10px] text-slate-500">{trace.decision_ref} · {trace.used_default?'默认输出':trace.matched_rules.join(', ')}</p><p className="mt-1 break-all">{JSON.stringify(trace.result)}</p></div>)}</section>:null}
            {preview?.skipped?.length ? <p className="text-xs text-slate-500">已跳过：{preview.skipped.map(s=>s.label).join('、')}</p>:null}
            <p className="text-[11px] leading-5 text-slate-400">试展开显示逻辑工序次数；成本侧栏按批量、时长和资源单价计算。</p>
          </>}
          {panel === "governance" && <>
            {editable && <section className="space-y-3"><label className="block text-xs text-slate-500">提交说明<Textarea className="mt-1.5" value={reason} onChange={(event) => setReason(event.target.value)} /></label><Button className="w-full" disabled={busy || !reason.trim() || !template.steps.length || !template.cases.length} onClick={() => void submit()}><Save className="size-4" /> 提交独立审核</Button>{draftId && <p className="text-xs text-emerald-600">提案已提交 · {draftId.slice(0, 8)}</p>}</section>}
            {semanticChanges.length > 0 && <details className="rounded-xl border border-slate-200 p-3"><summary className="cursor-pointer text-xs font-medium">本版本结构变化</summary><ul className="mt-2 space-y-1 text-xs text-slate-500">{semanticChanges.map((item) => <li key={item}>{item}</li>)}</ul></details>}
            <p className="text-xs text-slate-500">打开提案后，可在白板中查看工艺，并运行案例验证。</p>
            {changesets.map((change) => <article key={change.id} className="space-y-3 rounded-xl border border-slate-200 p-3"><div className="flex items-start gap-2"><p className="min-w-0 flex-1 text-sm font-medium">{change.patch?.template?.label ?? change.title}</p><span className="shrink-0 rounded-full bg-slate-100 px-2 py-0.5 text-[10px] text-slate-500">{statusNames[change.status] ?? change.status}</span></div><p className="text-[11px] text-slate-400">{change.actor} · {change.id.slice(0, 8)}</p><div className="flex flex-wrap gap-2"><Button variant="outline" size="sm" disabled={busy} onClick={() => inspectProposal(change)}>查看工艺</Button>{canReview && ["proposed", "review_required"].includes(change.status) && <><Button size="sm" disabled={busy || change.actor === session.principal} onClick={() => void govern(change, "approved")}><Check className="size-3.5" /> 批准</Button><Button variant="ghost" size="sm" disabled={busy} onClick={() => void govern(change, "changes_requested")}>退回</Button></>}{canPublish && change.status === "approved" && <><Button variant="outline" size="sm" disabled={busy} onClick={() => void govern(change, "apply")}>应用真源</Button>{change.source_revision && <Button size="sm" disabled={busy} onClick={() => void govern(change, "publish")}>验证发布</Button>}</>}</div></article>)}
            {!changesets.length && <p className="rounded-xl border border-dashed p-4 text-sm text-slate-400">暂无待审模板。</p>}
            <p className="text-[11px] leading-5 text-slate-400">专家编写、独立审核和发布按角色授权。应用真源后按团队 Git 流程提交。</p>
          </>}
        </div>
      </aside>}
      <section aria-label="工艺白板区域" tabIndex={0} className="@container relative flex min-w-0 flex-1 flex-col outline-none">
        <div className="z-10 flex h-14 shrink-0 items-center gap-2 border-b border-slate-200 bg-white/90 px-4">
          {editable ? <><Button size="sm" aria-label="下一工序" title="下一工序 · N" onClick={() => append(selected || undefined)}><Plus className="size-4" /><span className="hidden @min-[450px]:inline">下一工序</span> <kbd className="ml-2 hidden text-[10px] opacity-50 @min-[700px]:inline">N</kbd></Button><Button variant="outline" size="sm" onClick={()=>addGroup()}><Layers3 className="size-4" /><span className="hidden @min-[600px]:inline">重复组</span></Button><Button variant="outline" size="icon-sm" aria-label="添加工艺阶段" title="整组执行一次的工艺阶段" onClick={()=>addGroup('conditional',false)}><Workflow className="size-4"/></Button><Button variant="outline" size="icon-sm" aria-label="添加条件工序组" title="条件工序组" onClick={()=>addGroup('conditional')}><Diamond className="size-4"/></Button><Button variant="ghost" size="icon-sm" aria-label="批量粘贴工序" title="批量粘贴工序清单" onClick={() => setPanel("sequence")}><ClipboardPaste className="size-4" /></Button>
            <span className="mx-1 h-5 w-px bg-slate-200" /><Button variant="ghost" size="icon-sm" aria-label="撤销" title="撤销 · ⌘Z" disabled={!undoAvailable} onClick={() => undo()}><Undo2 className="size-4" /></Button><Button variant="ghost" size="icon-sm" aria-label="重做" title="重做 · ⌘⇧Z" disabled={!redoAvailable} onClick={() => undo(true)}><Redo2 className="size-4" /></Button>
          </> : <p className="flex items-center gap-2 text-xs text-slate-500"><LockKeyhole className="size-3.5" />{proposalView ? "审核提案 · 可查看工艺与验证案例" : "已发布模板 · 新建版本后可编辑"}</p>}
          <div className="ml-auto flex items-center gap-1"><button title="自动布局" aria-label="自动布局" className="flex shrink-0 items-center gap-1.5 rounded-lg px-2 py-1.5 text-xs text-slate-500 hover:bg-slate-100" onClick={arrange}><Workflow className="size-3.5" /><span className="hidden @min-[700px]:inline">自动布局</span></button><Button variant="ghost" size="icon-sm" aria-label="适应画布" title="适应画布" onClick={() => void flow?.fitView({ padding: 0.2, maxZoom: 1, duration: 300 })}><Focus className="size-4" /></Button></div>
        </div>
        {error && <div role="alert" className="absolute left-4 right-4 top-16 z-30 flex items-start gap-2 rounded-xl border border-rose-200 bg-rose-50 p-3 text-sm text-rose-700 shadow-sm"><CircleAlert className="mt-0.5 size-4 shrink-0" /><p className="flex-1">{error}</p><button aria-label="关闭错误提示" onClick={() => setError("")}><X className="size-4" /></button></div>}
        <div className="relative min-h-0 flex-1">
          <RouteCanvas template={template} operations={opChoices} editable={editable} selectedIds={selectedIds} selectedEdges={selectedEdges} layoutRevision={layoutRevision} restoredPositions={restoredPositions} counts={preview ? stageCounts : undefined} resolvedOperations={preview?resolvedOperations:undefined} collapsedGroups={collapsedGroups} onToggleGroup={toggleGroup}
            onSelect={chooseSelection}
            onRename={rename} onAppend={append} onInside={addInside} onDuplicate={(id) => duplicate([id])}
            onInspect={inspectCard}
            onConnect={(connection) => { if (connection.source && connection.target) attempt(() => commit(connectRoute(templateRef.current, connection.source!, connection.target!))); }}
            onDeleteEdge={(from, to) => update((copy) => { copy.edges = copy.edges.filter((edge) => edge.from !== from || edge.to !== to); })}
            onMove={(id, parent) => attempt(() => { commit(moveRouteStep(templateRef.current, id, parent)); if (parent) setCollapsedGroups(old => old.filter(group => group !== parent)); })}
            onDragStart={() => { dragStart.current = snapshot(); }} onDragEnd={finishDrag}
            onDropOperation={(ref, parent) => { const operation = opChoices.find((item) => item.id === ref); if (!operation) return;
              return parent ? addSequence([operation.label], undefined, parent, operation) : append(selected || undefined, operation); }} onFlow={setFlow} />
          {editable && selectedIds.length > 1 && <div className="absolute bottom-6 left-1/2 z-10 flex -translate-x-1/2 items-center gap-2 rounded-xl border border-slate-200 bg-white p-2 shadow-lg"><span className="px-2 text-xs text-slate-500">已选 {selectedIds.length} 项</span><Button size="sm" variant="outline" onClick={connectSelection}><ArrowRight className="size-3.5" /> 串联</Button><Button size="sm" variant="ghost" onClick={() => duplicate(selectedIds)}><Copy className="size-3.5" /> 复制</Button><Button size="sm" variant="ghost" onClick={() => removeSelected()}><Trash2 className="size-3.5" /></Button></div>}
          {template.steps.length === 0 && template.groups.length === 0 && <div className="pointer-events-none absolute inset-0 grid place-items-center"><p className="rounded-xl border border-dashed border-slate-300 bg-white/80 px-8 py-6 text-sm text-slate-400">从工序库拖入工序，或粘贴一段工序清单开始编排</p></div>}
        </div>
        <footer className="flex h-8 shrink-0 items-center gap-3 border-t border-slate-200 bg-white px-4 text-[10px] text-slate-400"><span className="truncate">点击名称直接编辑 · 拖线连接 · 拖动工序调整布局</span><span className="ml-auto hidden @min-[850px]:inline">Shift 框选 · N 追加 · ⌘K 定位 · ⌘⇧D 复制并串联 · ⌘Z 撤销 · Delete 删除</span></footer>
      </section>
      {showInspector && <aside aria-label="工序属性" className={`absolute inset-y-0 right-0 z-20 flex w-[320px] max-w-[calc(100vw-80px)] shrink-0 flex-col border-l border-slate-200 bg-white shadow-lg min-[768px]:static min-[768px]:shadow-none ${panel ? "max-[1099px]:hidden" : ""}`}>
        <div className="flex h-14 shrink-0 items-center gap-2 border-b border-slate-100 px-4"><h2 className="flex-1 text-sm font-semibold">{selectedIds.length > 1 ? `批量属性 · ${batchSteps.length} 道` : "工序属性"}</h2>
          <button aria-label="上一工序属性" title="上一工序 · Alt↑" disabled={navigationIndex <= 0} onClick={() => focusCard(outline[navigationIndex - 1].id)} className="grid size-7 place-items-center rounded hover:bg-slate-100 disabled:opacity-30"><ChevronLeft className="size-4" /></button>
          <button aria-label="下一工序属性" title="下一工序 · Alt↓" disabled={navigationIndex < 0 || navigationIndex >= outline.length - 1} onClick={() => focusCard(outline[navigationIndex + 1].id)} className="grid size-7 place-items-center rounded hover:bg-slate-100 disabled:opacity-30"><ChevronRight className="size-4" /></button>
          <button aria-label="收起属性侧栏" onClick={clearSelection} className="grid size-7 place-items-center rounded text-slate-400 hover:bg-slate-100"><X className="size-4" /></button>
        </div>
        <div key={selectedIds.join('|')} className="min-h-0 flex-1 space-y-4 overflow-y-auto p-4">
          {selectedIds.length > 1 && batchSteps.length ? <BatchProperties steps={batchSteps} editable={editable} allowItem={batchSteps.every(step => step.parent && template.groups.find(group => group.id === step.parent)?.group_mode !== 'conditional')} decisions={decisionChoices} onApply={applyBatch} onLibrary={() => { setLibraryMode('batch'); setPanel('operations'); }} onDecisions={() => setPanel('decisions')} /> : cardProperties}
          {selectedGroup && selectedIds.length === 1 && <div className="space-y-2 border-t pt-3"><Button className="w-full" size="sm" variant="outline" disabled={!batchSteps.length} onClick={() => { setCollapsedGroups(old => old.filter(id => id !== selectedGroup.id)); chooseSelection(batchSteps.map(step => step.id), []); }}>批量配置组内 {batchSteps.length} 道工序</Button>{editable && <Button className="w-full" size="sm" variant="ghost" onClick={() => { const tail = outline.filter(item => item.parent === selectedGroup.id).at(-1); if (tail) { setCollapsedGroups(old => old.filter(id => id !== selectedGroup.id)); inspectCard(tail.id); } setPanel('sequence'); }}>批量追加组内工序</Button>}</div>}
          {editable && selectedIds.length === 1 && <Button className="w-full" size="sm" variant="outline" onClick={() => duplicateAfter(selected)}><Copy className="size-3.5" />复制并接在后面 <kbd className="ml-auto text-[10px] text-slate-400">⌘⇧D</kbd></Button>}
        </div>
      </aside>}
    </div>}
  </main>;
}
