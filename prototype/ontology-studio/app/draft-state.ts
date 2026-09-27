import type { ChangeOperation, OntologyDefinition } from "./studio-data";

function canonical(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(canonical);
  if (value && typeof value === "object") return Object.fromEntries(Object.entries(value).sort(([a], [b]) => a.localeCompare(b)).map(([key, item]) => [key, canonical(item)]));
  return value;
}
const same = (a: unknown, b: unknown) => JSON.stringify(canonical(a)) === JSON.stringify(canonical(b));
function merge(base: unknown, local: unknown, remote: unknown): unknown {
  if (same(local, base)) return remote;
  if (same(remote, base) || same(local, remote)) return local;
  const object = (value: unknown): value is Record<string, unknown> => Boolean(value) && typeof value === "object" && !Array.isArray(value);
  if (object(base) && object(local) && object(remote)) {
    return Object.fromEntries([...new Set([...Object.keys(base), ...Object.keys(local), ...Object.keys(remote)])]
      .map((key) => [key, merge(base[key], local[key], remote[key])]));
  }
  throw new Error("同一字段已在服务器上修改");
}

export function rebaseDraft(remote: OntologyDefinition[], operations: ChangeOperation[]) {
  const definitions = [...remote];
  const rebased: ChangeOperation[] = [];
  for (const operation of operations) {
    const index = definitions.findIndex((item) => item.id === operation.targetId);
    const current = index < 0 ? null : definitions[index];
    let next: OntologyDefinition | null;
    try { next = merge(operation.before, operation.after, current) as OntologyDefinition | null; }
    catch { throw new Error(`${operation.targetId} 存在编辑冲突，草稿已保留；请检查服务器版本后重新编辑。`); }
    if (same(next, current)) continue;
    if (index >= 0) definitions.splice(index, 1);
    if (next) definitions.push(next);
    rebased.push({ ...operation, before: current, after: next });
  }
  return { definitions, operations: rebased };
}
