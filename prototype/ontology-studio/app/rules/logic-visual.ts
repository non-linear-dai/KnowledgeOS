import { graphDependencies, logicKindLabels, object, records, sampleInput, type LogicAsset, type LogicDocument, type LogicKind } from "./logic-api";
import type { OntologyDefinition } from "../studio-data";

const names: Record<string, string> = {
  facts: "场景条件", item: "当前对象", inputs: "计算输入", subject: "主体", candidates: "候选对象", contexts: "业务上下文",
  result: "判断结果", output: "计算结果", results: "逐项判断", value: "数值", literal: "数值", unit: "单位", id: "对象名称或标识",
  status: "完成情况", complete: "已完成", incomplete: "信息不足", blocked: "未通过", eligible: "符合条件", invalid: "不合法", valid: "合法", unknown: "信息不足",
  eligible_candidate_ids: "合格候选", eligible_candidates: "合格候选对象", verdict: "判断结论", classification: "分级结果",
  size: "尺寸演示值", equipment_class: "设备等级", workload_factor: "工作量系数", baseline_time: "基准工时", elapsed_time: "加工时长", effective_power: "有效功率",
  duration: "工时", energy: "能耗", power: "功率", time_inputs: "工时参数", rated_power: "额定功率", required_power: "要求功率", available_power: "候选能力",
  equipment: "设备", product: "产品", operation: "工序", selection: "选型结果", operation_ref: "标准工序", matched_rules: "命中规则", used_default: "使用默认结果",
  process_id: "工艺", projected_area_z: "Z向投影面积", required_clamping_force_tf: "所需锁模力（吨力）", press_class: "注塑机等级",
  organization: "组织", market: "市场", project: "项目", task: "任务", material_cost: "材料成本", labor_hours: "人工工时", labor_rate: "人工费率", overhead: "制造费用", acquisition_cost: "购置成本", residual_value: "残值", useful_life_hours: "使用寿命工时", utilization_factor: "利用率", schedule_delay_days: "计划延误天数", blocked_dependencies: "阻塞依赖数", high_risk_items: "高风险项数", baseline_finish: "基准完工日期", forecast_finish: "预计完工日期", amount: "金额", rate: "汇率", risk_points_per_calendar_day: "风险分/天", risk_points_per_count: "风险分/项",
  cost: "成本", total: "合计", quantity: "数量", precision: "小数位数", rounding: "舍入方式",
};
const assetNames: Record<string, string> = { operation_cycle_time: "工时计算", operation_energy: "能耗计算", cost_rollup: "成本汇总", equipment_hourly_depreciation: "设备每小时折旧", fx_conversion: "币种换算", project_risk_score: "项目风险评估", schedule_variance: "进度偏差" };
export function fieldLabel(key: string, definitions: { id: string; label?: string }[] = [], own?: unknown): string {
  if (typeof own === "string" && own.trim()) return own;
  return names[key] ?? definitions.find(item => item.id === key)?.label ?? key;
}
export function assetLabel(asset: Pick<LogicAsset, "id" | "label">) { return assetNames[asset.id] ?? asset.label; }
export function pathLabel(path: string[], definitions: { id: string; label?: string }[] = []): string { return path.length ? path.map(key => /^\d+$/.test(key) ? `第 ${Number(key) + 1} 项` : fieldLabel(key, definitions)).join(" / ") : "全部场景输入"; }
export const safePath = (path: string[]) => path.length <= 8 && path.every(key => key && !["__proto__", "constructor", "prototype"].includes(key));
export function getPath(value: unknown, path: string[]): unknown { return path.reduce<unknown>((current, key) => current && typeof current === "object" ? (current as Record<string, unknown>)[key] : undefined, value); }
export function setPath(value: unknown, path: string[], next: unknown): unknown {
  if (!safePath(path)) throw new Error("字段路径无效或层级超过限制");
  if (!path.length) return structuredClone(next);
  const result = structuredClone(value && typeof value === "object" ? value : /^\d+$/.test(path[0]) ? [] : {}) as Record<string, unknown>;
  result[path[0]] = setPath(result[path[0]], path.slice(1), next); return result;
}
export function removePath(value: unknown, path: string[]): unknown {
  if (!safePath(path) || !path.length) throw new Error("不能移除整个场景");
  const result = structuredClone(value) as Record<string, unknown>;
  if (path.length === 1) { if (Array.isArray(result)) result.splice(Number(path[0]), 1); else delete result[path[0]]; }
  else result[path[0]] = removePath(result[path[0]], path.slice(1));
  return result;
}
export type Port = { key: string; label: string; path: string[]; type: string; unit?: string };
export function flattenValues(value: unknown, path: string[] = [], depth = 0): Port[] {
  const row: Port = { key: JSON.stringify(path), label: pathLabel(path), path, type: Array.isArray(value) ? "list" : value !== null && typeof value === "object" ? "object" : typeof value === "boolean" ? "boolean" : "scalar" };
  if (value && typeof value === "object" && "literal" in object(value) && "unit" in object(value)) return [{ ...row, type: "quantity", unit: String(object(value).unit) }];
  if (depth >= 7 || !value || typeof value !== "object") return [row];
  return [row, ...Object.entries(value).filter(([key]) => safePath([key])).flatMap(([key, child]) => flattenValues(child, [...path, key], depth + 1))];
}
export function definitionFor(node: Record<string, unknown>, assets: LogicAsset[]) { return assets.find(asset => asset.ref === node.ref)?.definition ?? object(node.snapshot) as LogicDocument; }
export function inputPorts(kind: LogicKind, definition: LogicDocument, bindings?: Record<string, unknown>, definitions: OntologyDefinition[] = []): Port[] {
  const standard: Port[] = kind === "model" ? records(definition.inputs).map(input => ({ key: String(input.id), path: [String(input.id)], label: fieldLabel(String(input.id), definitions, input.label), type: input.type === "date" ? "scalar" : "number", unit: String(input.unit) }))
    : kind === "decision_table" ? records(definition.inputs).map(input => ({ key: String(input.field), path: [String(input.field)], label: fieldLabel(String(input.field), definitions, input.label), type: String(input.type), unit: input.unit ? String(input.unit) : undefined }))
    : [{ key: "subject", path: ["subject"], label: "主体属性", type: "object" }, ...(kind === "business_rule" ? [{ key: "candidates", path: ["candidates"], label: "候选对象", type: "list" }, ...(Object.keys(object(object(definition.scope).context_concepts)).length ? [{ key: "contexts", path: ["contexts"], label: "业务上下文", type: "object" }] : [])] : [])];
  if (!bindings) return standard;
  return [...new Set([...Object.keys(bindings), ...standard.filter(port => !((kind === "model" && "inputs" in bindings) || (kind === "decision_table" && ("facts" in bindings || "item" in bindings)))).map(port => port.key)])].map(key => standard.find(port => port.key === key) ?? { key, path: [key], label: fieldLabel(key, definitions), type: ["inputs", "facts", "item", "subject", "contexts"].includes(key) ? "object" : key === "candidates" ? "list" : "scalar" });
}
export function outputPorts(kind: LogicKind, definition: LogicDocument, definitions: OntologyDefinition[] = []): Port[] {
  if (kind === "decision_table") {
    if (definition.hit_policy === "COLLECT") return [{ key: "result", path: ["result"], label: "全部命中结果", type: "list" }];
    return [{ key: "result", path: ["result"], label: "完整判断结果", type: "object" }, ...records(definition.outputs).map(column => ({ key: `result:${column.id}`, path: ["result", String(column.id)], label: fieldLabel(String(column.id), definitions, column.label), type: String(column.type) }))];
  }
  if (kind === "model") return [{ key: "output", path: ["output"], label: "完整计算结果（含单位）", type: "object" }, { key: "value", path: ["output", "value"], label: "计算数值", type: "number", unit: String(definition.output_unit) }];
  if (kind === "business_rule") return [{ key: "eligible", path: ["eligible_candidates"], label: "合格候选对象", type: "list" }, { key: "ids", path: ["eligible_candidate_ids"], label: "合格候选名单", type: "list" }, { key: "status", path: ["status"], label: "完成情况", type: "string" }];
  return [{ key: "results", path: ["results"], label: "合法性判断", type: "list" }, { key: "status", path: ["status"], label: "完成情况", type: "string" }];
}
export function graphOrder(definition: LogicDocument): string[] {
  const ids = records(definition.nodes).map(node => String(node.id)), edges = graphDependencies(definition), result: string[] = [];
  while (result.length < ids.length) {
    const ready = ids.filter(id => !result.includes(id) && edges.filter(edge => edge.target === id).every(edge => result.includes(edge.source)));
    if (!ready.length) throw new Error("连接形成循环，或引用了不存在的节点。请调整数据流向。"); result.push(...ready);
  }
  return result;
}
export function bindGraph(definition: LogicDocument, target: string, key: string, binding?: Record<string, unknown>): LogicDocument {
  const next = structuredClone(definition);
  if (binding?.source === "node" && !records(next.nodes).some(node => node.id === binding.node)) throw new Error("来源节点不存在");
  if (binding?.source !== "literal" && binding?.path && !safePath(binding.path as string[])) throw new Error("输入路径无效");
  if (target === "$output") { const outputs = object(next.outputs); if (binding) outputs[key] = binding; else delete outputs[key]; next.outputs = outputs; }
  else { const nodes = records(next.nodes), node = nodes.find(item => item.id === target); if (!node) throw new Error("目标节点不存在"); const bindings = object(node.bindings); if (binding) bindings[key] = binding; else delete bindings[key]; node.bindings = bindings; next.nodes = nodes; }
  graphOrder(next); return next;
}
export function deleteGraphNode(definition: LogicDocument, id: string): LogicDocument {
  if (records(definition.nodes).some(node => Object.values(object(node.bindings)).some(binding => object(binding).source === "node" && object(binding).node === id)) || Object.values(object(definition.outputs)).some(binding => object(binding).source === "node" && object(binding).node === id)) throw new Error("此节点仍被使用。请先移除相关连线或调整结果来源，再删除节点。");
  return { ...definition, nodes: records(definition.nodes).filter(node => node.id !== id) };
}
export function assertPortCompatibility(source: Port, target: Port, definitions: { id: string; config?: Record<string, unknown> }[] = []) {
  if (target.type === "any") return;
  if (source.type === "quantity" && target.type === "number") { if (source.unit && target.unit && source.unit !== target.unit) { const left = definitions.find(item => item.id === `unit:${source.unit}`)?.config?.dimension, right = definitions.find(item => item.id === `unit:${target.unit}`)?.config?.dimension; if (!left || left !== right) throw new Error("量纲或单位不一致，请先选择兼容的单位再连接。"); } return; }
  if (source.type === "object" || source.type === "list" || target.type === "object" || target.type === "list") { if (source.type !== target.type) throw new Error("两端的数据类型不同，请连接相同类型的输入和输出。"); }
  else if (source.type !== "scalar" && target.type !== "scalar" && source.type !== target.type) throw new Error("数值、文本和是否值不能直接混接。");
  if (source.unit && target.unit && source.unit !== target.unit) throw new Error(`单位不一致（${source.unit} 与 ${target.unit}）。请使用相同单位的计算数值，或先增加明确的换算步骤。`);
}
export function addGraphAsset(definition: LogicDocument, asset: LogicAsset): LogicDocument {
  if (asset.lifecycle !== "active" || asset.kind === "decision_model") throw new Error("只能添加已启用的基础逻辑");
  if (asset.definition.governed_only) throw new Error("此计算需要权威来源调用，不能作为普通场景节点添加。");
  if (records(definition.nodes).length >= 30) throw new Error("一条决策链最多包含30个逻辑步骤。");
  const nodes = records(definition.nodes); let id = `step_${nodes.length + 1}`; while (nodes.some(node => node.id === id)) id += "_1";
  const ports = inputPorts(asset.kind, asset.definition);
  const bindings = asset.kind === "decision_table" && records(asset.definition.inputs).some(input => input.source === "item")
    ? { facts: { source: "facts", path: ["facts"] }, item: { source: "facts", path: ["item"] } }
    : Object.fromEntries(ports.map(port => [port.key, { source: "facts", path: asset.kind === "decision_table" ? ["facts", port.key] : [port.key] }]));
  const count = nodes.filter(node => node.ref === asset.ref).length;
  return { ...definition, nodes: [...nodes, { id, label: `${assetLabel(asset)}${count ? `（${count + 1}）` : ""}`, kind: asset.kind, ref: asset.ref, bindings }] };
}
export function addScenarioField(definition: LogicDocument, label: string, type: string, unit?: string): LogicDocument {
  if (!label.trim() || !["number", "string", "boolean", "date"].includes(type)) throw new Error("请填写输入名称并选择值类型。");
  const fields = records(definition.input_fields); if (fields.length >= 20) throw new Error("最多维护20个自定义场景字段。");
  const used = new Set([...fields.map(field => String(field.id)), ...records(definition.nodes).flatMap(node => Object.values(object(node.bindings)).map(object).filter(binding => binding.source === "facts").map(binding => String((binding.path as string[])?.[0]))) ]);
  let index = 1; while (used.has(`scenario_${index}`)) index++;
  return { ...definition, input_fields: [...fields, { id: `scenario_${index}`, label: label.trim(), type, ...(type === "number" && unit ? { unit } : {}) }] };
}
export function deleteScenarioField(definition: LogicDocument, id: string): LogicDocument {
  const used = [...records(definition.nodes).flatMap(node => Object.values(object(node.bindings))), ...Object.values(object(definition.outputs))].some(raw => { const binding = object(raw); return binding.source === "facts" && (binding.path as string[])?.[0] === id; });
  if (used) throw new Error("此输入仍被使用，请先调整相关连接再删除。");
  return { ...definition, input_fields: records(definition.input_fields).filter(field => field.id !== id), test_cases: definition.test_cases?.map(test => { const input = { ...test.input }; delete input[id]; return { ...test, input }; }) };
}
function mergeMissing(base: unknown, shape: unknown): unknown {
  if (base === undefined) return structuredClone(shape);
  if (Array.isArray(base) || Array.isArray(shape)) return base;
  if (base && shape && typeof base === "object" && typeof shape === "object") { const result = { ...object(base) }; for (const [key, child] of Object.entries(object(shape))) result[key] = mergeMissing(result[key], child); return result; }
  return base;
}
export function scenarioShape(kind: LogicKind, definition: LogicDocument, assets: LogicAsset[], definitions: OntologyDefinition[]): Record<string, unknown> {
  if (kind !== "decision_model") return sampleInput(kind, { ...definition, test_cases: [] }, definitions);
  let result: unknown = Object.fromEntries(records(definition.input_fields).map(field => [String(field.id), field.type === "boolean" ? false : field.unit ? { literal: "", unit: field.unit } : ""]));
  for (const node of records(definition.nodes)) {
    const source = definitionFor(node, assets); if (!source.id) continue;
    const input = scenarioShape(node.kind as LogicKind, source, [], definitions);
    for (const [key, raw] of Object.entries(object(node.bindings))) {
      const binding = object(raw); if (binding.source !== "facts") continue;
      const path = (binding.path ?? []) as string[]; if (!safePath(path)) continue;
      let value = object(input)[key];
      if (value === undefined && node.kind === "model") value = object(input.inputs)[key];
      if (value === undefined && node.kind === "decision_table") value = object(input.facts)[key];
      if (value === undefined) value = "";
      const existing = getPath(result, path); result = setPath(result, path, mergeMissing(existing, value));
    }
  }
  return object(result);
}
export function completeScenario(kind: LogicKind, definition: LogicDocument, assets: LogicAsset[], definitions: OntologyDefinition[], current: Record<string, unknown>) { return object(mergeMissing(current, scenarioShape(kind, definition, assets, definitions))); }
export function expectedShape(kind: LogicKind, definition: LogicDocument, assets: LogicAsset[] = []): Record<string, unknown> {
  if (kind === "decision_table") { const result = Object.fromEntries(records(definition.outputs).map(output => [String(output.id), output.type === "boolean" ? false : ""])); return { result: definition.hit_policy === "COLLECT" ? [result] : result }; }
  if (kind === "model") return { output: { value: "", unit: definition.output_unit } };
  if (kind === "business_constraint") return { results: [{ verdict: "valid" }] };
  if (kind === "business_rule") return { eligible_candidate_ids: [""] };
  return { output: Object.fromEntries(Object.entries(object(definition.outputs)).map(([key, raw]) => { const binding = object(raw), node = records(definition.nodes).find(node => node.id === binding.node); if (node) { const source = definitionFor(node, assets), expected = expectedShape(node.kind as LogicKind, source, []); return [key, getPath(expected, (binding.path ?? []) as string[]) ?? ""]; } return [key, ""]; })) };
}
export function canonicalDecimalString(value: unknown): unknown {
  if (typeof value !== "string" || !/^[+-]?\d+(?:\.\d*)?$/.test(value.trim())) return value;
  const raw = value.trim(), sign = raw.startsWith("-") ? "-" : "", unsigned = raw.replace(/^[+-]/, ""), [integer, fraction = ""] = unsigned.split(".");
  return `${sign}${integer.replace(/^0+(?=\d)/, "")}.${fraction.replace(/0+$/, "") || "0"}`;
}
export function normalizeCaseNumbers(kind: LogicKind, definition: LogicDocument, assets: LogicAsset[]): LogicDocument {
  function normalize(currentKind: LogicKind, current: LogicDocument, expected: Record<string, unknown>): Record<string, unknown> {
    const next = structuredClone(expected);
    if (currentKind === "model" && object(next.output).value !== undefined) object(next.output).value = canonicalDecimalString(object(next.output).value);
    if (currentKind === "decision_table") for (const result of Array.isArray(next.result) ? next.result : [next.result]) for (const column of records(current.outputs)) if (column.type === "number" && object(result)[String(column.id)] !== undefined) object(result)[String(column.id)] = canonicalDecimalString(object(result)[String(column.id)]);
    if (currentKind === "decision_model") for (const [key, raw] of Object.entries(object(current.outputs))) {
      const binding = object(raw), node = records(current.nodes).find(node => node.id === binding.node), actual = object(next.output);
      if (!node || !(key in actual)) continue;
      const path = (binding.path ?? []) as string[], source = definitionFor(node, assets), wrapper = object(setPath({}, path, actual[key])), normalized = normalize(node.kind as LogicKind, source, wrapper);
      actual[key] = getPath(normalized, path);
    }
    return next;
  }
  return { ...definition, test_cases: definition.test_cases?.map(test => ({ ...test, expected: normalize(kind, definition, test.expected) })) };
}
export function caseLabel(id: string, index: number, own?: unknown) { return typeof own === "string" && own ? own : ({ small_part: "小件选型", boundary_part: "分界值检查", large_part: "大件选型", large_half_hour: "工时单位换算", select_capable: "筛选符合能力的设备", lower_requirement: "较低能力要求", missing_capability: "候选能力缺失", valid_power: "合法功率", zero_power: "零功率", missing_power: "功率缺失", double_workload: "双倍工作量", minute_conversion: "分钟换算", duration_then_energy: "先算工时再算能耗", shorter_operation: "较短工序" } as Record<string, string>)[id] ?? `案例 ${index + 1}`; }
export function formulaText(value: unknown, labels: Record<string, string> = {}): string {
  const node = object(value), op = String(node.op);
  if (op === "input") return labels[String(node.id)] ?? fieldLabel(String(node.id));
  if (op === "const") return `${String(node.value ?? "")} ${node.unit === "one" ? "" : fieldLabel(String(node.unit ?? ""))}`.trim();
  if (op === "date_diff_days") return `日期间隔（${formulaText(node.start, labels)} → ${formulaText(node.end, labels)}）`;
  if (op === "divide") return `（${formulaText(node.left, labels)} ÷ ${formulaText(node.right, labels)}）`;
  return `（${records(node.args).map(item => formulaText(item, labels)).join(op === "multiply" ? " × " : op === "subtract" ? " − " : " ＋ ")}）`;
}
export function businessSummary(kind: LogicKind, definition: LogicDocument) {
  if (kind === "decision_table") return `${records(definition.inputs).length} 项条件 · ${records(definition.rules).length} 条规则 · ${records(definition.outputs).length} 项结果`;
  if (kind === "decision_model") return `${records(definition.nodes).length} 个逻辑步骤 · ${Object.keys(object(definition.outputs)).length} 项最终结果`;
  if (kind === "model") return formulaText(definition.formula, Object.fromEntries(records(definition.inputs).map(input => [String(input.id), fieldLabel(String(input.id), [], input.label)])));
  return `${records(definition.inputs).length} 项属性 · ${records(definition.checks).length} 个判断条件`;
}
export function displayValue(value: unknown): string {
  if (value === null || value === undefined || value === "") return "未填写";
  if (typeof value === "boolean") return value ? "是" : "否";
  if (Array.isArray(value)) return value.map(displayValue).join("、") || "无";
  if (typeof value === "object") { const quantity = object(value); if (("value" in quantity || "literal" in quantity) && "unit" in quantity) return `${quantity.value ?? quantity.literal} ${quantity.unit}`; return Object.entries(quantity).map(([key, child]) => `${fieldLabel(key)}：${displayValue(child)}`).join("；"); }
  return names[String(value)] ?? String(value);
}
export function projectExpected(actual: unknown, expected: unknown): unknown {
  if (Array.isArray(expected) && Array.isArray(actual)) return actual.map((item, index) => index < expected.length ? projectExpected(item, expected[index]) : item);
  if (expected && typeof expected === "object" && !Array.isArray(expected) && actual && typeof actual === "object" && !Array.isArray(actual)) return Object.fromEntries(Object.entries(expected).map(([key, child]) => [key, projectExpected(object(actual)[key], child)]));
  return actual;
}
export function sourceLabel(binding: unknown, graph: LogicDocument, assets: LogicAsset[]) {
  const row = object(binding); if (row.source === "literal") return `固定值 · ${displayValue(row.value)}`;
  if (row.source === "facts") return `场景输入 · ${pathLabel((row.path ?? []) as string[])}`;
  const node = records(graph.nodes).find(node => node.id === row.node), asset = assets.find(asset => asset.ref === node?.ref);
  return `${String(node?.label ?? (asset ? assetLabel(asset) : row.node ?? "待选择"))} · ${pathLabel((row.path ?? []) as string[])}`;
}
export function readableKind(kind: string) { return logicKindLabels[kind as LogicKind] ?? kind; }
