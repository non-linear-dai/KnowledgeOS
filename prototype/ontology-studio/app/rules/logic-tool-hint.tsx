"use client";

import type { ReactNode } from "react";
import { ToolHint } from "../tool-hint";

export function LogicToolHint({ label, children, side = "right", disabled = false }: { label: string; children: ReactNode; side?: "right" | "top"; disabled?: boolean }) {
  return <ToolHint label={label} side={side} disabled={disabled}>{children}</ToolHint>;
}
