/** Presentation only: closing a panel never changes selection or draft data. */
export type WorkspaceLayout = {
  leftOpen: boolean;
  rightOpen: boolean;
  focused: boolean;
  autoHide: boolean;
};

export type WorkspaceLayoutAction =
  | { type: "toggle-focus" | "canvas-focus" | "select" | "toggle-auto-hide" }
  | { type: "open" | "close" | "toggle"; side: "left" | "right" };

export const initialWorkspaceLayout: WorkspaceLayout = {
  leftOpen: false, rightOpen: false, focused: false, autoHide: true,
};

export function workspaceLayoutReducer(state: WorkspaceLayout, action: WorkspaceLayoutAction): WorkspaceLayout {
  if (action.type === "toggle-focus") return { ...state, focused: !state.focused };
  if (action.type === "toggle-auto-hide") return { ...state, autoHide: !state.autoHide };
  if (action.type === "canvas-focus") return state.autoHide ? { ...state, leftOpen: false, rightOpen: false } : state;
  // Opening a selection must not interrupt an explicitly focused workspace.
  if (action.type === "select") return { ...state, leftOpen: state.autoHide ? false : state.leftOpen, rightOpen: true };
  if (!("side" in action)) return state;
  const key = action.side === "left" ? "leftOpen" : "rightOpen";
  const open = action.type === "open" || (action.type === "toggle" && (state.focused || !state[key]));
  return { ...state, [key]: open, focused: open ? false : state.focused };
}

export function visibleWorkspacePanels(state: WorkspaceLayout) {
  return { left: state.leftOpen && !state.focused, right: state.rightOpen && !state.focused };
}
