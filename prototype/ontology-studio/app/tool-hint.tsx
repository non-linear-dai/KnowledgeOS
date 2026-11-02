"use client";

import type { ReactNode } from "react";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";

/** Hover and focus hint for icon-only tool buttons: the action's name and, when it has one, its shortcut. */
export function ToolHint({ label, shortcut, children, side = "right", disabled = false }: { label: string; shortcut?: string; children: ReactNode; side?: "right" | "top" | "bottom" | "left"; disabled?: boolean }) {
  return <Tooltip><TooltipTrigger asChild>{disabled ? <span className="inline-flex" tabIndex={0}>{children}</span> : children}</TooltipTrigger>
    <TooltipContent side={side} sideOffset={12} className="navigation-tooltip">{label}{shortcut && <kbd className="tool-hint-shortcut">{shortcut}</kbd>}</TooltipContent></Tooltip>;
}
