export type NavigationState = { expanded: boolean; pinned: boolean };
export type NavigationAction = { type: "show" | "close" | "toggle" } | { type: "set-pinned"; pinned: boolean };

export const initialNavigationState: NavigationState = { expanded: false, pinned: false };
export const navigationPinPreferenceKey = "knowledgeos.navigation.pinned.v1";

export function navigationReducer(state: NavigationState, action: NavigationAction): NavigationState {
  if (action.type === "set-pinned") return { pinned: action.pinned, expanded: action.pinned };
  if (state.pinned) return state;
  if (action.type === "show") return state.expanded ? state : { ...state, expanded: true };
  if (action.type === "close") return state.expanded ? { ...state, expanded: false } : state;
  return { ...state, expanded: !state.expanded };
}
