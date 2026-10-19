"use client";

import { Plus, Trash2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { object, records, type LogicAsset, type LogicDocument, type LogicKind } from "./logic-api";
import { definitionFor, displayValue, fieldLabel, pathLabel, projectExpected } from "./logic-visual";
import type { OntologyDefinition } from "../studio-data";

export type ValueHint = { label?: string; unit?: string; type?: string; units?: string[] };
export function expectedValueHints(kind: LogicKind, definition: LogicDocument): Record<string, ValueHint> {
  const hints: Record<string, ValueHint> = {};
  if (kind === "decision_table") for (const output of records(definition.outputs)) hints[`result/${definition.hit_policy === "COLLECT" ? "*/" : ""}${output.id}`] = { label: fieldLabel(String(output.id), [], output.label), type: String(output.type) };
  if (kind === "decision_model") for (const key of Object.keys(object(definition.outputs))) hints[`output/${key}`] = { label: fieldLabel(key, [], object(definition.output_labels)[key]) };
  return hints;
}
export function valueHints(kind: LogicKind, definition: LogicDocument, assets: LogicAsset[], definitions: OntologyDefinition[]): Record<string, ValueHint> {
  const hints: Record<string, ValueHint> = {};
  const store = (path: string[], hint: ValueHint) => { hints[path.join("/")] = hint; };
  if (kind === "decision_model") for (const field of records(definition.input_fields)) store([String(field.id)], { label: String(field.label), type: String(field.type), unit: field.unit ? String(field.unit) : undefined });
  if (kind === "model") for (const input of records(definition.inputs)) store(["inputs", String(input.id)], { label: fieldLabel(String(input.id), definitions, input.label), unit: String(input.unit), type: (input.type === "date" || input.unit === "iso8601_date") ? "date" : "number" });
  else if (kind === "decision_table") for (const input of records(definition.inputs)) store([String(input.source), String(input.field)], { label: fieldLabel(String(input.field), definitions, input.label), unit: input.unit ? String(input.unit) : undefined, type: String(input.type) });
  else if (kind === "decision_model") for (const node of records(definition.nodes)) {
    const source = definitionFor(node, assets), nested = valueHints(node.kind as LogicKind, source, [], definitions);
    for (const [key, raw] of Object.entries(object(node.bindings))) {
      const binding = object(raw); if (binding.source !== "facts") continue;
      const path = (binding.path ?? []) as string[];
      const direct = nested[`inputs/${key}`] ?? nested[`facts/${key}`] ?? nested[key];
      if (direct) store(path, direct);
      else for (const [sourcePath, hint] of Object.entries(nested)) if (sourcePath === key || sourcePath.startsWith(`${key}/`)) store([...path, ...sourcePath.split("/").slice(1)], hint);
    }
  } else for (const input of records(definition.inputs)) {
    const predicate = definitions.find(item => item.id === input.predicate), config = predicate?.config ?? {};
    const path = input.role === "candidate" ? ["candidates", "*", String(input.predicate)] : input.role === "subject" ? ["subject", String(input.predicate)] : ["contexts", String(input.role), String(input.predicate)];
    store(path, { label: fieldLabel(String(input.predicate), definitions), type: String(config.value_type), unit: String(config.default_unit ?? (config.units as string[])?.[0] ?? ""), units: config.units as string[] });
  }
  return hints;
}

export function BusinessValues({ value, onChange, disabled = false, definitions = [], hints = {}, label = "业务输入", path = [], allowStructure = false }: { value: unknown; onChange: (next: unknown) => void; disabled?: boolean; definitions?: OntologyDefinition[]; hints?: Record<string, ValueHint>; label?: string; path?: string[]; allowStructure?: boolean }) {
  const hint = hints[path.join("/")] ?? hints[path.map(key => /^\d+$/.test(key) ? "*" : key).join("/")] ?? {};
  const name = hint.label ?? label, record = object(value), quantity = value && typeof value === "object" && !Array.isArray(value) && ("literal" in record || "value" in record) && "unit" in record;
  const unit = String(record.unit ?? hint.unit ?? "");
  const dimension = definitions.find(item => item.id === `unit:${unit}`)?.config.dimension;
  const units = [...new Set([unit, ...(hint.units ?? definitions.filter(item => item.kind === "unit" && dimension && item.config.dimension === dimension).map(item => item.id.replace(/^unit:/, "")))] )].filter(Boolean);
  const changeType = (type: string) => onChange(type === "object" ? { value: "", unit: hint.unit ?? "" } : type === "list" ? [""] : type === "boolean" ? false : "");
  if (quantity || hint.unit && (value === undefined || typeof value === "string" || typeof value === "number") && hint.unit !== "one" && hint.type !== "date") return <label className="business-value-field"><span>{name}</span><div className="business-quantity"><Input aria-label={name} inputMode="decimal" value={String(quantity ? record.literal ?? record.value ?? "" : value ?? "")} disabled={disabled} onChange={event => onChange({ ...(quantity ? record : {}), [quantity && "value" in record ? "value" : "literal"]: event.target.value, unit })} /><select aria-label={`${name}单位`} value={unit} disabled={disabled || units.length < 2} onChange={event => onChange({ ...(quantity ? record : { literal: value ?? "" }), unit: event.target.value })}>{units.map(item => <option key={item} value={item}>{item}</option>)}</select></div></label>;
  if (Array.isArray(value)) return <fieldset className="business-value-group"><legend>{name}</legend>{value.map((item, index) => <div className="business-array-row" key={index}><BusinessValues value={item} onChange={next => onChange(value.map((old, position) => position === index ? next : old))} disabled={disabled} definitions={definitions} hints={hints} path={[...path, String(index)]} label={`第 ${index + 1} 项`} allowStructure={allowStructure} />{!disabled && <Button size="icon-sm" variant="ghost" aria-label={`移除${name}第${index + 1}项`} onClick={() => onChange(value.filter((_, position) => position !== index))}><Trash2 /></Button>}</div>)}{!value.length && <p className="logic-note">目前没有项目。</p>}{!disabled && <Button size="sm" variant="outline" onClick={() => { const item = value[0] && typeof value[0] === "object" ? { ...structuredClone(object(value[0])), ...(Object.hasOwn(object(value[0]), "id") ? { id: "" } : {}) } : ""; onChange([...value, item]); }}><Plus />添加{name === "合格候选" ? "候选" : "项目"}</Button>}</fieldset>;
  if (value !== null && typeof value === "object") return <fieldset className="business-value-group"><legend>{name}</legend><div className="business-value-fields">{Object.entries(record).map(([key, child]) => <BusinessValues key={key} value={child} onChange={next => onChange({ ...record, [key]: next })} disabled={disabled} definitions={definitions} hints={hints} path={[...path, key]} label={fieldLabel(key, definitions)} allowStructure={allowStructure} />)}</div>{!Object.keys(record).length && <p className="logic-note">尚未定义字段，请在规则白板中添加输入或输出。</p>}</fieldset>;
  const verdict = path.at(-1) === "verdict", status = path.at(-1) === "status";
  return <label className="business-value-field"><span>{name}</span><div className="business-value-control">{typeof value === "boolean" || hint.type === "boolean" ? <select aria-label={name} value={String(value)} disabled={disabled} onChange={event => onChange(event.target.value === "true")}><option value="true">是</option><option value="false">否</option></select> : verdict || status ? <select aria-label={name} value={String(value ?? "")} disabled={disabled} onChange={event => onChange(event.target.value)}>{(status ? ["complete", "incomplete", "blocked"] : ["valid", "invalid", "unknown", "eligible", "condition_failed", "not_matched"]).map(item => <option key={item} value={item}>{({ condition_failed: "候选条件未通过", not_matched: "不适用" } as Record<string, string>)[item] ?? fieldLabel(item)}</option>)}</select> : <Input aria-label={name} inputMode={hint.type === "number" ? "decimal" : undefined} value={String(value ?? "")} disabled={disabled} placeholder={hint.type === "date" ? "填写日期或带时区的时间" : "填写业务值"} onChange={event => onChange(event.target.value)} />}{allowStructure && !disabled && <select className="business-value-type" aria-label={`${name}值形式`} value="scalar" onChange={event => changeType(event.target.value)}><option value="scalar">单值</option><option value="object">数值与单位</option><option value="boolean">是否值</option><option value="list">多个值</option></select>}</div></label>;
}

export function BusinessData({ value, label = "结果", definitions = [], labels = {} }: { value: unknown; label?: string; definitions?: OntologyDefinition[]; labels?: Record<string, string> }) {
  if (!value || typeof value !== "object" || ("unit" in object(value) && ("value" in object(value) || "literal" in object(value)))) return <div className="business-data-leaf"><span>{label}</span><strong>{displayValue(value)}</strong></div>;
  if (Array.isArray(value)) return <div className="business-data-array" aria-label={label}>{value.length ? value.map((item, index) => <BusinessData key={index} value={item} label={`${label} ${index + 1}`} definitions={definitions} labels={labels} />) : <p className="logic-note">{label}：无</p>}</div>;
  return <dl className="business-data-grid">{Object.entries(value).map(([key, child]) => <div key={key}><dt>{labels[key] ?? fieldLabel(key, definitions)}</dt><dd>{child && typeof child === "object" ? <BusinessData value={child} label={fieldLabel(key, definitions)} definitions={definitions} labels={labels} /> : <strong>{displayValue(child)}</strong>}</dd></div>)}</dl>;
}

export function CaseResult({ expected, actual, definitions, labels = {} }: { expected: unknown; actual: unknown; definitions: OntologyDefinition[]; labels?: Record<string, string> }) { return <div className="business-case-comparison"><section><h4>业务预期</h4><BusinessData value={expected} definitions={definitions} labels={labels} /></section><section><h4>实际结果</h4><BusinessData value={projectExpected(actual, expected)} definitions={definitions} labels={labels} /></section></div>; }
export const inputHintSummary = (path: string[], definitions: OntologyDefinition[]) => pathLabel(path, definitions);
