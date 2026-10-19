"use client";

import type { ReactNode } from "react";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";

export function LogicToolHint({ label, children, side = "right", disabled = false }: { label: string; children: ReactNode; side?: "right" | "top"; disabled?: boolean }) {
  return <Tooltip><TooltipTrigger asChild>{disabled ? <span className="inline-flex" tabIndex={0}>{children}</span> : children}</TooltipTrigger><TooltipContent side={side} sideOffset={12} className="navigation-tooltip">{label}</TooltipContent></Tooltip>;
}
