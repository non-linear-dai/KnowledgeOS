"use client";

import type { ReactNode } from "react";
import { LogIn } from "lucide-react";
import { Button } from "@/components/ui/button";
import { useWorkspaceSession } from "./workspace-session";

/** Signed-out state for a workspace: what it is for, and one button that opens the shared account dialog. */
export function SignInPrompt({ icon, title, children }: { icon: ReactNode; title: string; children: ReactNode }) {
  const { setAccountOpen } = useWorkspaceSession();
  return <div className="sign-in-prompt">
    <span className="sign-in-prompt-icon" aria-hidden="true">{icon}</span>
    <h2>{title}</h2>
    <p>{children}</p>
    <Button onClick={() => setAccountOpen(true)}><LogIn />登录</Button>
  </div>;
}

/** The same sign-in action for empty states that already have their own layout. */
export function SignInButton() {
  const { setAccountOpen } = useWorkspaceSession();
  return <Button onClick={() => setAccountOpen(true)}><LogIn />登录</Button>;
}
