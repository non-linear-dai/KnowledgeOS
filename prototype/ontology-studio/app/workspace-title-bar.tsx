"use client";

import { useEffect, type ReactNode } from "react";
import { Sparkles } from "lucide-react";
import { Button } from "@/components/ui/button";
import { ToolHint } from "./tool-hint";

/** ⌘J / Ctrl J toggles a workspace's assistant while that workspace is in front. */
export function isAssistantShortcut(event: Pick<KeyboardEvent, "key" | "metaKey" | "ctrlKey" | "altKey" | "shiftKey">) {
  return (event.metaKey || event.ctrlKey) && !event.altKey && !event.shiftKey && event.key.toLowerCase() === "j";
}

export function useAssistantShortcut(enabled: boolean, toggle: () => void) {
  useEffect(() => {
    if (!enabled) return;
    const onKeyDown = (event: KeyboardEvent) => { if (isAssistantShortcut(event)) { event.preventDefault(); toggle(); } };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [enabled, toggle]);
}

/**
 * The one header every authoring workspace shares: workspace name and the object in hand on the left,
 * the primary action and the single assistant entry on the right.
 */
export function WorkspaceTitleBar({ title, leading, context, children, assistant }: {
  title: string; leading?: ReactNode; context?: ReactNode; children?: ReactNode;
  assistant?: { open: boolean; onToggle: () => void; disabled?: boolean; label?: string };
}) {
  return <header className="workspace-titlebar">
    {leading}
    <div className="workspace-titlebar-title"><h1>{title}</h1>{context && <><span className="workspace-titlebar-divider" aria-hidden="true">/</span><div className="workspace-titlebar-context">{context}</div></>}</div>
    <div className="workspace-titlebar-actions">
      {children}
      {assistant && <ToolHint side="bottom" label={assistant.label ?? "助手"} shortcut="⌘J" disabled={assistant.disabled}><Button size="sm" variant={assistant.open ? "secondary" : "outline"} aria-pressed={assistant.open} disabled={assistant.disabled} onClick={assistant.onToggle}><Sparkles />助手</Button></ToolHint>}
    </div>
  </header>;
}
