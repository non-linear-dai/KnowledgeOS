"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { lazy, Suspense, useEffect, useReducer, useRef, useState, type ReactNode } from "react";
import { BookOpen, Bot, Database, FileText, GitBranch, GitPullRequestArrow, Inbox, Network, PanelLeftClose, Pin, PinOff, Workflow } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Toaster } from "@/components/ui/sonner";
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@/components/ui/tooltip";
import { WorkspaceSession, useWorkspaceSession } from "./workspace-session";
import { navigationGroups, reviewBadge, reviewChangedEvent, workspaces, workspaceForPath, workspaceSurface, type WorkspaceId } from "./workspace-navigation";
import { request } from "./knowledgeos-api";
import { NavigationContext } from "./workspace-navigation-context";
import { initialNavigationState, navigationPinPreferenceKey, navigationReducer } from "./navigation-state";
import { AccountDialog } from "./account-dialog";

const icons = { ontology: Network, instances: Database, rules: GitBranch, templates: Workflow, extraction: FileText, changesets: GitPullRequestArrow, inbox: Inbox, agents: Bot, overview: BookOpen };
const OntologyWorkspace = lazy(() => import("./ontology-workspace"));
const ExpertTemplateWorkspace = lazy(() => import("./templates/template-workspace"));
const InstancesWorkspace = lazy(() => import("./instances/instances-workspace"));
const ChangesetsWorkspace = lazy(() => import("./changesets/changesets-workspace"));
const RulesWorkspace = lazy(() => import("./rules/rules-workspace"));
const ExtractionWorkspace = lazy(() => import("./extraction/extraction-workspace"));
const InboxWorkspace = lazy(() => import("./inbox/inbox-workspace"));
const AgentsWorkspace = lazy(() => import("./agents/agents-workspace"));

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
  const { session, setSession, accountOpen, setAccountOpen } = useWorkspaceSession();
  const [pendingReviews, setPendingReviews] = useState<number | null>(null);
  const canReview = Boolean(session?.permissions.includes("review"));

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

  useEffect(() => {
    // Retain each workspace after its first visit through external route navigation.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    if (active) { const surface = workspaceSurface(active); setVisited((previous) => previous.includes(surface) ? previous : [...previous, surface]); }
  }, [active]);

  useEffect(() => {
    // Keep the review badge current: on sign-in, route changes, window focus, review actions and once a minute.
    if (!canReview) return;
    let controller = new AbortController();
    const load = () => {
      controller.abort(); controller = new AbortController();
      const signal = controller.signal;
      void request<{ data: { counts: { can_approve: number } } }>("/v1/inbox", { signal })
        .then(result => { if (!signal.aborted) setPendingReviews(result.data.counts.can_approve); })
        .catch(() => { if (!signal.aborted) setPendingReviews(null); });
    };
    load();
    const timer = window.setInterval(load, 60_000);
    window.addEventListener("focus", load); window.addEventListener(reviewChangedEvent, load);
    return () => { controller.abort(); window.clearInterval(timer); window.removeEventListener("focus", load); window.removeEventListener(reviewChangedEvent, load); };
  }, [canReview, session?.principal, active]);

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
        {navigationGroups.map(group => <div key={group.id} className="navigation-group" role="group" aria-label={group.label}>
          <div className="navigation-section-label">{group.label}</div>
          {workspaces.filter(workspace => workspace.group === group.id).map(workspace => {
            const Icon = icons[workspace.id];
            const badge = workspace.id === "inbox" && canReview ? reviewBadge(pendingReviews) : "";
            const label = badge ? `${workspace.label}（${badge} 项待我处理）` : workspace.label;
            return <div key={workspace.id} className="navigation-entry"><NavigationHint label={label} enabled={collapsed}><Link href={workspace.href} aria-label={badge ? `${workspace.title}，${badge} 项待我处理` : workspace.title} onClick={() => dispatchNavigation({ type: "close" })}
              aria-current={active === workspace.id ? "page" : undefined} className="workspace-link">
              <Icon aria-hidden="true" size={19} strokeWidth={1.65} />
              <span className="workspace-link-copy"><strong>{workspace.label}</strong><span>{workspace.description}</span></span>
              <span className="workspace-link-short">{workspace.label}</span>
              {badge && <span className="navigation-badge" aria-hidden="true">{badge}</span>}
            </Link></NavigationHint></div>;
          })}
        </div>)}
      </nav>
      <div className="navigation-footer">
        <NavigationHint label="账户与连接" enabled={collapsed}><button className="navigation-account" aria-label="账户与连接" onClick={() => setAccountOpen(true)}>
          <span className="account-avatar" aria-hidden="true">{session ? (session.label ?? session.principal.replace(/^.*:/, "")).slice(0, 1).toUpperCase() : "K"}</span>
          <span className="navigation-account-copy"><strong>{session ? session.label ?? session.principal : "尚未登录"}</strong><span>{session ? session.principal : "登录或创建本机账号"}</span></span>
        </button></NavigationHint>
        <div className="navigation-version"><span>KnowledgeOS · 4.5</span><span>{navigation.pinned ? "已固定展开" : "图标导航"}</span></div>
      </div>
    </aside>
    <button className="navigation-backdrop" hidden={collapsed} aria-label="关闭工作区导航" onClick={() => dispatchNavigation({ type: "close" })} />
    <div className="workspace-surfaces" onPointerEnter={() => dispatchNavigation({ type: "close" })} onPointerDownCapture={() => dispatchNavigation({ type: "close" })} onFocusCapture={() => dispatchNavigation({ type: "close" })}>
      {workspaces.filter(({ id }) => id !== "overview" && (id === activeSurface || visited.includes(id))).map(({ id, title }) =>
        <section key={id} id={`${id}-surface`} hidden={activeSurface !== id} aria-label={id === "ontology" && active === "overview" ? "定义总览" : title} tabIndex={-1} className="workspace-surface">
          <Suspense fallback={<div className="workspace-loading" role="status">正在打开工作区…</div>}>
            {id === "ontology" ? <OntologyWorkspace active={activeSurface === id} view={active === "overview" ? "control" : "studio"} /> : id === "instances" ? <InstancesWorkspace active={active === id} /> : id === "rules" ? <RulesWorkspace active={active === id} /> : id === "changesets" ? <ChangesetsWorkspace active={active === id} /> : id === "extraction" ? <ExtractionWorkspace active={active === id} /> : id === "inbox" ? <InboxWorkspace active={active === id} /> : id === "agents" ? <AgentsWorkspace active={active === id} /> : <ExpertTemplateWorkspace active={active === id} />}
          </Suspense>
        </section>)}
      {children}
      <Toaster />
    </div>
    <AccountDialog open={accountOpen} onOpenChange={setAccountOpen} session={session} onSession={setSession} />
  </div></NavigationContext.Provider>;
}

export function KnowledgeWorkspace({ children }: { children: ReactNode }) {
  return <WorkspaceSession><TooltipProvider delayDuration={180} skipDelayDuration={80}><Shell>{children}</Shell></TooltipProvider></WorkspaceSession>;
}
