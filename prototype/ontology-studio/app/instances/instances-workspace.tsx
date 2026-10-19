"use client";

import { useEffect, useRef, useState, type FormEvent } from "react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { ArrowDownLeft, ArrowUpRight, ChevronLeft, ChevronRight, Copy, Database, Braces, Calculator, FileText, Link2, Loader2, LockKeyhole, RefreshCw, Search, SlidersHorizontal, X } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { ApiError, loadSession, loadStudioSnapshot, setSessionToken } from "../knowledgeos-api";
import { useWorkspaceSession } from "../workspace-session";
import { defaultInstanceFilters, displayDate, displayValue, loadInstanceDetail, loadInstances, type InstanceDetail, type InstanceFilters, type InstancePage, type InstanceSelection, type InstanceKind, instanceKindLabels, instanceSourceLabels, instanceStatusLabels } from "./instances-api";

import { NavigationToggle } from "../workspace-navigation-context";
import { RecordInspector } from "./record-inspector";
import { workDomainLabel } from "../work-domains";

const lifecycleLabels = instanceStatusLabels;
const kindIcons = { concept: Database, relation: Link2, predicate: Braces, model: Calculator };
const sourceLabels = instanceSourceLabels;
const assertionLabels: Record<string, string> = { confirmed: "已确认", proposed: "待确认", rejected: "已驳回", superseded: "已替代", retracted: "已撤回", disputed: "有争议" };

const gapLabels: Record<string, string> = { no_current_assertions: "暂无当前有效的已确认事实", draft_node: "此实例仍为草稿" };

function ConnectionForm() {
  const { setSession } = useWorkspaceSession();
  const [token, setToken] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  async function login(event: FormEvent) {
    event.preventDefault(); setBusy(true); setError("");
    try { setSessionToken(token); const identity = await loadSession(); setSession(identity); setToken(""); }
    catch (cause) { setSessionToken(""); setError(cause instanceof Error ? cause.message : "无法登录知识库"); }
    finally { setBusy(false); }
  }
  return <div className="instance-connection"><form onSubmit={(event) => void login(event)} className="connection-card">
    <span className="connection-icon"><Database size={25} strokeWidth={1.4} /></span>
    <span className="workspace-eyebrow">实例工作区</span><h2>连接你的知识库</h2>
    <p>统一查看概念、关系、判断和模型运行，搜索名称与内容，追溯来源及关联。</p>
    <label htmlFor="instances-token">个人访问凭证</label>
    <Input id="instances-token" type="password" autoComplete="off" placeholder="输入个人访问凭证" value={token} onChange={(event) => setToken(event.target.value)} disabled={busy} />
    <Button className="w-full" type="submit" disabled={busy || !token.trim()}>{busy ? <Loader2 className="size-4 animate-spin" /> : <LockKeyhole className="size-4" />}连接知识库</Button>
    <span className="connection-note">所有工作区共享当前登录会话</span>
    {error && <p role="alert" className="connection-error">{error}</p>}
  </form></div>;
}

function InstanceInspector({ id, labels, onClose, onNavigate, revision }: { id: string; labels: Record<string, string>; onClose: () => void; onNavigate: (id: string) => void; revision: number }) {
  const [detail, setDetail] = useState<InstanceDetail | null>(null);
  const [tab, setTab] = useState("overview");
  const [busy, setBusy] = useState(true);
  const [error, setError] = useState("");
  const [retry, setRetry] = useState(0);
  const closeRef = useRef<HTMLButtonElement>(null);

  useEffect(() => { closeRef.current?.focus({ preventScroll: true }); }, [id]);
  useEffect(() => {
    const controller = new AbortController();
    // Reset the inspector before fetching a different external record snapshot.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setBusy(true); setError(""); setDetail(null); setTab("overview");
    void loadInstanceDetail(id, controller.signal).then((next) => { if (!controller.signal.aborted) setDetail(next); })
      .catch((cause) => { if (!controller.signal.aborted) setError(cause instanceof Error ? cause.message : "无法读取实例详情"); })
      .finally(() => { if (!controller.signal.aborted) setBusy(false); });
    return () => controller.abort();
  }, [id, retry, revision]);

  async function copyId() {
    try { await navigator.clipboard.writeText(id); toast.success("已复制实例标识"); }
    catch { toast.error("无法复制，请选中标识后手动复制"); }
  }
  const currentIds = new Set(detail?.card.current_assertions.map((item) => item.id));
  return <aside className="instance-inspector" aria-label="实例详情" onKeyDown={(event) => { if (event.key === "Escape") { event.stopPropagation(); onClose(); } }}>
    <div className="inspector-topline"><span className="detail-navigation-access"><NavigationToggle /></span><span>实例详情</span><div className="flex gap-1"><Button variant="ghost" size="icon-sm" aria-label="复制实例标识" title="复制标识" onClick={() => void copyId()}><Copy /></Button><Button ref={closeRef} variant="ghost" size="icon-sm" aria-label="关闭实例详情" onClick={onClose}><X /></Button></div></div>
    {busy ? <div role="status" className="instance-detail-empty"><Loader2 className="size-5 animate-spin" /><p>正在读取实例…</p></div> : error ? <div className="instance-detail-empty"><p role="alert">{error}</p><Button variant="outline" onClick={() => setRetry((value) => value + 1)}>重试</Button></div> : detail && <>
      <div className="instance-detail-heading"><span className="neutral-badge">{labels[detail.card.type] ?? detail.card.type}</span><h2>{detail.card.label}</h2><code>{id}</code></div>
      <div className="px-5 py-3"><Link className="logic-link" href={`/rules?${new URLSearchParams({ subject: id, concept: detail.card.type })}`}>以此实例验证业务规则与查看运行记录 →</Link></div>
      <div role="tablist" aria-label="实例详情视图" className="detail-tabs">
        {[['overview', '概览'], ['facts', `事实 ${detail.assertions.length}`], ['relations', `关系 ${detail.neighbors.edges.length}`]].map(([value, label], index, tabs) => <button key={value} role="tab" id={`instance-tab-${value}`} aria-controls={`instance-panel-${value}`} aria-selected={tab === value} tabIndex={tab === value ? 0 : -1} onClick={() => setTab(value)} onKeyDown={(event) => {
          const direction = event.key === "ArrowRight" ? 1 : event.key === "ArrowLeft" ? -1 : 0;
          if (direction) { event.preventDefault(); const next = tabs[(index + direction + tabs.length) % tabs.length][0]; setTab(next); document.getElementById(`instance-tab-${next}`)?.focus(); }
        }}>{label}</button>)}
      </div>
      <div className="instance-detail-body" role="tabpanel" id={`instance-panel-${tab}`} aria-labelledby={`instance-tab-${tab}`} tabIndex={0}>
        {tab === "overview" && <>
          <section className="detail-section"><h3>稳定属性<span>{Object.keys(detail.card.attrs).length}</span></h3>
            {Object.keys(detail.card.attrs).length ? <dl className="property-list">{Object.entries(detail.card.attrs).map(([key, value]) => <div key={key}><dt>{labels[key] ?? key}</dt><dd>{displayValue(value)}</dd></div>)}</dl> : <p className="detail-empty-copy">此实例暂无稳定属性。</p>}
          </section>
          <section className="detail-section"><h3>来源与索引</h3><dl className="property-list"><div><dt>来源类别</dt><dd>{sourceLabels[detail.card.source_class] ?? detail.card.source_class}</dd></div><div><dt>实体卡更新</dt><dd>{displayDate(detail.card.card_updated_at)}</dd></div><div><dt>当前有效事实</dt><dd>{currentIds.size} 条</dd></div></dl></section>
          {!!detail.card.knowledge_gaps.length && <section className="detail-section"><h3>数据提示</h3>{detail.card.knowledge_gaps.map((gap) => <p className="detail-empty-copy" key={gap}>{gapLabels[gap] ?? gap}</p>)}</section>}
        </>}
        {tab === "facts" && <><p className="detail-empty-copy mb-4">包含当前与历史事实。展开来源可查看原始引用。</p>
          {detail.assertions.map((assertion) => <article className="assertion-card" key={assertion.id}>
            <div className="flex flex-wrap items-center justify-between gap-2"><h3>{labels[assertion.predicate] ?? assertion.predicate}</h3><span className="neutral-badge">{currentIds.has(assertion.id) ? "当前有效" : assertionLabels[assertion.epistemic.status] ?? assertion.epistemic.status}</span></div>
            <p className="assertion-value">{displayValue(assertion.value)}</p>
            <p className="text-xs text-muted-foreground">{assertion.provenance.tier} 级溯源 · 观测于 {displayDate(assertion.temporal.observed_at)}</p>
            <details className="assertion-sources"><summary>来源与证据 · {assertion.provenance.source_refs.length + assertion.provenance.evidence_refs.length} 项</summary>
              <dl className="property-list"><div><dt>事实标识</dt><dd><code>{assertion.id}</code></dd></div><div><dt>有效期</dt><dd>{displayDate(assertion.temporal.valid_from)} → {displayDate(assertion.temporal.valid_to)}</dd></div></dl>
              {!assertion.provenance.source_refs.length && !assertion.provenance.evidence_refs.length && <p className="detail-empty-copy">此事实没有附带来源或证据引用。</p>}
              {assertion.provenance.source_refs.map((ref, index) => <pre key={`source-${index}`}>{typeof ref === "string" ? ref : JSON.stringify(ref, null, 2)}</pre>)}
              {assertion.provenance.evidence_refs.map((ref, index) => <pre key={`evidence-${index}`}>{typeof ref === "string" ? ref : JSON.stringify(ref, null, 2)}</pre>)}
            </details>
          </article>)}
          {!detail.assertions.length && <div className="detail-empty-copy"><FileText className="mb-3 size-6" />此实例暂无事实记录。</div>}
        </>}
        {tab === "relations" && <>
          <p className="detail-empty-copy mb-4">查看当前有效的直接关系，点击关联实例继续浏览。</p>
          {detail.neighbors.edges.map((edge, index) => {
            const outgoing = edge.src === id;
            const targetId = outgoing ? edge.dst : edge.src;
            const target = detail.neighbors.nodes.find((node) => node.id === targetId);
            return <button key={`${edge.src}-${edge.predicate}-${edge.dst}-${index}`} className="instance-relation" onClick={() => onNavigate(targetId)}>
              <span className="relation-direction">{outgoing ? <ArrowUpRight size={16} /> : <ArrowDownLeft size={16} />}{outgoing ? "出向" : "入向"} · {labels[edge.predicate] ?? edge.predicate}</span>
              <strong>{target?.label ?? targetId}</strong><code>{targetId}</code><ChevronRight className="relation-chevron" size={15} />
            </button>;
          })}
          {!detail.neighbors.edges.length && <div className="detail-empty-copy"><Link2 className="mb-3 size-6" />此实例暂无当前有效关系。</div>}
        </>}
      </div>
    </>}
  </aside>;
}

export default function InstancesWorkspace({ active }: { active: boolean }) {
  const params = useSearchParams();
  const { session } = useWorkspaceSession();
  const [filters, setFilters] = useState<InstanceFilters>(defaultInstanceFilters);
  const [input, setInput] = useState("");
  const [page, setPage] = useState<InstancePage | null>(null);
  const [labels, setLabels] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [selection, setSelection] = useState<InstanceSelection | null>(null);
  const [showFilters, setShowFilters] = useState(false);
  const [revision, setRevision] = useState(0);
  const searchRef = useRef<HTMLInputElement>(null);
  const lastSelected = useRef<HTMLButtonElement | null>(null);

  useEffect(() => {
    const timer = window.setTimeout(() => setFilters((previous) => previous.query === input.trim() ? previous : { ...previous, query: input.trim(), offset: 0 }), 250);
    return () => window.clearTimeout(timer);
  }, [input]);
  useEffect(() => {
    if (!active) return;
    function shortcut(event: KeyboardEvent) {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") { event.preventDefault(); searchRef.current?.focus(); }
    }
    window.addEventListener("keydown", shortcut);
    return () => window.removeEventListener("keydown", shortcut);
  }, [active]);
  useEffect(() => {
    // Discard the previous principal's records when the shared session changes.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    if (!session) { setPage(null); setSelection(null); setLabels({}); return; }
    setPage(null); setSelection(null); setError("");
    let cancelled = false;
    void loadStudioSnapshot().then((snapshot) => { if (!cancelled) setLabels(Object.fromEntries(snapshot.definitions.map((definition) => [definition.id.replace(/^(concept|relation|predicate|model):/, ""), definition.label]))); }).catch(() => { /* Registry labels are optional; identifiers remain visible. */ });
    return () => { cancelled = true; };
  }, [session?.principal]);
  useEffect(() => {
    if (!session || !active) return;
    const controller = new AbortController();
    // Reset feedback at the start of this abortable external request.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setBusy(true); setError("");
    void loadInstances(filters, controller.signal).then((next) => {
      if (controller.signal.aborted) return;
      // A concurrent deletion can invalidate the last page; return to the first.
      if (next.total > 0 && filters.offset >= next.total) { setFilters((previous) => ({ ...previous, offset: 0 })); return; }
      setPage(next);
    }).catch((cause) => { if (!controller.signal.aborted) setError(cause instanceof ApiError ? cause.message : "无法连接 KnowledgeOS，请检查连接后重试。"); })
      .finally(() => { if (!controller.signal.aborted) setBusy(false); });
    return () => controller.abort();
  }, [active, session?.principal, filters, revision]);

  const linkedId = params.get("id") ?? "", linkedType = params.get("type") ?? "";
  useEffect(() => {
    if (!active || !session) return;
    // Synchronize the filters and selection with external navigation.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    if (linkedType) setFilters(previous => ({ ...previous, kind: "concept", type: linkedType, offset: 0 }));
    if (linkedId) setSelection({ key: `concept:${linkedId}`, id: linkedId, kind: "concept" });
  }, [active, session?.principal, linkedId, linkedType]);

  function filter(patch: Partial<InstanceFilters>) { if ("domain" in patch || "kind" in patch || "type" in patch) setSelection(null); setFilters((previous) => ({ ...previous, ...patch, offset: 0 })); }
  function closeDetail() { setSelection(null); requestAnimationFrame(() => lastSelected.current?.focus({ preventScroll: true })); }
  const filtered = Boolean(filters.domain || filters.query || filters.kind || filters.type || filters.lifecycle || filters.source_class);
  const advancedFilterCount = [filters.type, filters.lifecycle, filters.source_class].filter(Boolean).length;
  const stale = busy || Boolean(error) || input.trim() !== filters.query;
  return <div className="instances-workspace">
    {!session && <><h1 className="sr-only">实例浏览</h1><div className="workspace-navigation-access"><NavigationToggle /></div></>}
    {!session ? <ConnectionForm /> : <>
      <div className="instance-browser" data-detail-open={Boolean(selection)}>
        <section className="instance-results" aria-label="实例搜索结果" aria-busy={busy}>
          <section className="instance-search-area" aria-label="实例搜索与筛选">
            <div className="directory-heading"><NavigationToggle /><h1>实例浏览</h1><Button variant="ghost" size="icon-sm" disabled={busy} onClick={() => setRevision(value => value + 1)} aria-label="刷新实例目录" title="刷新实例目录"><RefreshCw className={busy ? "animate-spin" : ""} /></Button></div>
            <label className="instance-domain-select"><span>工作领域</span><select aria-label="实例工作领域" value={filters.domain} onChange={event => filter({ domain: event.target.value, kind: "", type: "", lifecycle: "", source_class: "" })}><option value="">全部领域</option>{page?.domains?.map(domain => <option key={domain.id} value={domain.id}>{workDomainLabel(domain)}</option>)}</select></label>
            <div className="instance-catalog-heading">
              <label className="instance-kind-select"><span className="sr-only">实例类别</span><select aria-label="实例类别" value={filters.kind} onChange={(event) => filter({ kind: event.target.value as InstanceKind | "", type: "", lifecycle: "", source_class: "" })}><option value="">全部实例 · {page?.overall_total ?? "—"}</option>{(Object.keys(instanceKindLabels) as InstanceKind[]).map((kind) => <option key={kind} value={kind}>{instanceKindLabels[kind]} · {page?.facets.kind.find((facet) => facet.value === kind)?.count ?? 0}</option>)}</select></label>
              <button className="instance-filter-toggle" aria-label="筛选与排序" aria-expanded={showFilters} aria-controls="instance-advanced-filters" data-filtered={advancedFilterCount > 0} onClick={() => setShowFilters((value) => !value)}><SlidersHorizontal size={14} aria-hidden="true" /><span>筛选</span>{advancedFilterCount > 0 && <span className="instance-filter-count">{advancedFilterCount}</span>}</button>
            </div>
            <div className="instance-search-input"><Search size={16} aria-hidden="true" /><Input ref={searchRef} aria-label="搜索所有实例" placeholder="搜索名称、标识或内容…" value={input} onChange={(event) => setInput(event.target.value)} />{input ? <button aria-label="清空实例搜索" onClick={() => { setInput(""); filter({ query: "" }); searchRef.current?.focus(); }}><X size={16} /></button> : <kbd title="⌘ / Ctrl K">⌘ K</kbd>}</div>
            <div className="instance-filter-row" id="instance-advanced-filters" hidden={!showFilters}>
              <label><span>具体类型</span><select aria-label="实例类型" value={filters.type ? `${filters.kind}:${filters.type}` : ""} onChange={(event) => { const facet = page?.facets.type.find((item) => `${item.kind}:${item.value}` === event.target.value); filter(facet ? { kind: facet.kind, type: facet.value } : { type: "" }); }}><option value="">全部具体类型</option>{page?.facets.type.map((facet) => <option key={`${facet.kind}:${facet.value}`} value={`${facet.kind}:${facet.value}`}>{facet.label ?? labels[facet.value] ?? facet.value} · {facet.count}</option>)}</select></label>
              <label><span>记录状态</span><select aria-label="实例记录状态" value={filters.lifecycle} onChange={(event) => filter({ lifecycle: event.target.value })}><option value="">全部状态</option>{page?.facets.lifecycle.map((facet) => <option key={facet.value} value={facet.value}>{lifecycleLabels[facet.value] ?? facet.value}</option>)}</select></label>
              <label><span>来源类别</span><select aria-label="实例来源类别" value={filters.source_class} onChange={(event) => filter({ source_class: event.target.value })}><option value="">全部来源</option>{page?.facets.source_class.map((facet) => <option key={facet.value} value={facet.value}>{sourceLabels[facet.value] ?? facet.value}</option>)}</select></label>
              <label><span>排序</span><select aria-label="实例排序" value={filters.sort} onChange={(event) => filter({ sort: event.target.value })}><option value="label">名称 A–Z</option><option value="updated">最近更新</option></select></label>
            </div>
            <div className="instance-list-summary"><p className="instance-search-count" role="status" aria-live="polite">{busy ? "正在搜索…" : page ? <><strong>{page.total}</strong> 个{filtered ? "匹配实例" : "实例"}</> : "实例目录"}</p>{filtered && <button className="clear-filters" onClick={() => { setInput(""); setFilters(defaultInstanceFilters); }}>重置<X size={12} /></button>}</div>
          </section>
          {error && <div className="instance-error" role="alert"><p>{error}{page ? " 当前显示上次加载的目录。" : ""}</p><Button variant="outline" size="sm" onClick={() => setRevision((value) => value + 1)}>重试</Button></div>}
          <div className="instance-list-scroll">
            <ul className={`instance-list${stale ? " instance-rows-stale" : ""}`} aria-label="实例目录">{page?.items.map((item) => {
              const kind = item.kind ?? "concept";
              const key = item.key ?? `concept:${item.id}`;
              const Icon = kindIcons[kind];
              return <li key={key}><button disabled={stale} className="instance-list-item" aria-pressed={selection?.key === key} aria-label={`${item.label} ${item.id}`} title={`${item.label}\n${item.id}`} onClick={(event) => { lastSelected.current = event.currentTarget; setSelection({ key, id: item.id, kind }); }}><span className={`instance-object-icon instance-kind-${kind}`}><Icon size={17} strokeWidth={1.5} aria-hidden="true" /></span><span className="instance-row-copy"><strong>{item.label}</strong><span className="instance-row-meta"><span className={`instance-kind-label instance-kind-${kind}`}>{instanceKindLabels[kind]}</span><span>{item.kind === "predicate" ? displayValue(item.value) : item.type_label ?? labels[item.type] ?? item.type}</span></span></span><ChevronRight className="instance-row-chevron" size={14} aria-hidden="true" /></button></li>;
            })}</ul>
            {!page && busy && <div className="instance-result-empty" role="status"><Loader2 className="size-6 animate-spin" /><h3>正在加载实例目录</h3></div>}
            {!busy && !error && page?.items.length === 0 && <div className="instance-result-empty"><Search className="size-7" strokeWidth={1.3} /><h3>{filters.kind && !filters.query && !filters.type && !filters.lifecycle && !filters.source_class ? `暂无${instanceKindLabels[filters.kind]}` : filtered ? "没有找到匹配的实例" : "知识库还没有实例"}</h3><p>{filtered ? "试试其他关键词，或减少筛选条件。" : "通过已配置的连接器接入数据，或编译已有知识源后刷新目录。"}</p>{filtered && <Button variant="outline" onClick={() => { setInput(""); setFilters(defaultInstanceFilters); }}>清除筛选</Button>}</div>}
          </div>
          <footer className="instance-pagination"><span>{page?.total ? `${page.offset + 1}–${Math.min(page.offset + page.items.length, page.total)} / ${page.total}` : "0 个实例"}</span><label><span className="sr-only">每页实例数</span><select aria-label="每页实例数" value={filters.limit} onChange={(event) => filter({ limit: Number(event.target.value) })}><option value={25}>25 条 / 页</option><option value={50}>50 条 / 页</option><option value={100}>100 条 / 页</option></select></label><div><Button variant="ghost" size="icon-sm" aria-label="上一页实例" disabled={stale || filters.offset === 0} onClick={() => setFilters((previous) => ({ ...previous, offset: Math.max(0, previous.offset - previous.limit) }))}><ChevronLeft /></Button><Button variant="ghost" size="icon-sm" aria-label="下一页实例" disabled={stale || !page || filters.offset + filters.limit >= page.total} onClick={() => setFilters((previous) => ({ ...previous, offset: previous.offset + previous.limit }))}><ChevronRight /></Button></div></footer>
        </section>
        {selection ? selection.kind === "concept" ? <InstanceInspector id={selection.id} labels={labels} onClose={closeDetail} onNavigate={(id) => setSelection({ key: `concept:${id}`, id, kind: "concept" })} revision={revision} /> : <RecordInspector selection={selection} onClose={closeDetail} onNavigate={setSelection} revision={revision} /> : <aside className="instance-inspector-placeholder" aria-label="实例详情提示"><span><FileText size={24} strokeWidth={1.2} /></span><h3>选择一个实例</h3><p>在这里查看属性、关系、判断来源<br />及模型运行结果</p><small>目录始终保留在左侧</small></aside>}
      </div>
    </>}
  </div>;
}
