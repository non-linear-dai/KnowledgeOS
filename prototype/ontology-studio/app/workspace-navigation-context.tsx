"use client";

import { createContext, useContext } from "react";
import { PanelLeftClose, PanelLeftOpen } from "lucide-react";
import { Button } from "@/components/ui/button";

export const NavigationContext = createContext({ collapsed: true, toggle: () => {} });

export function NavigationToggle() {
  const { collapsed, toggle } = useContext(NavigationContext);
  return <Button variant="ghost" size="icon-sm" className="workspace-navigation-toggle" aria-label={collapsed ? "展开工作区导航" : "收起工作区导航"} title={collapsed ? "展开导航" : "收起导航"} aria-expanded={!collapsed} aria-controls="workspace-navigation" onClick={toggle}>{collapsed ? <PanelLeftOpen /> : <PanelLeftClose />}</Button>;
}
