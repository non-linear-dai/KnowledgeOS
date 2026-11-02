"use client";

import { useEffect, useState, type FormEvent } from "react";
import Link from "next/link";
import { Bot, Check, Copy, KeyRound, Loader2, Plus, RefreshCw, ShieldCheck, UserRound } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { useWorkspaceSession } from "../workspace-session";
import { SignInPrompt } from "../sign-in-prompt";
import { NavigationToggle } from "../workspace-navigation-context";
import { displayDate } from "../instances/instances-api";
import { activeCredentials, createAgent, issueCredential, loadDirectory, loadMcpInfo, presetFor, proposalSummary, revokeCredential, rolePresets,
  slugFromLabel, updateParticipant, updatePolicy, type Directory, type IssuedCredential, type Participant } from "./agents-api";
import { hostConfig, hostLabels, hosts, type HostId, type McpInfo } from "./host-config";

async function copy(text: string, label: string) {
  try { await navigator.clipboard.writeText(text); toast.success(`已复制${label}`); }
  catch { toast.error("无法访问剪贴板，请手动选择复制"); }
}

function HostSetup({ agent, issued, info, infoError, initialHost }: { agent: Participant; issued: IssuedCredential; info: McpInfo | null; infoError: string; initialHost: HostId }) {
  const [host, setHost] = useState<HostId>(initialHost);
  const config = info ? hostConfig(host, agent.id, issued.token, info) : null;
  return <div className="agent-setup">
    <div className="agent-token" role="status"><KeyRound aria-hidden="true" /><div><strong>凭证已签发 · 只显示这一次</strong><code>{issued.token}</code><small>KnowledgeOS 只保存摘要。关闭后无法再次查看；遗失时吊销并重新签发。</small></div>
      <Button size="sm" variant="outline" onClick={() => void copy(issued.token, "凭证")}><Copy />复制</Button></div>
    <div className="agent-host-tabs" role="tablist" aria-label="选择接入宿主">{hosts.map(item => <button key={item.id} role="tab" aria-selected={host === item.id} onClick={() => setHost(item.id)}>{item.label}<small>{item.transport}</small></button>)}</div>
    {!info ? <p className="assistant-notice" data-tone="warning">{infoError || "正在读取 MCP 服务信息…"}</p> : config && <div className="agent-config">
      {config.file && <p className="agent-config-file">配置文件 <code>{config.file}</code></p>}
      <div className="agent-config-code"><pre aria-label={`${hostLabels[host]} 接入配置`}>{config.text}</pre><Button size="sm" variant="outline" onClick={() => void copy(config.text, `${hostLabels[host]}配置`)}><Copy />复制</Button></div>
      <ol className="agent-steps">{config.steps.map(step => <li key={step}>{step}</li>)}</ol>
      <p className="assistant-caption">接入后让 Agent 先调用 <code>whoami</code> 与 <code>ontology_catalog</code>；工作规范以提示词 <code>knowledgeos_guide</code> 下发。</p>
    </div>}
  </div>;
}

function CreateAgent({ open, onOpenChange, onCreated }: { open: boolean; onOpenChange: (open: boolean) => void; onCreated: (agent: Participant, host: HostId) => void }) {
  const [label, setLabel] = useState(""), [id, setId] = useState(""), [preset, setPreset] = useState<string>(rolePresets[0].id);
  const [host, setHost] = useState<HostId>("claude-code"), [model, setModel] = useState(""), [description, setDescription] = useState("");
  const [busy, setBusy] = useState(false), [error, setError] = useState("");
  const suggested = id || slugFromLabel(label);
  function submit(event: FormEvent) {
    event.preventDefault();
    setBusy(true); setError("");
    const roles = [...(rolePresets.find(item => item.id === preset)?.roles ?? ["agent"])];
    void createAgent({ id: suggested, label: label.trim(), roles, host, model: model.trim(), description: description.trim() })
      .then(agent => { onCreated(agent, host); setLabel(""); setId(""); setModel(""); setDescription(""); })
      .catch(cause => setError(cause instanceof Error ? cause.message : "登记失败"))
      .finally(() => setBusy(false));
  }
  return <Dialog open={open} onOpenChange={value => { if (!busy) onOpenChange(value); }}><DialogContent className="sm:max-w-lg">
    <DialogHeader><DialogTitle>登记 Agent</DialogTitle><DialogDescription>Agent 以自己的身份参与协作，负责人是你。权限只由角色决定；审核、批准与发布始终由人完成。</DialogDescription></DialogHeader>
    <form className="agent-form" onSubmit={submit}>
      <label>显示名称<Input value={label} maxLength={80} placeholder="例如：旋转执行器链研究员" onChange={event => setLabel(event.target.value)} /></label>
      <label>标识<Input value={id} maxLength={40} placeholder={slugFromLabel(label) || "小写字母开头，如 rotary-actuator"} onChange={event => setId(event.target.value.toLowerCase())} /><small>登记后不可修改；显示为 agent:{suggested || "…"}</small></label>
      <fieldset><legend>角色</legend>{rolePresets.map(item => <label key={item.id} className="agent-radio"><input type="radio" name="agent-role" checked={preset === item.id} onChange={() => setPreset(item.id)} /><span><strong>{item.label}</strong><small>{item.description}</small></span></label>)}</fieldset>
      <div className="agent-form-row"><label>宿主<select value={host} onChange={event => setHost(event.target.value as HostId)}>{hosts.map(item => <option key={item.id} value={item.id}>{item.label}</option>)}</select></label>
        <label>模型<Input value={model} maxLength={80} placeholder="例如：claude-sonnet、kimi-k2" onChange={event => setModel(event.target.value)} /></label></div>
      <label>职责说明<Textarea rows={2} value={description} maxLength={500} placeholder="例如：负责谐波减速器、无框力矩电机等部件与供应商研究" onChange={event => setDescription(event.target.value)} /></label>
      {error && <p role="alert" className="text-sm text-destructive">{error}</p>}
      <Button type="submit" disabled={busy || !label.trim() || !suggested}>{busy ? <Loader2 className="animate-spin" /> : <Plus />}登记并签发凭证</Button>
    </form>
  </DialogContent></Dialog>;
}

function AgentDetail({ agent, info, infoError, onChanged, pendingSetup, onSetupShown }: {
  agent: Participant; info: McpInfo | null; infoError: string; onChanged: () => void;
  pendingSetup: { issued: IssuedCredential; host: HostId } | null; onSetupShown: () => void;
}) {
  const [busy, setBusy] = useState(false), [issued, setIssued] = useState<{ issued: IssuedCredential; host: HostId } | null>(pendingSetup);
  const [host, setHost] = useState<HostId>((agent.host as HostId) in hostLabels ? agent.host as HostId : "claude-code");
  // A credential issued at registration is shown once, then forgotten.
  // eslint-disable-next-line react-hooks/set-state-in-effect
  useEffect(() => { if (pendingSetup) { setIssued(pendingSetup); onSetupShown(); } }, [pendingSetup, onSetupShown]);
  async function act(run: () => Promise<unknown>, message: string) {
    setBusy(true);
    try { await run(); toast.success(message); onChanged(); }
    catch (cause) { toast.error(cause instanceof Error ? cause.message : "操作未完成"); }
    finally { setBusy(false); }
  }
  const counts = proposalSummary(agent.changesets);
  const preset = presetFor(agent.roles);
  return <article className="agent-detail" aria-label={`${agent.label} 详情`}>
    <header><div><span className="neutral-badge">{agent.status === "active" ? "启用" : "已停用"}</span><h2>{agent.label}</h2><code>{agent.id}</code></div>
      <Button variant="outline" size="sm" disabled={busy} onClick={() => void act(() => updateParticipant(agent.id, { status: agent.status === "active" ? "disabled" : "active" }), agent.status === "active" ? "已停用，所有凭证立即失效" : "已启用")}>{agent.status === "active" ? "停用" : "启用"}</Button></header>
    {agent.description && <p className="agent-description">{agent.description}</p>}
    <dl className="property-list"><div><dt>负责人</dt><dd>{agent.owner}</dd></div><div><dt>角色</dt><dd>
      <select aria-label="Agent 角色" disabled={busy} value={preset?.id ?? ""} onChange={event => { const next = rolePresets.find(item => item.id === event.target.value); if (next) void act(() => updateParticipant(agent.id, { roles: [...next.roles] }), "已更新角色"); }}>
        {!preset && <option value="">{agent.roles.join("、")}</option>}{rolePresets.map(item => <option key={item.id} value={item.id}>{item.label}</option>)}</select>
      <small>{agent.permissions.join(" · ")}</small></dd></div>
      <div><dt>宿主 / 模型</dt><dd>{agent.host ? hostLabels[agent.host as HostId] ?? agent.host : "—"} · {agent.model || "未填写"}</dd></div>
      <div><dt>最近活动</dt><dd>{agent.last_used_at ? displayDate(agent.last_used_at) : "尚未连接"}</dd></div>
      <div><dt>提案</dt><dd>{counts.total ? `${counts.total} 项 · 待审 ${counts.pending} · 通过 ${counts.approved} · 驳回/需修改 ${counts.rejected}` : "暂无"}{counts.pending > 0 && <> · <Link href="/inbox">去审核</Link></>}</dd></div></dl>
    <section className="agent-section"><div className="agent-section-head"><h3>凭证</h3><div className="flex items-center gap-2">
      <select aria-label="新凭证用于" value={host} onChange={event => setHost(event.target.value as HostId)}>{hosts.map(item => <option key={item.id} value={item.id}>{item.label}</option>)}</select>
      <Button size="sm" disabled={busy || agent.status !== "active"} onClick={() => void act(async () => { setIssued({ issued: await issueCredential(agent.id, hostLabels[host]), host }); }, "已签发凭证")}><KeyRound />签发凭证</Button></div></div>
      {issued && <HostSetup key={issued.issued.id} agent={agent} issued={issued.issued} info={info} infoError={infoError} initialHost={issued.host} />}
      {activeCredentials(agent).length ? <ul className="agent-credentials">{activeCredentials(agent).map(item => <li key={item.id}><div><strong>{item.label}</strong><small>签发 {displayDate(item.created_at)} · {item.last_used_at ? `最近使用 ${displayDate(item.last_used_at)}` : "尚未使用"}{item.last_client ? ` · ${item.last_client}` : ""}</small></div>
        <Button size="sm" variant="ghost" disabled={busy} onClick={() => void act(() => revokeCredential(item.id), "凭证已吊销")}>吊销</Button></li>)}</ul> : <p className="assistant-caption">尚无有效凭证。签发后按宿主复制配置即可接入。</p>}
    </section>
  </article>;
}

export default function AgentsWorkspace({ active }: { active: boolean }) {
  const { session, setSession } = useWorkspaceSession();
  const [directory, setDirectory] = useState<Directory | null>(null);
  const [selectedId, setSelectedId] = useState("");
  const [info, setInfo] = useState<McpInfo | null>(null), [infoError, setInfoError] = useState("");
  const [loading, setLoading] = useState(false), [error, setError] = useState(""), [revision, setRevision] = useState(0);
  const [creating, setCreating] = useState(false), [pendingSetup, setPendingSetup] = useState<{ issued: IssuedCredential; host: HostId } | null>(null);
  const canManage = Boolean(session?.permissions.includes("manage"));
  useEffect(() => {
    if (!active || !session || !canManage) return;
    const controller = new AbortController();
    // Reset feedback for this abortable request.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setLoading(true); setError("");
    void loadDirectory(controller.signal).then(next => { if (!controller.signal.aborted) setDirectory(next); })
      .catch(cause => { if (!controller.signal.aborted) setError(cause instanceof Error ? cause.message : "无法加载 Agent 列表"); })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    void loadMcpInfo(controller.signal).then(next => { if (!controller.signal.aborted) { setInfo(next); setInfoError(""); } })
      .catch(cause => { if (!controller.signal.aborted) setInfoError(cause instanceof Error ? cause.message : "MCP 服务不可用"); });
    return () => controller.abort();
  }, [active, session, canManage, revision]);
  const agents = directory?.participants.filter(item => item.kind === "agent") ?? [];
  const people = directory?.participants.filter(item => item.kind === "human") ?? [];
  const selected = agents.find(item => item.id === selectedId) ?? agents[0];
  const policy = directory?.policy;
  async function setSelfReview(value: boolean) {
    try {
      const next = await updatePolicy({ self_review_high_risk: value });
      setDirectory(current => current && { ...current, policy: next });
      setSession(current => current && { ...current, policy: next });
      toast.success(value ? "已允许个人模式自审高风险变更" : "已关闭自审");
    } catch (cause) { toast.error(cause instanceof Error ? cause.message : "无法修改审批策略"); }
  }
  if (!session || !canManage) return <div className="agents-workspace"><h1 className="sr-only">Agent 管理</h1><div className="workspace-navigation-access"><NavigationToggle /></div>
    {session ? <div className="change-empty"><Bot size={28} strokeWidth={1.3} /><h2>Agent 管理</h2><p>当前账号没有管理权限。请使用本机所有者账号登录。</p></div> : <SignInPrompt icon={<Bot size={25} strokeWidth={1.4} />} title="Agent 管理">登录后登记 Agent、签发凭证并获取各宿主的接入配置。首次使用会创建本机所有者账号。</SignInPrompt>}</div>;
  return <div className="agents-workspace">
    <header className="agents-header"><div className="directory-heading"><NavigationToggle /><h1>Agent 管理</h1>
      <Button variant="ghost" size="icon-sm" aria-label="刷新" title="刷新" disabled={loading} onClick={() => setRevision(value => value + 1)}><RefreshCw className={loading ? "animate-spin" : ""} /></Button></div>
      <p>每个 Agent 以自己的身份通过 MCP 读取知识、提交提案；所有写入都是待你审核的 ChangeSet。</p>
      <Button onClick={() => setCreating(true)}><Plus />登记 Agent</Button></header>
    {error && <div role="alert" className="instance-error"><p>{error}</p></div>}
    <div className="agents-overview">
      <section className="agent-card" aria-label="MCP 服务"><h3><Bot aria-hidden="true" />MCP 服务</h3>{info ? <><p>本机 HTTP：<code>{info.http_url}</code></p><p>stdio：<code>{[info.stdio.command, ...info.stdio.args].join(" ")}</code></p></> : <p className="assistant-notice" data-tone="warning">{infoError || "正在读取…"}</p>}
        <small>只在本机可用（第一期）。ChatGPT 桌面版与 Grok Bot 需公网 HTTPS，计划第二期支持。</small></section>
      <section className="agent-card" aria-label="审批策略"><h3><ShieldCheck aria-hidden="true" />审批策略 · {policy?.mode === "organization" ? "组织模式" : "个人模式"}</h3>
        <p>{policy?.mode === "organization" ? "组织模式：Agent 与其负责人视为同一责任方，高风险变更须由另一位成员批准。" : "个人模式：你审核本人名下 Agent 的提案即为独立审核；Agent 之间不相互独立，也不能批准。"}</p>
        <label className="agent-toggle"><input type="checkbox" checked={policy?.human_final_approval ?? true} disabled readOnly /><span>最终批准与发布须由人完成</span></label>
        <label className="agent-toggle"><input type="checkbox" checked={policy?.self_review_high_risk ?? false} disabled={!policy || policy.mode !== "personal"} onChange={event => void setSelfReview(event.target.checked)} /><span>允许自审你本人提交的高风险变更（批准记录标注“个人模式自审”；批量审核不适用）</span></label></section>
      <section className="agent-card" aria-label="人员"><h3><UserRound aria-hidden="true" />人员</h3><ul>{people.map(item => <li key={item.id}>{item.label} <code>{item.id}</code>{item.id === session.principal && <span className="neutral-badge">当前</span>}</li>)}</ul></section>
    </div>
    <div className="agents-layout">
      <aside className="agents-list" aria-label="Agent 列表">{agents.length ? <ul>{agents.map(item => {
        const counts = proposalSummary(item.changesets);
        return <li key={item.id}><button aria-pressed={selected?.id === item.id} onClick={() => setSelectedId(item.id)}>
          <span className="agent-avatar" aria-hidden="true">{item.label.slice(0, 1)}</span>
          <span><strong>{item.label}</strong><small>{item.host ? hostLabels[item.host as HostId] ?? item.host : "未指定宿主"} · {item.status === "active" ? (item.last_used_at ? `活跃 ${displayDate(item.last_used_at)}` : "未连接") : "已停用"}</small></span>
          {counts.pending > 0 && <span className="agent-pending" aria-label={`${counts.pending} 项待审`}>{counts.pending}</span>}
          {activeCredentials(item).length > 0 && <Check className="agent-connected" aria-label="有有效凭证" />}
        </button></li>;
      })}</ul> : <div className="change-empty"><Bot size={24} strokeWidth={1.3} /><p>{loading ? "正在加载…" : "还没有 Agent。登记后签发凭证，即可在 Claude Code、Cursor 等宿主中接入。"}</p></div>}</aside>
      {selected && <AgentDetail key={selected.id} agent={selected} info={info} infoError={infoError} onChanged={() => setRevision(value => value + 1)}
        pendingSetup={pendingSetup?.issued.participant_id === selected.id ? pendingSetup : null} onSetupShown={() => setPendingSetup(null)} />}
    </div>
    <CreateAgent open={creating} onOpenChange={setCreating} onCreated={(agent, host) => {
      setCreating(false); setSelectedId(agent.id);
      void issueCredential(agent.id, hostLabels[host]).then(issued => { setPendingSetup({ issued, host }); setRevision(value => value + 1); toast.success(`已登记 ${agent.label} 并签发凭证`); })
        .catch(cause => toast.error(cause instanceof Error ? cause.message : "签发凭证失败"));
    }} />
  </div>;
}
