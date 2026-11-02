/** Navigation groups follow the work: author definitions, look things up, then govern changes and participants. */
export const navigationGroups = [
  { id: "model", label: "建模" },
  { id: "browse", label: "查阅" },
  { id: "govern", label: "治理" },
] as const;

export const workspaces = [
  { id: "ontology", group: "model", href: "/", label: "本体建模", description: "定义概念、关系与逻辑", title: "本体建模白板工作台" },
  { id: "rules", group: "model", href: "/rules", label: "规则与决策", description: "维护逻辑、验证与追溯", title: "业务规则与决策工作台" },
  { id: "templates", group: "model", href: "/templates", label: "专家模板", description: "编排流程与沉淀经验", title: "专家流程模板工作台" },
  { id: "extraction", group: "model", href: "/extraction", label: "抽取配置", description: "说明卡、术语与抽取方案", title: "抽取配置工作台" },
  { id: "instances", group: "browse", href: "/instances", label: "实例浏览", description: "搜索实例与追溯事实", title: "实例搜索查看平台" },
  { id: "overview", group: "browse", href: "/overview", label: "定义总览", description: "注册目录与使用说明", title: "定义总览" },
  { id: "changesets", group: "govern", href: "/changesets", label: "审核", description: "待我处理的提案、审批与发布", title: "审核工作区" },
  { id: "agents", group: "govern", href: "/agents", label: "Agent 管理", description: "登记 Agent、凭证与接入配置", title: "Agent 管理" },
] as const;

export type WorkspaceId = typeof workspaces[number]["id"];
/** Earlier addresses that now open another workspace: "我的待审" became the default view of the review workspace. */
const pathAliases: Record<string, WorkspaceId> = { "/inbox": "changesets" };

export function workspaceForPath(path: string): WorkspaceId | null {
  const normalized = path.replace(/\/$/, "") || "/";
  return workspaces.find((workspace) => workspace.href === normalized)?.id ?? pathAliases[normalized] ?? null;
}

/** Overview and the whiteboard share one mounted editor, preserving staged edits. */
export function workspaceSurface(id: WorkspaceId): Exclude<WorkspaceId, "overview"> {
  return id === "overview" ? "ontology" : id;
}

/** Number shown on the review entry: proposals this reviewer may decide now. Hidden when zero or unknown. */
export function reviewBadge(count: number | null | undefined) {
  return typeof count === "number" && count > 0 ? (count > 99 ? "99+" : String(count)) : "";
}

/** Fired after a review decision, apply or publish so navigation counts refresh without waiting for the next poll. */
export const reviewChangedEvent = "knowledgeos:review-changed";
export function announceReviewChange() {
  if (typeof window !== "undefined") window.dispatchEvent(new Event(reviewChangedEvent));
}
