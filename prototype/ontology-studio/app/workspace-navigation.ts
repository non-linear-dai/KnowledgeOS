export const workspaces = [
  { id: "ontology", href: "/", label: "本体建模", description: "定义概念、关系与逻辑", title: "本体建模白板工作台" },
  { id: "instances", href: "/instances", label: "实例浏览", description: "搜索实例与追溯事实", title: "实例搜索查看平台" },
  { id: "rules", href: "/rules", label: "规则与决策", description: "维护逻辑、验证与追溯", title: "业务规则与决策工作台" },
  { id: "templates", href: "/templates", label: "专家模板", description: "编排流程与沉淀经验", title: "专家流程模板工作台" },
  { id: "extraction", href: "/extraction", label: "抽取配置", description: "说明卡、术语与抽取方案", title: "抽取配置工作台" },
  { id: "changesets", href: "/changesets", label: "变更审核", description: "审批、应用与发布", title: "变更审核工作区" },
  { id: "overview", href: "/overview", label: "定义总览", description: "注册目录与使用说明", title: "定义总览" },
] as const;

export type WorkspaceId = typeof workspaces[number]["id"];
export function workspaceForPath(path: string): WorkspaceId | null {
  return workspaces.find((workspace) => workspace.href === (path.replace(/\/$/, "") || "/"))?.id ?? null;
}

/** Overview and the whiteboard share one mounted editor, preserving staged edits. */
export function workspaceSurface(id: WorkspaceId): Exclude<WorkspaceId, "overview"> {
  return id === "overview" ? "ontology" : id;
}
