/** Turns proposal payloads into rows a reviewer can read: which object, what kind of change, and field by field before → after. */

export type DiffRow = { field: string; before: string; after: string; changed: boolean };
export type PatchRow = { op: string; path: string; value: string };

const kindLabels: Record<string, string> = {
  schema: "核心契约", domain: "工作领域", concept: "概念类型", relation: "关系类型", predicate: "判断类型", model: "确定性模型",
  business_constraint: "业务约束", business_rule: "业务规则", unit: "物理单位", currency: "货币", policy: "治理策略", connector: "连接器映射",
  decision_table: "决策表", decision_model: "决策模型",
};
const typeLabels: Record<string, string> = { create: "新建", update: "修改", deprecate: "弃用", delete: "删除", replace: "替换", add: "添加", remove: "移除" };
const fieldLabels: Record<string, string> = {
  id: "标识", kind: "类型", label: "名称", description: "说明", lifecycle: "生命周期", source_path: "文件", sourcePath: "文件",
  bindings: "属性绑定", endpoints: "关系端点", aliases: "别名", version: "版本",
};

export function kindLabel(kind: unknown) { return kindLabels[String(kind ?? "")] ?? String(kind ?? "—"); }
export function changeTypeLabel(type: unknown) { return typeLabels[String(type ?? "")] ?? String(type ?? "修改"); }

function record(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : {};
}

/** A value as one readable line: empty values show as a dash, structures as compact JSON. */
export function displayValue(value: unknown): string {
  if (value === null || value === undefined || value === "") return "—";
  if (typeof value === "string") return value;
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  if (Array.isArray(value) && value.every(item => typeof item !== "object" || item === null)) return value.length ? value.map(item => displayValue(item)).join("、") : "—";
  return JSON.stringify(value);
}

/** Flattens an object one level into its nested records (e.g. config.value_type), keeping arrays and deeper structures whole. */
function flatten(value: unknown) {
  const rows = new Map<string, unknown>();
  for (const [key, item] of Object.entries(record(value))) {
    const nested = record(item);
    if (Object.keys(nested).length && !Array.isArray(item)) for (const [inner, leaf] of Object.entries(nested)) rows.set(`${key}.${inner}`, leaf);
    else rows.set(key, item);
  }
  return rows;
}

export function fieldLabel(path: string) {
  const [head, ...rest] = path.split(".");
  return rest.length ? `${fieldLabels[head] ?? head} · ${rest.join(".")}` : fieldLabels[head] ?? head;
}

/** Field rows for one definition operation. A creation lists every field; an update lists changed fields first. */
export function operationRows(operation: Record<string, unknown>): DiffRow[] {
  const before = flatten(operation.before), after = flatten(operation.after);
  const keys = [...new Set([...after.keys(), ...before.keys()])];
  const rows = keys.map(key => {
    const left = displayValue(before.get(key)), right = displayValue(after.get(key));
    return { field: fieldLabel(key), before: left, after: right, changed: left !== right };
  });
  return rows.filter(row => row.changed || operation.type !== "create").sort((a, b) => Number(b.changed) - Number(a.changed));
}

export function operationTarget(operation: Record<string, unknown>) {
  const after = record(operation.after), before = record(operation.before);
  return {
    id: String(operation.targetId ?? operation.target_id ?? operation.target ?? after.id ?? before.id ?? ""),
    label: String(after.label ?? before.label ?? ""),
    kind: kindLabel(operation.targetKind ?? operation.target_kind ?? after.kind ?? before.kind),
    type: changeTypeLabel(operation.type),
  };
}

/** JSON-patch style knowledge changes ([{op, path, value}] or {ops: [...]}) as rows. */
export function patchRows(patch: unknown): PatchRow[] {
  const ops = Array.isArray(patch) ? patch : Array.isArray(record(patch).ops) ? record(patch).ops as unknown[] : [];
  return ops.map(item => record(item)).filter(item => typeof item.path === "string")
    .map(item => ({ op: changeTypeLabel(item.op), path: String(item.path), value: displayValue(item.value) }));
}
