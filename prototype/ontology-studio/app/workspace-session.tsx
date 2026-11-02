"use client";

import { createContext, useContext, useState, type Dispatch, type ReactNode, type SetStateAction } from "react";
import type { UserSession } from "./knowledgeos-api";

type SessionContext = {
  session: UserSession | null;
  setSession: Dispatch<SetStateAction<UserSession | null>>;
  /** The single sign-in surface: every workspace opens the shared account dialog instead of its own form. */
  accountOpen: boolean;
  setAccountOpen: Dispatch<SetStateAction<boolean>>;
};
const Context = createContext<SessionContext | null>(null);

/** One in-memory identity for all workspaces; credentials are never persisted. */
export function WorkspaceSession({ children }: { children: ReactNode }) {
  const [session, setSession] = useState<UserSession | null>(null);
  const [accountOpen, setAccountOpen] = useState(false);
  return <Context.Provider value={{ session, setSession, accountOpen, setAccountOpen }}>{children}</Context.Provider>;
}

export function useWorkspaceSession() {
  const context = useContext(Context);
  if (!context) throw new Error("WorkspaceSession is required");
  return context;
}
