"use client";

import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import { usePathname, useSearchParams } from "next/navigation";
import { ArrowLeft, ArrowUpRight, Check, FileCheck2, FlaskConical, GitPullRequestArrow, Loader2, RefreshCw, Search, Settings2, X } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { applyChangeSet, publishChangeSet, reviewChangeSet } from "../knowledgeos-api";
import { useWorkspaceSession } from "../workspace-session";
import { announceReviewChange } from "../workspace-navigation";
import { SignInPrompt } from "../sign-in-prompt";
import { NavigationToggle } from "../workspace-navigation-context";
import { BatchReview } from "../batch-review";
import { displayDate } from "../instances/instances-api";
import { previewTemplate, type ExpertTemplate, type Preview } from "../templates/template-api";
import { RouteCanvas } from "../templates/route-canvas";
import { actorText, assistanceOf, batchReviewRecords, changeCategory, changeStatusLabels, isTemplateChange, loadChangeRecords, reviewAccess, supportsSourceApplication, type ChangeRecord } from "./changesets-api";
import { AssistanceSection, AssistantMetrics } from "./assistance-review";
import { isPending, nextAfter, parseReviewView, queueFor, reviewViews, riskLabels, viewCounts, type ReviewView } from "./review-queue";
import { operationRows, operationTarget, patchRows, workspaceLink, type DiffRow } from "./change-diff";
import { updatePolicy } from "../agents/agents-api";
import { previewLogic, object, type LogicDocument, type LogicKind, type TestReport } from "../rules/logic-api";

const noop = () => {};

function LogicProposal({ change }: { change: ChangeRecord }) {
  const [report, setReport] = useState<TestReport | null>(null);
  const [busy, setBusy] = useState(false), [error, setError] = useState("");
  const operation = change.operations[0], after = object(operation?.after);
  const kind = (change.patch.kind ?? operation?.targetKind ?? operation?.target_kind) as LogicKind;
  const definition = (change.patch.definition ?? { ...object(after.config), id: String(after.id ?? "").replace(/^[^:]+:/, ""), label: after.label, description: after.description, status: { lifecycle: after.lifecycle } }) as LogicDocument;
  async function verify() {
    setBusy(true); setError(""); setReport(null);
    try { setReport((await previewLogic(kind, definition)).tests); }
    catch (cause) { setError(cause instanceof Error ? cause.message : "验证失败"); }
    finally { setBusy(false); }
  }
  return <section className="change-section"><h3>业务逻辑验证</h3><p className="change-section-note">检查适用范围、依赖版本与预期结果；重新运行提案中的全部案例。</p><Button size="sm" variant="outline" disabled={busy} onClick={() => void verify()}>{busy ? <Loader2 className="animate-spin" /> : <FlaskConical />}验证逻辑案例</Button>{error && <p role="alert" className="text-sm text-destructive">{error}</p>}{report && <div className="logic-test-report" role="status"><strong>{report.passed ? "全部案例通过" : "案例未全部通过"}</strong>{report.cases.map(item => <details className="change-json" key={item.id}><summary>{item.passed ? "✓" : "×"} {item.id}{item.error && ` · ${item.error}`}</summary><pre>{JSON.stringify({ expected: item.expected, actual: item.actual }, null, 2)}</pre></details>)}</div>}<details className="change-json"><summary>完整业务逻辑与案例</summary><pre>{JSON.stringify(definition, null, 2)}</pre></details><Link className="logic-link" href="/rules">前往规则与决策工作台</Link></section>;
}

function TemplateProposal({ template }: { template: ExpertTemplate }) {
  const { session } = useWorkspaceSession();
  const canPreview = session?.permissions.some(permission => ["template_edit", "template_review", "template_publish"].includes(permission));
  const [selected, setSelected] = useState<string[]>([]);
  const [collapsed, setCollapsed] = useState<string[]>([]);
  const [caseId, setCaseId] = useState(template.cases[0]?.id ?? "");
  const [result, setResult] = useState<Preview | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const item = template.steps.find(step => step.id === selected[0]) ?? template.groups.find(group => group.id === selected[0]);
  async function runCase() {
    const sample = template.cases.find(value => value.id === caseId);
    if (!sample) return;
    setBusy(true); setError(""); setResult(null);
    try { setResult(await previewTemplate(template, sample)); }
    catch (cause) { setError(cause instanceof Error ? cause.message : "案例验证失败"); }
    finally { setBusy(false); }
  }
  return <section className="change-section"><h3>工艺与案例</h3><p className="change-section-note">{template.label} · v{template.version} · {template.steps.length} 道工序</p>
    <div className="change-template-canvas"><RouteCanvas template={template} operations={template.operations.map(operation => ({ ...operation, id: `operation:${operation.id}:${operation.version}` }))} editable={false} selectedIds={selected} selectedEdges={[]} layoutRevision={0} restoredPositions={null} collapsedGroups={collapsed} onToggleGroup={id => setCollapsed(values => values.includes(id) ? values.filter(value => value !== id) : [...values, id])} onSelect={ids => setSelected(ids)} onInspect={id => setSelected([id])} onRename={noop} onAppend={noop} onInside={noop} onDuplicate={noop} onConnect={noop} onDeleteEdge={noop} onMove={noop} onDragStart={noop} onDragEnd={noop} onDropOperation={() => undefined} onFlow={noop} onWorkspaceFocus={noop} /></div>
    {item && <details open className="change-json"><summary>所选工序 · {item.label}</summary><pre>{JSON.stringify(item, null, 2)}</pre></details>}
    <div className="change-case-controls"><label><span className="sr-only">审核验证案例</span><select aria-label="审核验证案例" value={caseId} onChange={event => { setCaseId(event.target.value); setResult(null); }}>{template.cases.map(sample => <option key={sample.id} value={sample.id}>{sample.id} · 预期 {sample.expected_operations} 道工序</option>)}</select></label><Button size="sm" variant="outline" disabled={busy || !caseId || !canPreview} onClick={() => void runCase()}>{busy ? <Loader2 className="animate-spin" /> : <FlaskConical />}验证案例</Button></div>
    {error && <p role="alert" className="text-sm text-destructive">{error}</p>}
    {result && <div role="status" className="change-case-result"><p>{result.matches_expected ? "案例通过" : "与专家预期不一致"} · 展开 {result.operation_count} 道工序</p><details className="change-json"><summary>展开结果与决策过程</summary><pre>{JSON.stringify(result, null, 2)}</pre></details></div>}
  </section>;
}

function DiffTable({ rows, created }: { rows: DiffRow[]; created: boolean }) {
  if (!rows.length) return <p className="change-section-note">没有字段变化。</p>;
  return <table className="change-diff"><thead><tr><th scope="col">字段</th>{!created && <th scope="col">原值</th>}<th scope="col">{created ? "值" : "新值"}</th></tr></thead>
    <tbody>{rows.map(row => <tr key={row.field} data-changed={row.changed}><th scope="row">{row.field}</th>{!created && <td>{row.before}</td>}<td>{row.after}</td></tr>)}</tbody></table>;
}

function OperationDiffs({ operations }: { operations: Record<string, unknown>[] }) {
  return <section className="change-section"><h3>定义差异 <span>{operations.length} 项</span></h3>
    {operations.map((operation, index) => {
      const target = operationTarget(operation);
      return <details key={String(operation.id ?? index)} className="change-json change-operation" open={operations.length <= 3}>
        <summary><strong>{target.label || target.id}</strong>{target.label && <code>{target.id}</code>}<span>{target.type} · {target.kind}</span></summary>
        <DiffTable rows={operationRows(operation)} created={operation.type === "create"} />
      </details>;
    })}
  </section>;
}

function PatchContent({ change }: { change: ChangeRecord }) {
  const rows = patchRows(change.patch);
  const title = change.patch.op === "semantic_control" ? "术语与语义变更" : change.patch.op === "extraction_asset" ? "抽取配置内容" : "知识变更内容";
  return <section className="change-section"><h3>{title}{rows.length > 0 && <span>{rows.length} 处</span>}</h3>
    {rows.length ? <table className="change-diff"><thead><tr><th scope="col">位置</th><th scope="col">操作</th><th scope="col">新值</th></tr></thead>
      <tbody>{rows.map((row, index) => <tr key={`${row.path}:${index}`} data-changed="true"><th scope="row"><code>{row.path}</code></th><td>{row.op}</td><td>{row.value}</td></tr>)}</tbody></table>
      : <details open className="change-json"><summary>{title}</summary><pre>{JSON.stringify(change.patch, null, 2)}</pre></details>}
  </section>;
}

type ActionRunner = (action: () => Promise<unknown>, message: string, advance?: boolean) => Promise<void>;

/** The decision stays in view at the bottom of the detail, whatever the length of the proposal. */
function DecisionBar({ change, blocked, onAction }: { change: ChangeRecord; blocked: boolean; onAction: ActionRunner }) {
  const { session } = useWorkspaceSession();
  const access = reviewAccess(change, session);
  const [note, setNote] = useState("");
  const [sourceRevision, setSourceRevision] = useState(change.source_revision ?? "");
  // Refresh the editable revision after an external review updates the record.
  // eslint-disable-next-line react-hooks/set-state-in-effect
  useEffect(() => { setSourceRevision(change.source_revision ?? ""); }, [change.source_revision]);
  if (isPending(change)) {
    if (!access.canReview) return <footer className="change-decision-bar"><p className="change-section-note">当前账户可以查看此提案。{isTemplateChange(change) ? "需要模板审核权限才能处理。" : "需要审核权限才能处理。"}</p></footer>;
    return <footer className="change-decision-bar" aria-label="审核决定">
      {access.approvalNote && <p className="change-section-note">{access.approvalNote}</p>}
      <div className="change-decision-row">
        <Textarea aria-label="审核意见" rows={1} value={note} onChange={event => setNote(event.target.value)} placeholder="审核意见：记录判断依据，或说明需要补充的内容" disabled={blocked} />
        <Button variant="outline" disabled={blocked || !note.trim()} onClick={() => void onAction(() => reviewChangeSet(change.id, "rejected", note.trim()), "已驳回提案", true)}><X />驳回</Button>
        <Button variant="outline" disabled={blocked || !note.trim()} onClick={() => void onAction(() => reviewChangeSet(change.id, "changes_requested", note.trim()), "已要求修改", true)}>要求修改</Button>
        <Button disabled={blocked || !access.canApprove || !note.trim()} onClick={() => void onAction(() => reviewChangeSet(change.id, "approved", note.trim()), "审核已批准", true)}><Check />批准</Button>
      </div>
    </footer>;
  }
  if (change.status !== "approved") return null;
  if (!access.canPublish) return <footer className="change-decision-bar"><p className="change-section-note">需要{isTemplateChange(change) ? "模板" : ""}发布权限才能应用真源或发布。</p></footer>;
  return <footer className="change-decision-bar" aria-label="应用与发布">
    <p className="change-section-note">{supportsSourceApplication(change) ? "应用后验证真源并编译，成功后登记发布。" : "此类真源请按 Git 流程应用；完成后填写修订并验证发布。"}</p>
    <div className="change-decision-row">
      <Input aria-label="待发布真源修订" value={sourceRevision} onChange={event => setSourceRevision(event.target.value)} disabled={blocked} placeholder="真源修订：应用真源后自动填入，也可填写已完成的 Git 修订" />
      {supportsSourceApplication(change) && <Button variant="outline" disabled={blocked || Boolean(change.source_revision)} onClick={() => void onAction(async () => { const revision = await applyChangeSet(change.id); setSourceRevision(revision); }, "已应用真源，等待验证发布")}>应用到真源</Button>}
      <Button disabled={blocked || !sourceRevision.trim()} onClick={() => void onAction(() => publishChangeSet(change.id, sourceRevision.trim()), "真源已验证并发布", true)}><FileCheck2 />验证发布</Button>
    </div>
  </footer>;
}

function ChangeDetail({ change, blocked, onAction, onBack }: { change: ChangeRecord; blocked: boolean; onAction: ActionRunner; onBack: () => void }) {
  const detailRef = useRef<HTMLElement>(null);
  useEffect(() => { detailRef.current?.focus({ preventScroll: true }); }, []);
  return <article ref={detailRef} tabIndex={-1} className="change-detail outline-none" aria-label="变更详情" onKeyDown={event => { if (event.key === "Escape") { event.stopPropagation(); onBack(); } }}>
    <div className="change-detail-topline"><span className="detail-navigation-access"><NavigationToggle /></span><Button className="change-back" size="sm" variant="ghost" onClick={onBack}><ArrowLeft />返回队列</Button><span>{changeCategory(change)}</span><code>{change.id}</code></div>
    <div className="change-detail-content">
      <div className="change-title"><div className="flex flex-wrap items-center gap-2"><span className="neutral-badge">{changeStatusLabels[change.status] ?? change.status}</span><span className="review-risk" data-risk={change.risk}>{riskLabels[change.risk] ?? change.risk}{change.risk === "high" ? " · 独立审核" : ""}</span></div><h2>{change.title}</h2><p>{change.reason}</p><span>{actorText(change)} · {displayDate(change.created_at)}</span>{(() => { const link = workspaceLink(changeCategory(change), change); return link && <Link className="logic-link" href={link.href}>{link.label}<ArrowUpRight size={13} aria-hidden="true" /></Link>; })()}</div>
      <ol className="change-progress" aria-label="变更发布流程">{["提交提案", "独立审核", "应用真源", "验证发布"].map((label, index) => <li key={label} data-complete={index === 0 || index === 1 && ["approved", "published"].includes(change.status) || index === 2 && Boolean(change.source_revision) || index === 3 && change.status === "published"}><span>{index + 1}</span>{label}</li>)}</ol>
      {assistanceOf(change) && <AssistanceSection assistance={assistanceOf(change)!} />}
      {change.operations.length > 0 && <OperationDiffs operations={change.operations} />}
      {isTemplateChange(change) && change.patch.template && <TemplateProposal template={change.patch.template} />}
      {changeCategory(change) === "业务逻辑" && <LogicProposal change={change} />}
      {!change.operations.length && !isTemplateChange(change) && <PatchContent change={change} />}
      <section className="change-section"><h3>来源与记录</h3><dl className="property-list"><div><dt>目标真源</dt><dd><code>{change.target_source}</code></dd></div>{change.reviewed_by && <div><dt>审核人</dt><dd>{change.reviewed_by} · {displayDate(change.reviewed_at)}</dd></div>}{change.review_note && <div><dt>审核意见</dt><dd>{change.review_note}</dd></div>}{change.source_revision && <div><dt>真源修订</dt><dd><code>{change.source_revision}</code></dd></div>}{change.published_by && <div><dt>发布人</dt><dd>{change.published_by} · {displayDate(change.published_at)}</dd></div>}</dl><details className="change-json"><summary>原始提案（JSON）</summary><pre>{JSON.stringify(change, null, 2)}</pre></details></section>
    </div>
    <DecisionBar key={change.id} change={change} blocked={blocked} onAction={onAction} />
  </article>;
}

/** Approval policy, shown where review happens; owners can change the personal-mode self-review switch here. */
function ReviewSettings() {
  const { session, setSession } = useWorkspaceSession();
  const policy = session?.policy;
  const canManage = Boolean(session?.permissions.includes("manage"));
  const [busy, setBusy] = useState(false);
  async function setSelfReview(value: boolean) {
    setBusy(true);
    try { const next = await updatePolicy({ self_review_high_risk: value }); setSession(current => current && { ...current, policy: next }); toast.success(value ? "已允许个人模式自审高风险变更" : "已关闭自审"); announceReviewChange(); }
    catch (cause) { toast.error(cause instanceof Error ? cause.message : "无法修改审批策略"); }
    finally { setBusy(false); }
  }
  return <Popover><PopoverTrigger asChild><Button variant="ghost" size="icon-sm" aria-label="审核设置" title="审核设置"><Settings2 /></Button></PopoverTrigger>
    <PopoverContent side="bottom" align="end" className="review-settings space-y-3"><h2 className="text-sm font-medium">审核设置 · {policy?.mode === "organization" ? "组织模式" : "个人模式"}</h2>
      <p className="text-xs leading-5 text-muted-foreground">{policy?.mode === "organization" ? "Agent 与其负责人视为同一责任方，高风险变更须由另一位成员批准。" : "你审核本人名下 Agent 的提案即为独立审核；Agent 之间不相互独立，也不能批准。"}</p>
      <label className="agent-toggle"><input type="checkbox" checked={policy?.human_final_approval ?? true} disabled readOnly /><span>最终批准与发布须由人完成</span></label>
      <label className="agent-toggle"><input type="checkbox" checked={policy?.self_review_high_risk ?? false} disabled={busy || !canManage || !policy || policy.mode !== "personal"} onChange={event => void setSelfReview(event.target.checked)} /><span>允许自审你本人提交的高风险变更（批准记录标注“个人模式自审”；批量审核不适用）</span></label>
      {!canManage && <p className="text-xs text-muted-foreground">需要本机所有者账号才能修改。</p>}
    </PopoverContent></Popover>;
}

export default function ChangesetsWorkspace({ active }: { active: boolean }) {
  const params = useSearchParams();
  const pathname = usePathname();
  const { session } = useWorkspaceSession();
  const canReview = Boolean(session?.permissions.some(permission => permission === "review" || permission === "template_review"));
  const requestedView = parseReviewView(params.get("view")) ?? (pathname.replace(/\/$/, "") === "/inbox" ? "mine" : null);
  const [view, setView] = useState<ReviewView>(requestedView ?? "mine");
  const [records, setRecords] = useState<ChangeRecord[]>([]);
  const [selectedId, setSelectedId] = useState("");
  const [query, setQuery] = useState("");
  const [category, setCategory] = useState("");
  const [status, setStatus] = useState("");
  const [loading, setLoading] = useState(false);
  const [loaded, setLoaded] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [revision, setRevision] = useState(0);
  const lastSelected = useRef<HTMLButtonElement>(null);
  const autoOpened = useRef(false);
  function closeDetail() { setSelectedId(""); requestAnimationFrame(() => lastSelected.current?.focus({ preventScroll: true })); }
  // Clear records from the previous principal when the shared session changes.
  // eslint-disable-next-line react-hooks/set-state-in-effect
  useEffect(() => { setRecords([]); setSelectedId(""); setLoaded(false); autoOpened.current = false; setView(requestedView ?? (canReview ? "mine" : "all")); }, [session?.principal]);
  // Follow external links: a view in the query or path, or a specific proposal.
  // eslint-disable-next-line react-hooks/set-state-in-effect
  useEffect(() => { if (active && requestedView) setView(requestedView); }, [active, requestedView]);
  const targetId = params.get("id") ?? "";
  // eslint-disable-next-line react-hooks/set-state-in-effect
  useEffect(() => { if (active && targetId) { setSelectedId(targetId); autoOpened.current = true; } }, [active, targetId, session?.principal]);
  useEffect(() => {
    if (!active || !session) return;
    const controller = new AbortController();
    // Reset feedback at the start of this abortable external request.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setLoading(true); setError("");
    void loadChangeRecords(controller.signal).then(next => { if (!controller.signal.aborted) { setRecords(next); setLoaded(true); } }).catch(cause => { if (!controller.signal.aborted) setError(cause instanceof Error ? cause.message : "无法加载审核队列"); }).finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [active, session?.principal, revision]);
  const counts = viewCounts(records, session?.principal);
  const queue = queueFor(view, records, session?.principal);
  const visible = view !== "all" ? queue : queue.filter(change => (!category || changeCategory(change) === category) && (!status || (status === "pending" ? isPending(change) : change.status === status)) && `${change.title} ${change.id} ${change.actor} ${change.target_source} ${change.reason}`.toLowerCase().includes(query.trim().toLowerCase()));
  // Open the first item of "待我处理" once, so a reviewer lands on work rather than an empty detail.
  useEffect(() => {
    if (!active || !loaded || autoOpened.current || selectedId || view !== "mine" || !visible.length) return;
    autoOpened.current = true;
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setSelectedId(visible[0].id);
  }, [active, loaded, view, visible, selectedId]);
  const selected = records.find(change => change.id === selectedId);
  async function action(run: () => Promise<unknown>, message: string, advance = false) {
    const next = advance && selectedId ? nextAfter(selectedId, visible) : selectedId;
    setBusy(true); setError("");
    try { await run(); toast.success(advance && next ? `${message}，已打开下一条` : message); setRevision(value => value + 1); announceReviewChange(); if (advance) setSelectedId(next); }
    catch (cause) { setError(cause instanceof Error ? cause.message : "操作未完成，请重试"); }
    finally { setBusy(false); }
  }
  function chooseView(next: ReviewView) { setView(next); setSelectedId(""); }
  const othersNote = session?.policy?.mode === "organization" ? "你或你名下 Agent 提交的高风险变更，需另一位成员批准；你可以驳回或要求修改。" : "你本人提交的高风险变更，需另一位成员批准，或在「审核设置」中开启个人模式自审。";
  return <div className="changesets-workspace">
    {!session && <><h1 className="sr-only">审核</h1><div className="workspace-navigation-access"><NavigationToggle /></div></>}
    {!session ? <SignInPrompt icon={<GitPullRequestArrow size={25} strokeWidth={1.4} />} title="审核">登录后在这里处理所有工作区和 Agent 提交的 ChangeSet：先看待你处理的，再看其他。</SignInPrompt> : <>
      {error && <div role="alert" className="instance-error"><p>{error}</p><Button variant="outline" size="sm" disabled={busy || loading} onClick={() => setRevision(value => value + 1)}>重试</Button></div>}
      <div className="changesets-layout" data-detail-open={Boolean(selected)}><aside className="changeset-directory" aria-label="审核队列"><div className="change-list-filters">
        <div className="directory-heading"><NavigationToggle /><h1>审核</h1><div className="flex items-center gap-1"><Button variant="ghost" size="icon-sm" aria-label="刷新审核队列" title="刷新审核队列" disabled={loading || busy} onClick={() => setRevision(value => value + 1)}><RefreshCw className={loading ? "animate-spin" : ""} /></Button><ReviewSettings /></div></div>
        <div className="review-views" role="tablist" aria-label="审核视图">{reviewViews.filter(item => canReview || item.id === "submitted" || item.id === "all").map(item => <button key={item.id} role="tab" aria-selected={view === item.id} onClick={() => chooseView(item.id)}>{item.label}<span className="review-view-count">{counts[item.id]}</span></button>)}</div>
        {view === "all" && <><div className="instance-search-input mt-3"><Search size={16} aria-hidden="true" /><Input aria-label="搜索变更" placeholder="搜索提案、作者或来源…" value={query} onChange={event => setQuery(event.target.value)} /></div><div className="change-list-selects"><select aria-label="变更类别" value={category} onChange={event => setCategory(event.target.value)}><option value="">全部类别</option>{["本体与定义", "业务逻辑", "抽取配置", "专家模板", "知识变更"].map(value => <option key={value}>{value}</option>)}</select><select aria-label="变更状态" value={status} onChange={event => setStatus(event.target.value)}><option value="">全部状态</option><option value="pending">待审核</option>{Object.entries(changeStatusLabels).filter(([value]) => !["proposed", "review_required"].includes(value)).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></div>
          <div className="change-queue-summary"><p role="status">{loading ? "正在同步…" : `${visible.length} 项变更`}{records.length === 500 && " · 最近 500 项"}</p><BatchReview changeSets={batchReviewRecords(records)} enabled={session.permissions.includes("review") && !busy && !loading && !error} onUpdated={async () => { setRevision(value => value + 1); announceReviewChange(); }} /></div></>}
        {view === "mine" && <p className="review-view-note">可由你批准的提案，高风险在前。做出决定后自动打开下一条。</p>}
        {view === "others" && <p className="review-view-note">{othersNote}</p>}
      </div>
      <div className="changeset-list"><ul>{visible.map(change => <li key={change.id}><button disabled={busy || loading} aria-pressed={selectedId === change.id} onClick={event => { lastSelected.current = event.currentTarget; setSelectedId(change.id); }}><div>{isPending(change) ? <span className="review-risk" data-risk={change.risk}>{riskLabels[change.risk] ?? change.risk}</span> : <span>{changeStatusLabels[change.status] ?? change.status}</span>}<span>{changeCategory(change)}{assistanceOf(change) ? " · 助手起草" : ""}</span></div><strong>{change.title}</strong><small>{actorText(change)} · {displayDate(change.created_at)}</small></button></li>)}</ul>{!loading && visible.length === 0 && <p className="change-list-empty">{view === "mine" ? "没有需要你处理的提案。" : view === "others" ? "没有需要他人批准的提案。" : view === "submitted" ? "你还没有提交过提案。" : records.length ? "没有匹配的变更" : "暂无已提交的变更"}</p>}</div></aside>
        {selected ? <ChangeDetail key={selected.id} change={selected} blocked={busy || loading || Boolean(error)} onAction={action} onBack={closeDetail} /> : <div className="change-empty"><GitPullRequestArrow size={28} strokeWidth={1.3} /><h2>{loading ? "正在读取审核队列…" : view === "mine" && !visible.length ? "都处理完了" : "选择一项变更"}</h2><p>在这里检查提案内容、记录审核意见，并完成真源应用与发布。</p><AssistantMetrics active={active && Boolean(session)} revision={revision} /></div>}
      </div>
    </>}
  </div>;
}
