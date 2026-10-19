"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { lazy, Suspense, useEffect, useReducer, useRef, useState, type ReactNode } from "react";
import { BookOpen, Database, GitBranch, GitPullRequestArrow, Network, PanelLeftClose, Pin, PinOff, Workflow } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Toaster } from "@/components/ui/sonner";
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@/components/ui/tooltip";
import { WorkspaceSession, useWorkspaceSession } from "./workspace-session";
import { workspaces, workspaceForPath, workspaceSurface, type WorkspaceId } from "./workspace-navigation";
import { NavigationContext } from "./workspace-navigation-context";
import { initialNavigationState, navigationPinPreferenceKey, navigationReducer } from "./navigation-state";
import { loadSession, setSessionToken } from "./knowledgeos-api";

const icons = { ontology: Network, instances: Database, rules: GitBranch, templates: Workflow, changesets: GitPullRequestArrow, overview: BookOpen };
const OntologyWorkspace = lazy(() => import("./ontology-workspace"));
const ExpertTemplateWorkspace = lazy(() => import("./templates/template-workspace"));
const InstancesWorkspace = lazy(() => import("./instances/instances-workspace"));
const ChangesetsWorkspace = lazy(() => import("./changesets/changesets-workspace"));
const RulesWorkspace = lazy(() => import("./rules/rules-workspace"));

function NavigationHint({ children, label, enabled }: { children: ReactNode; label: string; enabled: boolean }) {
  return <Tooltip><TooltipTrigger asChild>{children}</TooltipTrigger>{enabled && <TooltipContent side="right" sideOffset={12} className="navigation-tooltip">{label}</TooltipContent>}</Tooltip>;
}

function Shell({ children }: { children: ReactNode }) {
  const pathname = usePathname();
  const active = workspaceForPath(pathname);
  const [visited, setVisited] = useState<WorkspaceId[]>(active ? [workspaceSurface(active)] : []);
  const [navigation, dispatchNavigation] = useReducer(navigationReducer, initialNavigationState);
  const [compactNavigation, setCompactNavigation] = useState(false);
  const pinPreference = useRef(false);
  const navigationRef = useRef<HTMLElement>(null);
  const collapsed = !navigation.expanded;
  const { session, setSession } = useWorkspaceSession();
  const [accountOpen, setAccountOpen] = useState(false);
  const [token, setToken] = useState("");
  const [accountBusy, setAccountBusy] = useState(false);
  const [accountError, setAccountError] = useState("");

  function focusNavigationControl() {
    const controls = document.querySelectorAll<HTMLButtonElement>(".workspace-surface:not([hidden]) .workspace-navigation-toggle");
    Array.from(controls).find(control => control.getClientRects().length && getComputedStyle(control).visibility !== "hidden")?.focus();
  }

  function toggleNavigation() {
    if (navigation.pinned) return;
    dispatchNavigation({ type: "toggle" });
    requestAnimationFrame(() => {
      if (navigation.expanded) focusNavigationControl();
      else navigationRef.current?.querySelector<HTMLAnchorElement>('.workspace-link[aria-current="page"]')?.focus();
    });
  }
  function togglePinned() {
    const pinned = !navigation.pinned;
    pinPreference.current = pinned;
    dispatchNavigation({ type: "set-pinned", pinned });
    try { window.localStorage.setItem(navigationPinPreferenceKey, String(pinned)); } catch { /* The preference still works in this session when storage is unavailable. */ }
  }
  useEffect(() => {
    const media = window.matchMedia("(max-width: 900px)");
    try { pinPreference.current = window.localStorage.getItem(navigationPinPreferenceKey) === "true"; } catch { /* Storage is optional. */ }
    function sync() {
      setCompactNavigation(media.matches);
      dispatchNavigation({ type: "set-pinned", pinned: !media.matches && pinPreference.current });
      if (media.matches) dispatchNavigation({ type: "close" });
    }
    function storage(event: StorageEvent) {
      if (event.key !== navigationPinPreferenceKey && event.key !== null) return;
      pinPreference.current = event.newValue === "true"; sync();
    }
    sync(); media.addEventListener("change", sync); window.addEventListener("storage", storage);
    return () => { media.removeEventListener("change", sync); window.removeEventListener("storage", storage); };
  }, []);

  async function connectAccount() {
    setAccountBusy(true); setAccountError("");
    try { setSessionToken(token); const identity = await loadSession(); setSession(identity); setToken(""); setAccountOpen(false); }
    catch (cause) { setSessionToken(""); setSession(null); setAccountError(cause instanceof Error ? cause.message : "无法连接账户"); }
    finally { setAccountBusy(false); }
  }

  useEffect(() => {
    // Retain each workspace after its first visit through external route navigation.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    if (active) { const surface = workspaceSurface(active); setVisited((previous) => previous.includes(surface) ? previous : [...previous, surface]); }
  }, [active]);

  if (!active) return children;
  const activeSurface = workspaceSurface(active);
  return <NavigationContext.Provider value={{ collapsed, toggle: toggleNavigation }}><div className="knowledge-shell" data-navigation-collapsed={collapsed} data-navigation-pinned={navigation.pinned}>
    <a className="skip-link" href={`#${activeSurface}-surface`}>跳到当前工作区</a>
    <aside ref={navigationRef} id="workspace-navigation" className="workspace-navigation" aria-label="KnowledgeOS 工作区导航"
      onPointerLeave={() => { if (!navigationRef.current?.contains(document.activeElement)) dispatchNavigation({ type: "close" }); }}
      onBlurCapture={event => { if (!event.currentTarget.contains(event.relatedTarget)) dispatchNavigation({ type: "close" }); }}
      onKeyDown={event => { if (event.key === "Escape" && !navigation.pinned) { event.preventDefault(); dispatchNavigation({ type: "close" }); requestAnimationFrame(() => focusNavigationControl()); } }}>
      <div className="navigation-brand">
        <NavigationHint label="展开工作区导航" enabled={collapsed}><button className="navigation-brand-trigger" aria-label="打开工作区导航菜单" aria-expanded={navigation.expanded} aria-controls="workspace-navigation" onClick={() => dispatchNavigation({ type: "show" })}><span className="knowledge-mark" aria-hidden="true"><span /><span /><span /><span /></span></button></NavigationHint>
        <div className="navigation-wordmark"><strong>KnowledgeOS</strong><span>知识工作空间</span></div>
        {compactNavigation ? <Button variant="ghost" size="icon-sm" className="navigation-pin" aria-label="收起工作区导航" onClick={toggleNavigation}><PanelLeftClose /></Button> : !collapsed && <NavigationHint label={navigation.pinned ? "取消固定" : "固定展开"} enabled><Button variant="ghost" size="icon-sm" className="navigation-pin" aria-label={navigation.pinned ? "取消固定工作区导航" : "固定展开工作区导航"} aria-pressed={navigation.pinned} onClick={togglePinned}>{navigation.pinned ? <PinOff /> : <Pin />}</Button></NavigationHint>}
      </div>
      <nav className="workspace-links" aria-label="切换工作区">
        {workspaces.map((workspace, index) => {
          const Icon = icons[workspace.id];
          return <div key={workspace.id} className="navigation-entry">{(index === 0 || workspace.id === "changesets") && <div className="navigation-section-label">{index === 0 ? "工作区" : "知识管理"}</div>}<NavigationHint label={workspace.label} enabled={collapsed}><Link href={workspace.href} aria-label={workspace.title} onClick={() => dispatchNavigation({ type: "close" })}
            aria-current={active === workspace.id ? "page" : undefined} className="workspace-link">
            <Icon aria-hidden="true" size={19} strokeWidth={1.65} />
            <span className="workspace-link-copy"><strong>{workspace.label}</strong><span>{workspace.description}</span></span>
            <span className="workspace-link-short">{workspace.label}</span>
          </Link></NavigationHint></div>;
        })}
      </nav>
      <div className="navigation-footer">
        <NavigationHint label="账户与连接" enabled={collapsed}><button className="navigation-account" aria-label="账户与连接" onClick={() => { setAccountError(""); setAccountOpen(true); }}>
          <span className="account-avatar" aria-hidden="true">{session ? session.principal.replace(/^.*:/, "").slice(0, 1).toUpperCase() : "K"}</span>
          <span className="navigation-account-copy"><strong>{session?.principal ?? "尚未登录"}</strong><span>{session ? "当前会话" : "在工作区中连接账户"}</span></span>
        </button></NavigationHint>
        <div className="navigation-version"><span>KnowledgeOS · 4.0</span><span>{navigation.pinned ? "已固定展开" : "图标导航"}</span></div>
      </div>
    </aside>
    <button className="navigation-backdrop" hidden={collapsed} aria-label="关闭工作区导航" onClick={() => dispatchNavigation({ type: "close" })} />
    <div className="workspace-surfaces" onPointerEnter={() => dispatchNavigation({ type: "close" })} onPointerDownCapture={() => dispatchNavigation({ type: "close" })} onFocusCapture={() => dispatchNavigation({ type: "close" })}>
      {workspaces.filter(({ id }) => id !== "overview" && (id === activeSurface || visited.includes(id))).map(({ id, title }) =>
        <section key={id} id={`${id}-surface`} hidden={activeSurface !== id} aria-label={id === "ontology" && active === "overview" ? "定义总览" : title} tabIndex={-1} className="workspace-surface">
          <Suspense fallback={<div className="workspace-loading" role="status">正在打开工作区…</div>}>
            {id === "ontology" ? <OntologyWorkspace active={activeSurface === id} view={active === "overview" ? "control" : "studio"} /> : id === "instances" ? <InstancesWorkspace active={active === id} /> : id === "rules" ? <RulesWorkspace active={active === id} /> : id === "changesets" ? <ChangesetsWorkspace active={active === id} /> : <ExpertTemplateWorkspace active={active === id} />}
          </Suspense>
        </section>)}
      {children}
      <Toaster />
    </div>
    <Dialog open={accountOpen} onOpenChange={(open) => { setAccountOpen(open); if (!open) setToken(""); }}><DialogContent className="sm:max-w-md"><DialogHeader><DialogTitle>账户与连接</DialogTitle><DialogDescription>所有工作区共享登录会话，访问凭证仅保留在当前浏览器页面的内存中。</DialogDescription></DialogHeader>
      {session ? <><p className="rounded-md border border-border bg-muted px-3 py-2 text-sm">{session.principal}</p><Button variant="outline" onClick={() => { setSessionToken(""); setSession(null); setAccountOpen(false); }}>退出登录</Button></> : <form className="space-y-3" onSubmit={(event) => { event.preventDefault(); void connectAccount(); }}><Input type="password" aria-label="个人访问凭证" autoComplete="off" placeholder="输入个人访问凭证" disabled={accountBusy} value={token} onChange={(event) => setToken(event.target.value)} /><Button type="submit" className="w-full" disabled={accountBusy || !token.trim()}>{accountBusy ? "正在连接…" : "连接知识库"}</Button></form>}
      {accountError && <p role="alert" className="text-sm text-destructive">{accountError}</p>}
    </DialogContent></Dialog>
  </div></NavigationContext.Provider>;
}

export function KnowledgeWorkspace({ children }: { children: ReactNode }) {
  return <WorkspaceSession><TooltipProvider delayDuration={180} skipDelayDuration={80}><Shell>{children}</Shell></TooltipProvider></WorkspaceSession>;
}
