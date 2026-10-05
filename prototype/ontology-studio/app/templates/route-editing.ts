import type { Condition, ExpertTemplate, OperationSummary, RouteStep } from "./template-api";

export function conditionUsesItem(condition?:Condition):boolean { return Boolean(condition && ('source' in condition ? condition.source==='item' : ('all' in condition?condition.all:condition.any).some(conditionUsesItem))); }

export function nextRouteKey(prefix: string, used: string[]) {
  let index = 1;
  while (used.includes(`${prefix}_${index}`)) index++;
  return `${prefix}_${index}`;
}

export function routeScope(template: ExpertTemplate, id: string) {
  if (template.groups.some((group) => group.id === id)) return null;
  const step = template.steps.find((item) => item.id === id);
  if (!step) throw new Error("工序不存在");
  return step.parent;
}

export function connectionError(template: ExpertTemplate, from: string, to: string): string | null {
  if (from === to) return "工序不能连接到自身";
  if (routeScope(template, from) !== routeScope(template, to)) return "请连接同一层级的工序；组内与组外通过工序组衔接";
  if (template.edges.some((edge) => edge.from === from && edge.to === to)) return "这条前置关系已经存在";
  const next = new Map<string, string[]>();
  for (const edge of template.edges) next.set(edge.from, [...(next.get(edge.from) ?? []), edge.to]);
  const pending = [to], visited = new Set<string>();
  while (pending.length) {
    const current = pending.pop()!;
    if (current === from) return "这条连接会形成循环，请调整工序先后关系";
    if (visited.has(current)) continue;
    visited.add(current); pending.push(...(next.get(current) ?? []));
  }
  return null;
}

export function connectRoute(template: ExpertTemplate, from: string, to: string): ExpertTemplate {
  const error = connectionError(template, from, to);
  if (error) throw new Error(error);
  return { ...template, edges: [...template.edges, { from, to }] };
}

/** Insert a sequence between a selected stage and all of its existing successors. */
export function insertRouteSequence(template: ExpertTemplate, labels: string[], options: {
  after?: string; parent?: string | null; operation?: OperationSummary; library?: OperationSummary[];
} = {}): { template: ExpertTemplate; ids: string[] } {
  const names = labels.map((label) => label.trim()).filter(Boolean);
  if (!names.length) throw new Error("请至少输入一个工序名称");
  if (names.length + template.steps.length > 200) throw new Error("一份模板最多包含 200 道工序");
  const copy = structuredClone(template);
  const parent = options.after ? routeScope(copy, options.after) : options.parent ?? null;
  if (parent && !copy.groups.some((group) => group.id === parent)) throw new Error("工序组不存在");
  const outgoing = options.after ? copy.edges.filter((edge) => edge.from === options.after) : [];
  copy.edges = copy.edges.filter((edge) => edge.from !== options.after);
  const ids: string[] = [];
  for (const name of names) {
    const id = nextRouteKey("step", [...copy.groups, ...copy.steps].map((item) => item.id));
    const existing = options.operation ?? options.library?.find((item) => item.label === name);
    let operationRef = existing?.id;
    if (!operationRef) {
      const local = copy.operations.find((item) => item.label === name);
      if (local) operationRef = `operation:${local.id}:${local.version}`;
      else {
        const operationId = nextRouteKey("operation", [...copy.operations.map((item) => item.id),
          ...(options.library ?? []).map((item) => item.id.split(":")[1])]);
        copy.operations.push({ id: operationId, version: "1.0.0", label: name, cost_basis: copy.output_basis });
        operationRef = `operation:${operationId}:1.0.0`;
      }
    }
    copy.steps.push({ id, label: name, parent, operation_ref: operationRef, cost_basis: existing?.cost_basis ?? copy.output_basis });
    if (ids.length) copy.edges.push({ from: ids[ids.length - 1], to: id });
    ids.push(id);
  }
  if (options.after) copy.edges.push({ from: options.after, to: ids[0] });
  for (const edge of outgoing) copy.edges.push({ from: ids[ids.length - 1], to: edge.to });
  return { template: copy, ids };
}

/** Copy selected subgraphs, including group children, without inheriting external dependencies. */
export function duplicateRouteItems(template: ExpertTemplate, selected: string[]) {
  const copy = structuredClone(template);
  const included = new Set(selected);
  for (const step of template.steps) if (step.parent && included.has(step.parent)) included.add(step.id);
  if (template.steps.length + template.steps.filter(step => included.has(step.id)).length > 200 || template.groups.length + template.groups.filter(group => included.has(group.id)).length > 200) throw new Error('复制后将超过 200 道工序或 200 个阶段');
  const mapping = new Map<string, string>();
  const used = [...copy.groups, ...copy.steps].map((item) => item.id);
  for (const id of included) {
    const key = nextRouteKey(template.groups.some((group) => group.id === id) ? "group" : "step", used);
    used.push(key); mapping.set(id, key);
  }
  for (const group of template.groups) if (included.has(group.id)) copy.groups.push({ ...group, id: mapping.get(group.id)!, label: `${group.label} 副本` });
  for (const step of template.steps) if (included.has(step.id)) copy.steps.push({ ...step, id: mapping.get(step.id)!,
    label: `${step.label} 副本`, parent: step.parent ? mapping.get(step.parent) ?? step.parent : null });
  for (const edge of template.edges) if (included.has(edge.from) && included.has(edge.to)) copy.edges.push({ from: mapping.get(edge.from)!, to: mapping.get(edge.to)! });
  return { template: copy, ids: selected.map((id) => mapping.get(id)!).filter(Boolean) };
}

export function duplicateRouteAfter(template: ExpertTemplate, id: string) {
  routeScope(template, id);
  const result = duplicateRouteItems(template, [id]), added = result.ids[0];
  if (!added) throw new Error('请先选择一道工序或一个阶段');
  const outgoing = result.template.edges.filter(edge => edge.from === id);
  result.template.edges = result.template.edges.filter(edge => edge.from !== id);
  result.template.edges.push({ from: id, to: added }, ...outgoing.map(edge => ({ from: added, to: edge.to })));
  return result;
}

export function removeRouteItems(template: ExpertTemplate, selected: string[]) {
  const removed = new Set(selected);
  for (const step of template.steps) if (step.parent && removed.has(step.parent)) removed.add(step.id);
  return { ...template, groups: template.groups.filter((item) => !removed.has(item.id)),
    steps: template.steps.filter((item) => !removed.has(item.id)),
    edges: template.edges.filter((edge) => !removed.has(edge.from) && !removed.has(edge.to)) };
}

export function moveRouteStep(template: ExpertTemplate, id: string, parent: string | null) {
  const copy = structuredClone(template), step = copy.steps.find((item) => item.id === id);
  if (!step || step.parent === parent) return copy;
  if (parent && !copy.groups.some((group) => group.id === parent)) throw new Error("工序组不存在");
  if ((!parent || copy.groups.find(g=>g.id===parent)?.group_mode==='conditional') && conditionUsesItem(step.when)) throw new Error("使用当前对象条件的工序必须保留在重复组内");
  step.parent = parent;
  copy.edges = copy.edges.filter((edge) => edge.from !== id && edge.to !== id);
  return copy;
}

export type RouteBox = { id: string; parent: string | null; x: number; y: number; width: number; height: number };

/** Use longest-path columns to keep forks and joins visible in each scope. */
function layoutScope(ids: string[], edges: ExpertTemplate["edges"], sizes: Map<string, { width: number; height: number }>) {
  const rank = new Map(ids.map((id) => [id, 0]));
  for (let pass = 0; pass < ids.length; pass++) {
    let changed = false;
    for (const edge of edges) if (rank.has(edge.from) && rank.has(edge.to)) {
      const value = rank.get(edge.from)! + 1;
      if (rank.get(edge.to)! < value) { rank.set(edge.to, value); changed = true; }
    }
    if (!changed) break;
  }
  const columns = [...new Set(rank.values())].sort((a, b) => a - b);
  const result = new Map<string, { x: number; y: number }>();
  let x = 0;
  for (const column of columns) {
    let y = 0, width = 0;
    for (const id of ids.filter((key) => rank.get(key) === column)) {
      const size = sizes.get(id)!; result.set(id, { x, y });
      y += size.height + 56; width = Math.max(width, size.width);
    }
    x += width + 90;
  }
  return result;
}

export function layoutRoute(template: ExpertTemplate, collapsedGroups: string[] = []): RouteBox[] {
  const sizes = new Map<string, { width: number; height: number }>(template.steps.map((step: RouteStep) => [step.id, { width: 224, height: 116 }]));
  const children = new Map<string, Map<string, { x: number; y: number }>>();
  for (const group of template.groups) {
    const ids = template.steps.filter((step) => step.parent === group.id).map((step) => step.id);
    const layout = layoutScope(ids, template.edges, sizes); children.set(group.id, layout);
    sizes.set(group.id, { width: Math.max(340, ...[...layout.values()].map((point) => point.x + 284)),
      height: Math.max(240, ...[...layout.values()].map((point) => point.y + 232)) });
    if (collapsedGroups.includes(group.id)) sizes.set(group.id, { width: 284, height: 144 });
  }
  const top = [...template.groups.map((group) => group.id), ...template.steps.filter((step) => !step.parent).map((step) => step.id)];
  const topLayout = layoutScope(top, template.edges, sizes);
  return [
    ...top.map((id) => ({ id, parent: null, ...topLayout.get(id)!, ...sizes.get(id)! })),
    ...template.steps.filter((step) => step.parent).map((step) => ({ id: step.id, parent: step.parent,
      x: children.get(step.parent!)!.get(step.id)!.x + 30, y: children.get(step.parent!)!.get(step.id)!.y + 82, ...sizes.get(step.id)! })),
  ];
}

export type RoutePlan = { blocks: { label?: string; mode?: 'repeat' | 'conditional'; iteration_set?: string; execution_mode?: 'parallel' | 'sequential';
  steps: { label: string; operation_ref?: string }[] }[]; step_count: number; group_count: number };

/** A reviewable text outline, using the existing governed route semantics. */
export function parseRoutePlan(text: string): RoutePlan {
  const blocks: RoutePlan['blocks'] = [];
  let current: RoutePlan['blocks'][number] | undefined;
  for (const raw of text.split(/\n|→|；|;/)) {
    const line = raw.trim(); if (!line) continue;
    const header = line.match(/^\[(.+)\]$/);
    if (header) {
      if (current && !current.steps.length) throw new Error(`阶段“${current.label}”没有工序`);
      const parts = header[1].split('|').map(p => p.trim());
      if (!parts[0] || parts.length > 4) throw new Error('阶段格式为 [名称] 或 [名称 | 重复 | 集合标识 | 顺序]');
      if (parts.length > 1 && parts[1] !== '重复') throw new Error('阶段第二项仅支持“重复”');
      if (parts[1] === '重复' && (!parts[2] || !/^[a-z][a-z0-9_-]{0,63}$/.test(parts[2]))) throw new Error('重复阶段需要小写英文的对象集合标识');
      if (parts[3] && !['顺序', '独立'].includes(parts[3])) throw new Error('对象执行方式为“独立”或“顺序”');
      current = parts[0] === '主路线' && parts.length === 1 ? { steps: [] } : {
        label: parts[0], mode: parts[1] === '重复' ? 'repeat' : 'conditional', iteration_set: parts[2],
        execution_mode: parts[3] === '顺序' ? 'sequential' : 'parallel', steps: [] };
      blocks.push(current); continue;
    }
    if (!current) { current = { steps: [] }; blocks.push(current); }
    const [label, ref, ...extra] = line.split('\t').map(value => value.trim());
    if (!label || extra.length || ref && !/^operation:[a-z][a-z0-9_-]{0,63}:[1-9]\d*\.\d+\.\d+$/.test(ref)) throw new Error('工序每行一个名称；第二列可填写固定版本的标准工序引用');
    const cleaned = label.replace(/^\d+\.(?!\d)\s*|^\d+[)）、]\s*|^[-*]\s+/, '').trim();
    if (!cleaned) throw new Error('工序名称不能为空');
    current.steps.push({ label: cleaned, ...(ref ? { operation_ref: ref } : {}) });
  }
  if (current && !current.steps.length) throw new Error(`阶段“${current.label ?? '主路线'}”没有工序`);
  return { blocks, step_count: blocks.reduce((total, block) => total + block.steps.length, 0), group_count: blocks.filter(b => b.label).length };
}

export function insertRoutePlan(template: ExpertTemplate, plan: RoutePlan, options: { after?: string; parent?: string | null; library: OperationSummary[] }) {
  if (!plan.step_count) throw new Error('请至少输入一个工序');
  const parent = options.after ? routeScope(template, options.after) : options.parent ?? null;
  if (parent && plan.group_count) throw new Error('阶段只能创建在主路线；组内可粘贴普通工序清单');
  if (plan.step_count + template.steps.length > 200 || plan.group_count + template.groups.length > 200) throw new Error('当前模板最多支持 200 道工序和 200 个阶段');
  let copy = structuredClone(template);
  const outgoing = options.after ? copy.edges.filter(edge => edge.from === options.after) : [];
  copy.edges = copy.edges.filter(edge => edge.from !== options.after);
  const ids: string[] = [], groupIds: string[] = [];
  let tail = options.after;
  for (const block of plan.blocks) {
    let scope = parent, previous = tail;
    if (block.label) {
      const id = nextRouteKey('group', [...copy.groups, ...copy.steps].map(item => item.id));
      scope = id; previous = undefined;
      copy.groups.push({ id, label: block.label, group_mode: block.mode, iteration_set: block.iteration_set ?? `items_${id}`, execution_mode: block.execution_mode, join_policy: 'all' });
      if (tail) copy.edges.push({ from: tail, to: id });
      if (block.mode === 'repeat') for (const sample of copy.cases) sample.sets[block.iteration_set!] ??= [{ id: 'item_1' }];
      groupIds.push(id); tail = id;
    }
    for (const row of block.steps) {
      const matches = options.library.filter(op => row.operation_ref ? op.id === row.operation_ref : op.label === row.label);
      if (row.operation_ref && !matches.length) throw new Error(`标准工序版本不可用：${row.operation_ref}`);
      if (matches.length > 1) throw new Error(`“${row.label}”有多个标准版本，请在第二列填写明确的工序引用`);
      const added = insertRouteSequence(copy, [row.label], { after: previous, parent: scope, operation: matches[0], library: options.library });
      copy = added.template; previous = added.ids[0]; ids.push(previous);
      if (!block.label) tail = previous;
    }
  }
  if (tail) copy.edges.push(...outgoing.map(edge => ({ from: tail!, to: edge.to })));
  return { template: copy, ids, groupIds, selected: tail! };
}

/** Stable process-order navigation, including every child in its own scope. */
export function routeOutline(template: ExpertTemplate): { id: string; label: string; parent: string | null; isGroup: boolean }[] {
  function ordered(ids: string[]) {
    const pending = [...ids], result: string[] = [];
    while (pending.length) {
      const index = pending.findIndex(id => !template.edges.some(edge => edge.to === id && pending.includes(edge.from)));
      if (index < 0) throw new Error('路线包含循环');
      result.push(...pending.splice(index, 1));
    }
    return result;
  }
  return ordered([...template.groups.map(g => g.id), ...template.steps.filter(s => s.parent === null).map(s => s.id)]).flatMap(id => {
    const group = template.groups.find(g => g.id === id), step = template.steps.find(s => s.id === id);
    const entry = { id, label: (group ?? step)!.label, parent: null, isGroup: Boolean(group) };
    return group ? [entry, ...ordered(template.steps.filter(s => s.parent === id).map(s => s.id)).map(key => ({ id: key, label: template.steps.find(s => s.id === key)!.label, parent: id, isGroup: false }))] : [entry];
  });
}

export function selectedRouteSteps(template: ExpertTemplate, selected: string[]) {
  return template.steps.filter(step => selected.includes(step.id) || step.parent !== null && selected.includes(step.parent));
}

export function patchRouteSteps(template: ExpertTemplate, selected: string[], patch: Partial<Pick<RouteStep, 'operation_ref' | 'cost_basis' | 'when' | 'decision_binding'>>) {
  const copy = structuredClone(template), ids = new Set(selectedRouteSteps(template, selected).map(step => step.id));
  for (const step of copy.steps) if (ids.has(step.id)) {
    if (conditionUsesItem(patch.when) && (!step.parent || copy.groups.find(g => g.id === step.parent)?.group_mode === 'conditional')) throw new Error('当前对象条件只能批量应用到重复组内的工序');
    Object.assign(step, structuredClone(patch));
  }
  return copy;
}
