"use client";

import { useEffect, useState, type FormEvent } from "react";
import { KeyRound, LogOut } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { changePassword, loadAuthStatus, loadSession, setSessionToken, setupOwner, signIn, signOut, type UserSession } from "./knowledgeos-api";

type Mode = "login" | "setup" | "token";
const kindLabels = { human: "人员账号", agent: "Agent" } as const;

export function AccountDialog({ open, onOpenChange, session, onSession }: {
  open: boolean; onOpenChange: (open: boolean) => void; session: UserSession | null; onSession: (session: UserSession | null) => void;
}) {
  const [mode, setMode] = useState<Mode>("login");
  const [setupRequired, setSetupRequired] = useState(false);
  const [username, setUsername] = useState(""), [label, setLabel] = useState("");
  const [password, setPassword] = useState(""), [confirm, setConfirm] = useState(""), [token, setToken] = useState("");
  const [current, setCurrent] = useState(""), [changing, setChanging] = useState(false);
  const [busy, setBusy] = useState(false), [error, setError] = useState(""), [notice, setNotice] = useState("");

  useEffect(() => {
    if (!open || session) return;
    const controller = new AbortController();
    void loadAuthStatus(controller.signal).then(status => { if (controller.signal.aborted) return; setSetupRequired(status.setup_required); setMode(previous => previous === "token" ? previous : status.setup_required ? "setup" : "login"); }).catch(() => undefined);
    return () => controller.abort();
  }, [open, session]);

  function reset() { setPassword(""); setConfirm(""); setToken(""); setCurrent(""); setError(""); setNotice(""); setChanging(false); }
  async function run(action: () => Promise<void>) {
    setBusy(true); setError(""); setNotice("");
    try { await action(); }
    catch (cause) { setError(cause instanceof Error ? cause.message : "操作未完成，请重试"); }
    finally { setBusy(false); }
  }
  async function connect() {
    const identity = await loadSession();
    onSession(identity); reset(); onOpenChange(false);
  }
  function submit(event: FormEvent) {
    event.preventDefault();
    if (mode === "setup") {
      if (password !== confirm) { setError("两次输入的密码不一致"); return; }
      void run(async () => { await setupOwner(username.trim(), label.trim() || username.trim(), password); await connect(); });
    } else if (mode === "login") {
      void run(async () => { await signIn(username.trim(), password); await connect(); });
    } else {
      void run(async () => { setSessionToken(token); try { await connect(); } catch (cause) { setSessionToken(""); throw cause; } });
    }
  }
  function submitPassword(event: FormEvent) {
    event.preventDefault();
    if (password !== confirm) { setError("两次输入的新密码不一致"); return; }
    void run(async () => { await changePassword(current, password); reset(); setNotice("密码已修改"); });
  }

  return <Dialog open={open} onOpenChange={value => { onOpenChange(value); if (!value) reset(); }}><DialogContent className="sm:max-w-md">
    <DialogHeader><DialogTitle>账户与连接</DialogTitle><DialogDescription>
      {session ? "所有工作区共享当前登录会话。" : mode === "setup" ? "首次使用：创建本机所有者账号。你将以此账号审核与审批，并为 Agent 签发凭证。" : "使用本地账号登录；会话凭证只保存在当前页面内存中，12 小时后过期。"}
    </DialogDescription></DialogHeader>
    {session ? <div className="account-panel">
      <div className="account-identity"><strong>{session.label ?? session.principal}</strong><span>{session.principal}{session.kind ? ` · ${kindLabels[session.kind]}` : " · 开发凭证"}</span>
        {session.policy && <span>审批策略：{session.policy.mode === "personal" ? "个人模式" : "组织模式"}{session.policy.self_review_high_risk ? " · 允许自审高风险变更" : ""}</span>}</div>
      {session.kind === "human" && (changing ? <form className="space-y-3" onSubmit={submitPassword}>
        <Input type="password" aria-label="当前密码" autoComplete="current-password" placeholder="当前密码" value={current} disabled={busy} onChange={event => setCurrent(event.target.value)} />
        <Input type="password" aria-label="新密码" autoComplete="new-password" placeholder="新密码（至少 8 位）" value={password} disabled={busy} onChange={event => setPassword(event.target.value)} />
        <Input type="password" aria-label="确认新密码" autoComplete="new-password" placeholder="再次输入新密码" value={confirm} disabled={busy} onChange={event => setConfirm(event.target.value)} />
        <div className="flex gap-2"><Button type="submit" disabled={busy || !current || password.length < 8}>保存新密码</Button><Button type="button" variant="ghost" onClick={() => { setChanging(false); reset(); }}>取消</Button></div>
      </form> : <Button variant="outline" onClick={() => { reset(); setChanging(true); }}><KeyRound />修改密码</Button>)}
      <Button variant="outline" disabled={busy} onClick={() => void run(async () => { await signOut(); onSession(null); onOpenChange(false); })}><LogOut />退出登录</Button>
    </div> : <form className="space-y-3" onSubmit={submit}>
      {mode !== "token" ? <>
        <Input aria-label="用户名" autoComplete="username" placeholder="用户名（小写字母开头，如 ricky）" value={username} disabled={busy} onChange={event => setUsername(event.target.value)} />
        {mode === "setup" && <Input aria-label="显示名称" placeholder="显示名称（可选）" value={label} disabled={busy} onChange={event => setLabel(event.target.value)} />}
        <Input type="password" aria-label="密码" autoComplete={mode === "setup" ? "new-password" : "current-password"} placeholder={mode === "setup" ? "密码（至少 8 位）" : "密码"} value={password} disabled={busy} onChange={event => setPassword(event.target.value)} />
        {mode === "setup" && <Input type="password" aria-label="确认密码" autoComplete="new-password" placeholder="再次输入密码" value={confirm} disabled={busy} onChange={event => setConfirm(event.target.value)} />}
        <Button type="submit" className="w-full" disabled={busy || !username.trim() || !password || (mode === "setup" && password.length < 8)}>{busy ? "正在连接…" : mode === "setup" ? "创建账号并登录" : "登录"}</Button>
      </> : <>
        <Input type="password" aria-label="个人访问凭证" autoComplete="off" placeholder="输入访问凭证" disabled={busy} value={token} onChange={event => setToken(event.target.value)} />
        <Button type="submit" className="w-full" disabled={busy || !token.trim()}>{busy ? "正在连接…" : "连接知识库"}</Button>
      </>}
      <div className="account-switch">
        {mode === "token" && <button type="button" onClick={() => { reset(); setMode(setupRequired ? "setup" : "login"); }}>{setupRequired ? "创建本机所有者账号" : "使用账号密码登录"}</button>}
        {mode !== "token" && <button type="button" onClick={() => { reset(); setMode("token"); }}>使用访问凭证</button>}
      </div>
    </form>}
    {notice && <p role="status" className="text-sm text-muted-foreground">{notice}</p>}
    {error && <p role="alert" className="text-sm text-destructive">{error}</p>}
  </DialogContent></Dialog>;
}
