"use client";

import { useEffect, useRef, useState, type ReactNode } from "react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { BarChart3, CircleAlert, FileText, FlaskConical, Loader2, Plus, RefreshCw, Search, Send, ShieldCheck, Trash2 } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { useWorkspaceSession } from "../workspace-session";
import { NavigationToggle } from "../workspace-navigation-context";
import {
  applicable, applySuggestion, assetVersion, bumpVersion, cleanDraft, loadFeedback, percent, suggestionKinds, suggestionStatus, compareVersions, cueSegments, cueTypes, isStableId, lines, loadExtractionAssets, mapping,
  mappingRoles, newCard, newRecipe, object, pairs, pendingFor, previewAsset, proposeAsset, routeLabels, selectorKinds, strings,
  type AssetKind, type AssetPreview, type ExtractionFeedback, type Suggestion, type ConceptInfo, type ExtractionAssets, type JsonRecord, type PropertyInfo, type ValueSample,
} from "./extraction-api";

type Selection = { kind: AssetKind; id: string; isNew?: boolean };
type Draft = JsonRecord;
type Update = (updater: (draft: Draft) => Draft) => void;

function setIn(value: unknown, path: (string | number)[], next: unknown): Draft {
  const [head, ...rest] = path;
  const base: JsonRecord | unknown[] = Array.isArray(value) ? [...value] : { ...object(value) };
  const current = Array.isArray(base) ? base[head as number] : (base as JsonRecord)[head as string];
  const replaced = rest.length ? setIn(current, rest, next) : next;
  if (Array.isArray(base)) base[head as number] = replaced; else (base as JsonRecord)[head as string] = replaced;
  return base as Draft;
}
function selectionKey(selection: Selection) { return `${selection.kind}:${selection.id}`; }
function rows(value: unknown): JsonRecord[] { return Array.isArray(value) ? value.map(item => object(item)) : []; }

// ---------------------------------------------------------------------------
// Small controlled fields. Text areas keep their own text so blank lines survive typing.

function Field({ label, hint, children }: { label: string; hint?: string; children: ReactNode }) {
  return <label className="extraction-field"><span>{label}</span>{children}{hint && <small>{hint}</small>}</label>;
}

function LinesField({ label, hint, value, onChange, disabled, placeholder }: { label: string; hint?: string; value: string[]; onChange: (value: string[]) => void; disabled?: boolean; placeholder?: string }) {
  const [text, setText] = useState(() => value.join("\n"));
  return <Field label={label} hint={hint ?? "每行一项"}><Textarea rows={Math.min(Math.max(value.length + 1, 2), 8)} value={text} disabled={disabled} placeholder={placeholder}
    onChange={event => { setText(event.target.value); onChange(lines(event.target.value)); }} /></Field>;
}

function PairsField({ label, hint, value, options, keyLabel, valueLabel, onChange, disabled }: {
  label: string; hint?: string; value: Record<string, string>; options?: { value: string; label: string }[]; keyLabel: string; valueLabel: string;
  onChange: (value: Record<string, string>) => void; disabled?: boolean;
}) {
  const [entries, setEntries] = useState<[string, string][]>(() => pairs(value));
  function change(next: [string, string][]) { setEntries(next); onChange(mapping(next)); }
  return <div className="extraction-field"><span>{label}</span>
    <div className="extraction-rows">
      {entries.map(([key, target], index) => <div className="extraction-row" key={index}>
        <Input aria-label={`${label}：${keyLabel}`} placeholder={keyLabel} value={key} disabled={disabled} onChange={event => change(entries.map((entry, position) => position === index ? [event.target.value, entry[1]] : entry))} />
        <span aria-hidden="true">→</span>
        {options ? <select aria-label={`${label}：${valueLabel}`} value={target} disabled={disabled} onChange={event => change(entries.map((entry, position) => position === index ? [entry[0], event.target.value] : entry))}>
          <option value="">选择{valueLabel}</option>{options.map(option => <option key={option.value} value={option.value}>{option.label}</option>)}
        </select> : <Input aria-label={`${label}：${valueLabel}`} placeholder={valueLabel} value={target} disabled={disabled} onChange={event => change(entries.map((entry, position) => position === index ? [entry[0], event.target.value] : entry))} />}
        <Button type="button" variant="ghost" size="icon-sm" aria-label="删除此行" disabled={disabled} onClick={() => change(entries.filter((_, position) => position !== index))}><Trash2 /></Button>
      </div>)}
      <Button type="button" variant="outline" size="xs" disabled={disabled} onClick={() => change([...entries, ["", options?.length === 1 ? options[0].value : ""]])}><Plus />添加一行</Button>
    </div>{hint && <small>{hint}</small>}</div>;
}

function PatternRows({ label, value, onChange, disabled }: { label: string; value: JsonRecord[]; onChange: (value: JsonRecord[]) => void; disabled?: boolean }) {
  return <div className="extraction-field"><span>{label}</span><div className="extraction-rows">
    {value.map((pattern, index) => <div className="extraction-row extraction-row-wide" key={index}>
      <Input aria-label="模式标识" placeholder="标识，如 material_code" value={String(pattern.id ?? "")} disabled={disabled} onChange={event => onChange(value.map((item, position) => position === index ? { ...item, id: event.target.value } : item))} />
      <Input aria-label="正则表达式" placeholder="正则，如 ^[A-Z0-9-]{3,}$" value={String(pattern.pattern ?? "")} disabled={disabled} onChange={event => onChange(value.map((item, position) => position === index ? { ...item, pattern: event.target.value } : item))} />
      <Input aria-label="模式说明" placeholder="说明（可选）" value={String(pattern.description ?? "")} disabled={disabled} onChange={event => onChange(value.map((item, position) => position === index ? { ...item, description: event.target.value } : item))} />
      <Button type="button" variant="ghost" size="icon-sm" aria-label="删除模式" disabled={disabled} onClick={() => onChange(value.filter((_, position) => position !== index))}><Trash2 /></Button>
    </div>)}
    <Button type="button" variant="outline" size="xs" disabled={disabled} onClick={() => onChange([...value, { id: "", pattern: "" }])}><Plus />添加模式</Button>
  </div><small>用于定位编码或取值的线索；标识用小写字母、数字和下划线。</small></div>;
}

function ExampleRows({ label, value, onChange, disabled }: { label: string; value: JsonRecord[]; onChange: (value: JsonRecord[]) => void; disabled?: boolean }) {
  return <div className="extraction-field"><span>{label}</span><div className="extraction-rows">
    {value.map((example, index) => <div className="extraction-row extraction-row-example" key={index}>
      <Textarea aria-label={`${label}原文`} rows={2} placeholder="原文片段" value={String(example.text ?? "")} disabled={disabled} onChange={event => onChange(value.map((item, position) => position === index ? { ...item, text: event.target.value } : item))} />
      <Input aria-label={`${label}说明`} placeholder="为什么算或不算" value={String(example.note ?? "")} disabled={disabled} onChange={event => onChange(value.map((item, position) => position === index ? { ...item, note: event.target.value } : item))} />
      <Button type="button" variant="ghost" size="icon-sm" aria-label="删除示例" disabled={disabled} onClick={() => onChange(value.filter((_, position) => position !== index))}><Trash2 /></Button>
    </div>)}
    <Button type="button" variant="outline" size="xs" disabled={disabled} onClick={() => onChange([...value, { text: "" }])}><Plus />添加示例</Button>
  </div></div>;
}

function Tabs<T extends string>({ value, options, onChange }: { value: T; options: Record<T, string>; onChange: (value: T) => void }) {
  return <div className="extraction-tabs" role="tablist">{(Object.keys(options) as T[]).map(key => <button key={key} type="button" role="tab" aria-selected={value === key} onClick={() => onChange(key)}>{options[key]}</button>)}</div>;
}

// ---------------------------------------------------------------------------
// Editors

function unitOptions(property: PropertyInfo, assets: ExtractionAssets) {
  const ids = property.units.length ? property.units : property.default_unit ? [property.default_unit] :
    property.dimension ? assets.units.filter(unit => unit.dimension === property.dimension).map(unit => unit.id) : [];
  return ids.length ? ids.map(id => ({ value: id, label: assets.units.find(unit => unit.id === id)?.label ? `${id} · ${assets.units.find(unit => unit.id === id)?.label}` : id })) : undefined;
}

function PropertyEditor({ property, entry, assets, update, disabled }: { property: PropertyInfo; entry: JsonRecord; assets: ExtractionAssets; update: (path: (string | number)[], value: unknown) => void; disabled: boolean }) {
  const card = object(entry.card);
  return <details className="extraction-property" open={Object.keys(entry).length > 0}>
    <summary><strong>{property.label}</strong><code>{property.predicate}</code><span className="neutral-badge">{property.type}{property.units.length ? ` · ${property.units.join(" / ")}` : property.default_unit ? ` · ${property.default_unit}` : ""}</span>
      <span className="extraction-route" data-route={property.publication_route}>{routeLabels[property.publication_route]}</span>{property.required && <span className="neutral-badge">发布必填</span>}</summary>
    <div className="extraction-property-body">
      {property.description && <p className="change-section-note">{property.description}</p>}
      <div className="extraction-grid">
        <Field label="中文首选名称"><Input value={String(object(object(entry.terms).pref).zh ?? "")} disabled={disabled} placeholder="例如 额定功率" onChange={event => update(["terms", "pref", "zh"], event.target.value)} /></Field>
        <LinesField label="其他叫法" hint="文档中可能出现的字段名、表头或口语说法，每行一项" value={strings(object(entry.terms).alt)} disabled={disabled} onChange={value => update(["terms", "alt"], value)} />
      </div>
      {property.type === "quantity" && <PairsField label="单位写法" hint="原文单位写法 → 注册单位；只能选择该属性允许的单位" keyLabel="原文写法，如 千瓦" valueLabel="注册单位" value={object(entry.unit_aliases) as Record<string, string>} options={unitOptions(property, assets)} disabled={disabled} onChange={value => update(["unit_aliases"], value)} />}
      {property.type === "enum" && <PairsField label="枚举写法" hint="原文说法 → 可选值" keyLabel="原文说法，如 高风险" valueLabel="可选值" value={object(entry.enum_map) as Record<string, string>} options={property.allowed.map(value => ({ value, label: value }))} disabled={disabled} onChange={value => update(["enum_map"], value)} />}
      <PatternRows label="值模式" value={rows(entry.value_patterns)} disabled={disabled} onChange={value => update(["value_patterns"], value)} />
      <div className="extraction-grid">
        <Field label="属性说明（给 AI 看）"><Textarea rows={2} value={String(object(card.definition).zh ?? "")} disabled={disabled} placeholder="什么情况下才算这个属性的取值" onChange={event => update(["card", "definition", "zh"], event.target.value)} /></Field>
        <LinesField label="识别线索" value={strings(card.recognition_cues)} disabled={disabled} onChange={value => update(["card", "recognition_cues"], value)} />
      </div>
    </div>
  </details>;
}

type CardTab = "overview" | "properties" | "identity" | "trial";

function CardEditor({ concept, draft, onChange, assets, disabled, tab, onTab }: { concept: ConceptInfo; draft: Draft; onChange: Update; assets: ExtractionAssets; disabled: boolean; tab: CardTab; onTab: (tab: CardTab) => void }) {
  const update = (path: (string | number)[], value: unknown) => onChange(current => setIn(current, path, value));
  const card = object(draft.card), identity = object(draft.identity), properties = object(draft.properties);
  const keys = rows(identity.keys);
  return <>
    <Tabs value={tab} onChange={onTab} options={{ overview: "识别说明", properties: `属性写法 · ${concept.properties.length}`, identity: "身份规则", trial: "试算与预览" }} />
    {tab === "overview" && <section className="change-section extraction-section">
      <div className="extraction-grid">
        <Field label="中文定义"><Textarea rows={3} value={String(object(card.definition).zh ?? "")} disabled={disabled} placeholder="这个概念在文档里指什么" onChange={event => update(["card", "definition", "zh"], event.target.value)} /></Field>
        <Field label="English definition"><Textarea rows={3} value={String(object(card.definition).en ?? "")} disabled={disabled} onChange={event => update(["card", "definition", "en"], event.target.value)} /></Field>
      </div>
      <div className="extraction-grid">
        <LinesField label="识别线索" hint="看到这些字眼时要考虑这个概念，每行一项" value={strings(card.recognition_cues)} disabled={disabled} onChange={value => update(["card", "recognition_cues"], value)} />
        <div>
          <LinesField label="其他叫法" hint="补充业务术语表之外的叫法，每行一项" value={strings(object(draft.terms).alt)} disabled={disabled} onChange={value => update(["terms", "alt"], value)} />
          <p className="extraction-vocabulary">业务术语表：{Object.values(concept.terms.pref).join(" / ") || concept.label}{concept.terms.alt.length > 0 && `；其他叫法 ${concept.terms.alt.join("、")}`}</p>
        </div>
      </div>
      <ExampleRows label="正例" value={rows(card.positive_examples)} disabled={disabled} onChange={value => update(["card", "positive_examples"], value)} />
      <ExampleRows label="反例" value={rows(card.negative_examples)} disabled={disabled} onChange={value => update(["card", "negative_examples"], value)} />
      <div className="extraction-grid">
        <LinesField label="边界说明" value={strings(card.boundary_notes)} disabled={disabled} onChange={value => update(["card", "boundary_notes"], value)} />
        <LinesField label="常见位置" value={strings(card.typical_locations)} disabled={disabled} onChange={value => update(["card", "typical_locations"], value)} />
      </div>
    </section>}
    {tab === "properties" && <section className="change-section extraction-section">
      <p className="change-section-note">为每个属性维护文档中的叫法、单位与枚举写法和值模式。“业务系统维护”的属性仍会被抽取，但只登记为待核对事项，不写入 Git 真源。</p>
      {concept.properties.map(property => <PropertyEditor key={property.predicate} property={property} entry={object(properties[property.predicate])} assets={assets} disabled={disabled}
        update={(path, value) => update(["properties", property.predicate, ...path], value)} />)}
      {concept.properties.length === 0 && <p className="change-section-note">该概念尚未绑定属性，请先在本体白板绑定。</p>}
    </section>}
    {tab === "identity" && <section className="change-section extraction-section">
      <p className="change-section-note">身份键决定哪些编码能唯一识别实例；最终是否为同一实例仍由服务端依据证据判定。</p>
      {keys.map((key, index) => <div className="extraction-card-block" key={index}>
        <div className="extraction-grid">
          <Field label="来源"><select value={String(key.source ?? "natural_key")} disabled={disabled} onChange={event => update(["identity", "keys", index], { ...key, source: event.target.value, ...(event.target.value === "natural_key" ? { predicate: undefined } : {}) })}>
            <option value="natural_key">业务编码（实例 key）</option><option value="predicate">属性取值</option></select></Field>
          {key.source === "predicate" ? <Field label="属性"><select value={String(key.predicate ?? "")} disabled={disabled} onChange={event => update(["identity", "keys", index, "predicate"], event.target.value)}>
            <option value="">选择属性</option>{concept.properties.map(property => <option key={property.predicate} value={property.predicate}>{property.label}（{property.predicate}）</option>)}</select></Field>
            : <Field label="编码命名空间" hint="留空表示默认命名空间"><Input value={String(key.namespace ?? "")} disabled={disabled} onChange={event => update(["identity", "keys", index, "namespace"], event.target.value)} /></Field>}
        </div>
        <PatternRows label="编码模式" value={rows(key.patterns)} disabled={disabled} onChange={value => update(["identity", "keys", index, "patterns"], value)} />
        <div className="change-actions"><Button type="button" variant="ghost" size="sm" disabled={disabled} onClick={() => update(["identity", "keys"], keys.filter((_, position) => position !== index))}><Trash2 />删除身份键</Button></div>
      </div>)}
      <Button type="button" variant="outline" size="sm" disabled={disabled} onClick={() => update(["identity", "keys"], [...keys, { source: "natural_key" }])}><Plus />添加身份键</Button>
      <div className="extraction-field"><span>建档最少字段</span><div className="extraction-checks">{concept.properties.map(property => {
        const selected = strings(identity.minimum_fields).includes(property.predicate);
        return <label key={property.predicate}><input type="checkbox" checked={selected} disabled={disabled} onChange={() => update(["identity", "minimum_fields"], selected ? strings(identity.minimum_fields).filter(item => item !== property.predicate) : [...strings(identity.minimum_fields), property.predicate])} />{property.label}</label>;
      })}</div><small>缺少这些字段的新实例直接拒绝；缺少发布必填字段的实例只进入补全队列。</small></div>
    </section>}
  </>;
}

function RecipeEditor({ draft, onChange, assets, disabled, isNew }: { draft: Draft; onChange: Update; assets: ExtractionAssets; disabled: boolean; isNew: boolean }) {
  const update = (path: (string | number)[], value: unknown) => onChange(current => setIn(current, path, value));
  const match = object(draft.match), mappings = rows(draft.mappings);
  const concepts = assets.concepts.filter(concept => !concept.excluded);
  return <section className="change-section extraction-section">
    <div className="extraction-grid">
      <Field label="方案标识" hint="小写字母、数字和下划线；创建后不可修改"><Input value={String(draft.id ?? "")} disabled={disabled || !isNew} onChange={event => update(["id"], event.target.value)} /></Field>
      <LinesField label="适用文档类型" value={strings(draft.document_types)} disabled={disabled} onChange={value => update(["document_types"], value)} placeholder="supplier_review_minutes" />
    </div>
    <div className="extraction-grid">
      <LinesField label="标题匹配（正则）" value={strings(match.title_patterns)} disabled={disabled} onChange={value => update(["match", "title_patterns"], value)} />
      <LinesField label="来源类型" value={strings(match.source_kinds)} disabled={disabled} onChange={value => update(["match", "source_kinds"], value)} placeholder="meeting_minutes" />
    </div>
    <div className="extraction-field"><span>映射：文档位置 → 概念与属性</span><div className="extraction-mappings">
      {mappings.map((item, index) => {
        const selector = object(item.selector), concept = concepts.find(value => value.id === item.concept);
        return <div className="extraction-mapping" key={index}>
          <select aria-label="位置类型" value={String(selector.kind ?? "heading")} disabled={disabled} onChange={event => update(["mappings", index, "selector", "kind"], event.target.value)}>{Object.entries(selectorKinds).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select>
          <Input aria-label="匹配正则" placeholder="正则，如 ^(标准)?交期$" value={String(selector.pattern ?? "")} disabled={disabled} onChange={event => update(["mappings", index, "selector", "pattern"], event.target.value)} />
          <select aria-label="概念" value={String(item.concept ?? "")} disabled={disabled} onChange={event => update(["mappings", index], { ...item, concept: event.target.value, predicate: undefined })}><option value="">选择概念</option>{concepts.map(value => <option key={value.id} value={value.id}>{value.label}</option>)}</select>
          <select aria-label="作用" value={String(item.role ?? "field")} disabled={disabled} onChange={event => update(["mappings", index, "role"], event.target.value)}>{Object.entries(mappingRoles).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select>
          {item.role === "relation" ? <select aria-label="关系" value={String(item.relation ?? "")} disabled={disabled} onChange={event => update(["mappings", index, "relation"], event.target.value)}><option value="">选择关系</option>{assets.relations.map(relation => <option key={relation.id} value={relation.id}>{relation.label}</option>)}</select>
            : item.role === "field" ? <select aria-label="属性" value={String(item.predicate ?? "")} disabled={disabled || !concept} onChange={event => update(["mappings", index, "predicate"], event.target.value)}><option value="">选择属性</option>{(concept?.properties ?? []).map(property => <option key={property.predicate} value={property.predicate}>{property.label}</option>)}</select> : <span />}
          <Button type="button" variant="ghost" size="icon-sm" aria-label="删除映射" disabled={disabled} onClick={() => update(["mappings"], mappings.filter((_, position) => position !== index))}><Trash2 /></Button>
        </div>;
      })}
      <Button type="button" variant="outline" size="xs" disabled={disabled} onClick={() => update(["mappings"], [...mappings, { selector: { kind: "table_header", pattern: "" }, concept: "", role: "field" }])}><Plus />添加映射</Button>
    </div><small>“表头或行标签”匹配表头单元格和首列；“键值标签”匹配“标签：值”中冒号前的文字。</small></div>
  </section>;
}

function ProfileEditor({ draft, onChange, assets, disabled }: { draft: Draft; onChange: Update; assets: ExtractionAssets; disabled: boolean }) {
  const update = (path: (string | number)[], value: unknown) => onChange(current => setIn(current, path, value));
  const limits = object(draft.limits), acceptance = object(draft.acceptance), authority = object(draft.source_authority);
  const excluded = strings(draft.excluded_concepts);
  const limitLabels: Record<string, string> = { max_blocks: "最大块数", max_instances: "最大实例数", max_fields_per_instance: "每实例最大字段数", max_anchors_per_field: "每字段最大锚点数", max_quote_chars: "最大引文长度" };
  return <section className="change-section extraction-section">
    <div className="extraction-grid">
      <Field label="默认时区偏移" hint="只有日期的原文按此解释，如 +08:00"><Input value={String(draft.default_utc_offset ?? "")} disabled={disabled} onChange={event => update(["default_utc_offset"], event.target.value)} /></Field>
      <Field label="引文匹配"><select value={String(acceptance.quote_match ?? "exact")} disabled={disabled} onChange={event => update(["acceptance", "quote_match"], event.target.value)}><option value="exact">精确匹配</option><option value="normalized_exact">允许全角与空白差异</option></select></Field>
      <Field label="最低置信度" hint="0 到 1；低于此值的项不进入提案"><Input type="number" min={0} max={1} step={0.05} value={String(acceptance.min_confidence ?? 0)} disabled={disabled} onChange={event => update(["acceptance", "min_confidence"], Number(event.target.value))} /></Field>
    </div>
    <div className="extraction-checks">
      <label><input type="checkbox" checked={acceptance.draft_instances === true} disabled={disabled} onChange={event => update(["acceptance", "draft_instances"], event.target.checked)} />缺少必填字段时建草稿实例</label>
      <label><input type="checkbox" checked={acceptance.relations_between_new_instances === true} disabled={disabled} onChange={event => update(["acceptance", "relations_between_new_instances"], event.target.checked)} />允许新实例之间建立关系</label>
    </div>
    <div className="extraction-grid extraction-grid-five">{Object.entries(limitLabels).map(([key, label]) => <Field key={key} label={label}><Input type="number" min={1} value={String(limits[key] ?? "")} disabled={disabled} onChange={event => update(["limits", key], Number(event.target.value))} /></Field>)}</div>
    <div className="extraction-field"><span>不开放抽取的概念</span><div className="extraction-checks">{assets.concepts.map(concept => <label key={concept.id}><input type="checkbox" checked={excluded.includes(concept.id)} disabled={disabled} onChange={() => update(["excluded_concepts"], excluded.includes(concept.id) ? excluded.filter(item => item !== concept.id) : [...excluded, concept.id])} />{concept.label}</label>)}</div></div>
    <div className="extraction-grid">
      <PairsField label="新实例 ID 前缀" keyLabel="概念" valueLabel="前缀" value={object(draft.identity_prefixes) as Record<string, string>} disabled={disabled} onChange={value => update(["identity_prefixes"], value)} />
      <div>
        <Field label="默认来源等级"><Input value={String(authority.default ?? "")} disabled={disabled} onChange={event => update(["source_authority", "default"], event.target.value)} /></Field>
        <PairsField label="来源系统 → 权威等级" keyLabel="来源系统，如 dms" valueLabel="等级，如 research" value={object(authority.systems) as Record<string, string>} disabled={disabled} onChange={value => update(["source_authority", "systems"], value)} />
      </div>
    </div>
    <div className="extraction-grid">
      <PairsField label="全局单位写法" keyLabel="原文写法" valueLabel="注册单位" value={object(draft.unit_aliases) as Record<string, string>} options={assets.units.map(unit => ({ value: unit.id, label: `${unit.id} · ${unit.label}` }))} disabled={disabled} onChange={value => update(["unit_aliases"], value)} />
      <PairsField label="币种写法" keyLabel="原文写法" valueLabel="币种" value={object(draft.currency_aliases) as Record<string, string>} options={assets.currencies.map(code => ({ value: code, label: code }))} disabled={disabled} onChange={value => update(["currency_aliases"], value)} />
    </div>
  </section>;
}

function TrialPanel({ kind, properties, conceptId, draft, canPreview }: { kind: AssetKind; properties: PropertyInfo[]; conceptId?: string; draft: Draft; canPreview: boolean }) {
  const [text, setText] = useState("");
  const [samples, setSamples] = useState<ValueSample[]>([]);
  const [result, setResult] = useState<AssetPreview | null>(null);
  const [busy, setBusy] = useState(false), [error, setError] = useState("");
  async function run() {
    setBusy(true); setError("");
    try { setResult(await previewAsset(kind, cleanDraft(kind, draft), { sampleText: text, samples: samples.filter(sample => sample.predicate && sample.raw.trim()).map(sample => ({ ...sample, concept: conceptId })) })); }
    catch (cause) { setError(cause instanceof Error ? cause.message : "试算失败"); }
    finally { setBusy(false); }
  }
  return <section className="change-section extraction-section">
    <p className="change-section-note">用当前草稿试算：粘贴一段材料查看会命中的线索；填写原文取值查看服务端规范化结果；同时生成外部 Agent 将收到的标准说明。试算不写入任何数据。</p>
    <Field label="样例材料" hint="每行作为一个文本块；以“一、”或 # 开头的行视为标题"><Textarea rows={6} value={text} onChange={event => setText(event.target.value)} placeholder={"一、设备要求\n3号测试台（设备编号 EQ-TB-03）额定功率 7.5kW"} /></Field>
    {properties.length > 0 && <div className="extraction-field"><span>取值试算</span><div className="extraction-rows">
      {samples.map((sample, index) => <div className="extraction-row" key={index}>
        <select aria-label="试算属性" value={sample.predicate} onChange={event => setSamples(samples.map((item, position) => position === index ? { ...item, predicate: event.target.value } : item))}><option value="">选择属性</option>{properties.map(property => <option key={property.predicate} value={property.predicate}>{property.label}</option>)}</select>
        <Input aria-label="原文取值" placeholder="原文，如 约30天、5万元、高风险" value={sample.raw} onChange={event => setSamples(samples.map((item, position) => position === index ? { ...item, raw: event.target.value } : item))} />
        <Button type="button" variant="ghost" size="icon-sm" aria-label="删除试算" onClick={() => setSamples(samples.filter((_, position) => position !== index))}><Trash2 /></Button>
      </div>)}
      <Button type="button" variant="outline" size="xs" onClick={() => setSamples([...samples, { predicate: properties[0]?.predicate ?? "", raw: "" }])}><Plus />添加取值</Button>
    </div></div>}
    <div className="change-actions"><Button type="button" variant="outline" disabled={busy || !canPreview} onClick={() => void run()}>{busy ? <Loader2 className="animate-spin" /> : <FlaskConical />}运行试算</Button></div>
    {!canPreview && <p className="change-section-note">需要提案权限才能试算。</p>}
    {error && <p role="alert" className="text-sm text-destructive">{error}</p>}
    {result && <div role="status" className="extraction-trial">
      {result.errors.length > 0 && <ul className="extraction-errors">{result.errors.map(item => <li key={item}>{item}</li>)}</ul>}
      {(result.trials ?? []).length > 0 && <table className="extraction-table"><thead><tr><th>属性</th><th>原文</th><th>规范化结果</th></tr></thead><tbody>{(result.trials ?? []).map((trial, index) => <tr key={index}><td>{trial.predicate}</td><td>{trial.raw}</td><td>{trial.error ? <span className="text-destructive">{trial.code ? `${trial.code}：` : ""}{trial.error}</span> : <code>{JSON.stringify(trial.normalized)}</code>}{trial.notes?.map(note => <small key={note}>{note}</small>)}</td></tr>)}</tbody></table>}
      {(result.cue_preview ?? []).length > 0 && <div className="extraction-cues">{(result.cue_preview ?? []).map(block => <p key={block.block_id}>{cueSegments(block.text, block.cues).map((segment, index) => segment.cue ? <mark key={index} title={`${cueTypes[segment.cue.type] ?? segment.cue.type}${segment.cue.predicate ? ` · ${segment.cue.predicate}` : segment.cue.concept ? ` · ${segment.cue.concept}` : ""}`} data-cue={segment.cue.type}>{segment.text}</mark> : <span key={index}>{segment.text}</span>)}</p>)}
        <div className="extraction-legend">{Object.entries(cueTypes).map(([type, label]) => <span key={type}><mark data-cue={type}>{label}</mark></span>)}</div></div>}
      {result.instructions && <details className="change-json extraction-instructions" open><summary>Agent 将看到的说明 <span>标准提示词 · {result.instructions.renderer_version} · {result.instructions.content_hash.slice(0, 12)}</span></summary>
        <pre>{conceptId && result.instructions.concepts?.[conceptId] ? [result.instructions.concepts[conceptId].mentions, result.instructions.concepts[conceptId].consolidate, result.instructions.concepts[conceptId].fill_fields].join("\n\n") : result.instructions.markdown}</pre></details>}
      {result.contract && <details className="change-json"><summary>草稿生效后的契约片段</summary><pre>{JSON.stringify(result.contract, null, 2)}</pre></details>}
    </div>}
  </section>;
}

function SubmitBar({ kind, draft, original, revision, canPropose, onSubmitted, onVersion }: { kind: AssetKind; draft: Draft; original: Draft | null; revision: string; canPropose: boolean; onSubmitted: (id: string) => void; onVersion: (version: string) => void }) {
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [errors, setErrors] = useState<string[]>([]), [checked, setChecked] = useState(false);
  const version = assetVersion(kind, draft), previous = original ? assetVersion(kind, original) : "";
  const versionOk = !previous || /^\d+\.\d+\.\d+$/.test(version) && compareVersions(version, previous) > 0;
  async function check() {
    setBusy(true); setErrors([]); setChecked(false);
    try { const preview = await previewAsset(kind, cleanDraft(kind, draft)); setErrors(preview.errors); setChecked(preview.valid); }
    catch (cause) { setErrors([cause instanceof Error ? cause.message : "检查失败"]); }
    finally { setBusy(false); }
  }
  async function submit() {
    setBusy(true); setErrors([]);
    try { const created = await proposeAsset(kind, cleanDraft(kind, draft), reason.trim(), revision); setReason(""); setChecked(false); onSubmitted(created.id); }
    catch (cause) { setErrors([cause instanceof Error ? cause.message : "提交失败"]); }
    finally { setBusy(false); }
  }
  return <section className="extraction-submit" aria-label="提交审核">
    <div className="extraction-submit-fields">
      <Field label="版本" hint={previous ? `当前 ${previous}，须更高` : "新建"}><Input value={version} disabled={busy} aria-invalid={!versionOk} onChange={event => onVersion(event.target.value)} /></Field>
      <Field label="修改原因"><Textarea rows={2} value={reason} disabled={busy} placeholder="说明为什么调整，便于审核" onChange={event => setReason(event.target.value)} /></Field>
    </div>
    {errors.length > 0 && <ul className="extraction-errors" role="alert">{errors.map(item => <li key={item}>{item}</li>)}</ul>}
    {checked && <p role="status" className="extraction-ok"><ShieldCheck size={14} />草稿通过检查，可以提交审核。</p>}
    <div className="change-actions">
      <Button type="button" variant="outline" disabled={busy || !canPropose} onClick={() => void check()}>{busy ? <Loader2 className="animate-spin" /> : <ShieldCheck />}检查草稿</Button>
      <Button type="button" disabled={busy || !canPropose || !reason.trim() || !versionOk} onClick={() => void submit()}><Send />提交审核</Button>
    </div>
    {!canPropose && <p className="change-section-note">当前账户没有提案权限，只能查看。</p>}
  </section>;
}


function FeedbackView({ feedback, assets, loading, error, concept, onConcept, onRefresh, onApply, onOpen }: {
  feedback: ExtractionFeedback | null; assets: ExtractionAssets; loading: boolean; error: string; concept: string; onConcept: (value: string) => void;
  onRefresh: () => void; onApply: (suggestion: Suggestion, choice: string) => void; onOpen: (suggestion: Suggestion) => void;
}) {
  const [choices, setChoices] = useState<Record<string, string>>({});
  const [showClosed, setShowClosed] = useState(false);
  const label = (conceptId: string | null) => assets.concepts.find(item => item.id === conceptId)?.label ?? conceptId ?? "—";
  const suggestions = (feedback?.suggestions ?? []).filter(item => showClosed || item.status === "open");
  return <div className="change-detail-content">
    <div className="change-title"><h2>抽取反馈</h2><p>汇总外部 Agent 已提案的抽取结果：哪些写法没能规范化、哪些线索反复漏抽、哪些事实找不到对应属性，并按配置版本比较效果。建议只会加入说明卡草稿，仍需检查并提交审核。预检不计入统计。</p></div>
    <div className="extraction-feedback-filters">
      <select aria-label="按概念筛选" value={concept} onChange={event => onConcept(event.target.value)}><option value="">全部概念</option>{assets.concepts.filter(item => !item.excluded).map(item => <option key={item.id} value={item.id}>{item.label}</option>)}</select>
      <label className="extraction-checks"><span><input type="checkbox" checked={showClosed} onChange={event => setShowClosed(event.target.checked)} />显示已解决和已失效</span></label>
      <Button variant="outline" size="sm" disabled={loading} onClick={onRefresh}><RefreshCw className={loading ? "animate-spin" : ""} />刷新</Button>
    </div>
    {error && <p role="alert" className="text-sm text-destructive">{error}</p>}
    {!feedback ? <p className="change-section-note">{loading ? "正在汇总…" : ""}</p> : feedback.totals.submissions === 0 ? <div className="extraction-pending"><p>还没有已提案的抽取提交。外部 Agent 以 propose 模式提交后，这里会出现统计与建议。</p></div> : <>
      <div className="extraction-stats" role="status">
        <div><strong>{feedback.totals.submissions}</strong><span>提案提交</span></div>
        <div><strong>{feedback.totals.documents}</strong><span>文档</span></div>
        <div><strong>{feedback.totals.open_suggestions}</strong><span>待处理建议</span></div>
      </div>
      <section className="change-section"><h3>按配置版本比较</h3><p className="change-section-note">每次发布本体或抽取配置都会形成新的配置版本。字段通过率 = 被接受、已存在、冲突待审或转业务系统的字段占比。</p>
        <table className="extraction-table"><thead><tr><th>版本</th><th>时间</th><th>提交</th><th>字段通过率</th><th>规范化失败</th><th>未覆盖线索</th><th>未映射事实</th><th>身份待确认</th></tr></thead>
          <tbody>{feedback.versions.map(version => <tr key={version.registry_fingerprint}><td>{version.label}{version.current ? " · 当前" : ""}<small><code>{version.registry_fingerprint.slice(0, 10)}</code></small></td><td>{version.first_seen.slice(0, 10)}{version.last_seen.slice(0, 10) !== version.first_seen.slice(0, 10) ? ` 至 ${version.last_seen.slice(0, 10)}` : ""}</td><td>{version.submissions}</td><td>{percent(version.field_acceptance_rate)}</td><td>{version.normalization_failures}</td><td>{version.uncovered_cues}</td><td>{version.unmapped_facts}</td><td>{version.needs_resolution}</td></tr>)}</tbody></table>
      </section>
      <section className="change-section"><h3>按概念</h3>
        <table className="extraction-table"><thead><tr><th>概念</th><th>实例（匹配/新建/草稿/待确认/拒绝）</th><th>字段（通过/拒绝）</th><th>常见问题</th></tr></thead>
          <tbody>{feedback.concepts.map(item => {
            const ok = ["accepted", "already_present", "conflict", "upstream_required"].reduce((sum, key) => sum + (item.fields[key] ?? 0), 0);
            const issues = Object.entries(item.issues).sort((a, b) => b[1] - a[1]).slice(0, 3);
            return <tr key={item.concept}><td>{item.label}</td><td>{["matched", "new", "draft", "needs_resolution", "rejected"].map(key => item.instances[key] ?? 0).join(" / ")}</td><td>{ok} / {item.fields.rejected ?? 0}</td><td>{issues.map(([code, count]) => <small key={code}>{code} × {count}</small>)}</td></tr>;
          })}</tbody></table>
      </section>
      <section className="change-section"><h3>改进建议 <span>{suggestions.length} 项</span></h3>
        {suggestions.length === 0 && <p className="change-section-note">暂无待处理建议。</p>}
        <ul className="extraction-suggestions">{suggestions.map(item => {
          const choice = choices[item.id] ?? (item.action?.choices?.length === 1 ? item.action.choices[0] : "");
          const property = item.concept ? assets.concepts.find(value => value.id === item.concept)?.properties : undefined;
          const choiceLabel = (value: string) => item.kind === "property_term" ? `${property?.find(entry => entry.predicate === value)?.label ?? value}（${value}）` : value;
          return <li key={item.id} data-status={item.status}>
            <div className="extraction-suggestion-head"><span className="neutral-badge">{suggestionKinds[item.kind]}</span><strong>{item.text || "（空）"}</strong><span>{label(item.concept)}{item.predicate ? ` · ${item.predicate_label ?? item.predicate}` : ""}</span><span className="extraction-suggestion-count">{item.count} 次 · {item.documents} 份文档 · {suggestionStatus[item.status]}</span></div>
            <p>{item.advice}</p>
            {item.facts && item.facts.length > 0 && <p className="change-section-note">事实：{item.facts.join("；")}</p>}
            {item.samples.length > 0 && <details><summary>原文样例 {item.samples.length} 条</summary><ul className="extraction-samples">{item.samples.map((sample, index) => <li key={index}><span>{sample.document_id} · {sample.document_version}{sample.block_id ? ` · ${sample.block_id}` : ""}</span><p>{sample.text}</p></li>)}</ul></details>}
            {item.status === "open" && <div className="change-actions">
              {applicable(item) && <><select aria-label="选择对应项" value={choice} onChange={event => setChoices(current => ({ ...current, [item.id]: event.target.value }))}><option value="">{item.kind === "property_term" ? "加入哪个属性的叫法" : item.kind === "enum_alias" ? "对应的可选值" : item.kind === "currency_alias" ? "对应的币种" : "对应的注册单位"}</option>{item.action?.choices?.map(value => <option key={value} value={value}>{choiceLabel(value)}</option>)}</select>
                <Button size="sm" disabled={!choice} onClick={() => onApply(item, choice)}><Plus />加入草稿</Button></>}
              {item.concept && <Button size="sm" variant="outline" onClick={() => onOpen(item)}>打开说明卡</Button>}
            </div>}
          </li>;
        })}</ul>
      </section>
    </>}
  </div>;
}

// ---------------------------------------------------------------------------
// Workspace

function initialDraft(selection: Selection, assets: ExtractionAssets): Draft {
  if (selection.kind === "profile") return structuredClone(assets.profile.document ?? object(assets.effective_profile));
  if (selection.kind === "recipe") {
    const file = assets.recipes.find(item => item.id === selection.id);
    return file?.document ? { ...structuredClone(file.document), version: bumpVersion(file.document.version) } : newRecipe(selection.id);
  }
  const concept = assets.concepts.find(item => item.id === selection.id);
  if (!concept) return {};
  return concept.card.document ? { ...structuredClone(concept.card.document), card_version: bumpVersion(concept.card.document.card_version) } : newCard(concept);
}

export default function ExtractionWorkspace({ active }: { active: boolean }) {
  const params = useSearchParams();
  const { session } = useWorkspaceSession();
  const [assets, setAssets] = useState<ExtractionAssets | null>(null);
  const [selection, setSelection] = useState<Selection | null>(null);
  const [drafts, setDrafts] = useState<Record<string, Draft>>({});
  const [editorEpoch, setEditorEpoch] = useState(0);
  const [tab, setTab] = useState<CardTab>("overview");
  const [query, setQuery] = useState("");
  const [loading, setLoading] = useState(false), [error, setError] = useState("");
  const [revision, setRevision] = useState(0);
  const [submitted, setSubmitted] = useState<{ key: string; id: string } | null>(null);
  const [newRecipeId, setNewRecipeId] = useState("");
  const [view, setView] = useState<"assets" | "feedback">("assets");
  const [feedback, setFeedback] = useState<ExtractionFeedback | null>(null);
  const [feedbackConcept, setFeedbackConcept] = useState("");
  const [feedbackLoading, setFeedbackLoading] = useState(false), [feedbackError, setFeedbackError] = useState("");
  const [feedbackRevision, setFeedbackRevision] = useState(0);
  const detailRef = useRef<HTMLElement>(null);
  const permissions = session?.permissions ?? [];
  const canPropose = permissions.includes("propose");

  // Credentials and drafts belong to one principal.
  // eslint-disable-next-line react-hooks/set-state-in-effect
  useEffect(() => { setAssets(null); setDrafts({}); setSelection(null); setSubmitted(null); setFeedback(null); setView("assets"); }, [session?.principal]);
  useEffect(() => {
    if (!active || !session) return;
    const controller = new AbortController();
    // Reset feedback at the start of this abortable request.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setLoading(true); setError("");
    void loadExtractionAssets(controller.signal).then(next => { if (!controller.signal.aborted) setAssets(next); })
      .catch(cause => { if (!controller.signal.aborted) setError(cause instanceof Error ? cause.message : "无法加载抽取配置"); })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [active, session, revision]);
  useEffect(() => {
    if (!active || !session) return;
    const controller = new AbortController();
    // Reset feedback state at the start of this abortable request.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setFeedbackLoading(true); setFeedbackError("");
    void loadFeedback({ concept: feedbackConcept }, controller.signal).then(next => { if (!controller.signal.aborted) setFeedback(next); })
      .catch(cause => { if (!controller.signal.aborted) setFeedbackError(cause instanceof Error ? cause.message : "无法加载抽取反馈"); })
      .finally(() => { if (!controller.signal.aborted) setFeedbackLoading(false); });
    return () => controller.abort();
  }, [active, session, revision, feedbackRevision, feedbackConcept]);
  const conceptParam = params.get("concept") ?? "", predicateParam = params.get("predicate") ?? "", recipeParam = params.get("recipe") ?? "";
  useEffect(() => {
    if (!active || !assets) return;
    const concept = conceptParam ? assets.concepts.find(item => item.id === conceptParam)
      : predicateParam ? assets.concepts.find(item => !item.excluded && item.properties.some(property => property.predicate === predicateParam)) : undefined;
    const target: Selection | null = concept ? { kind: "card", id: concept.id } : recipeParam && assets.recipes.some(item => item.id === recipeParam) ? { kind: "recipe", id: recipeParam } : null;
    if (!target) return;
    // Follow links from the ontology whiteboard and the review queue.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setSelection(target);
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setTab(concept && predicateParam ? "properties" : "overview");
  }, [active, assets, conceptParam, predicateParam, recipeParam]);

  const key = selection ? selectionKey(selection) : "";
  const draft = selection && assets ? drafts[key] ?? initialDraft(selection, assets) : null;
  const concept = selection?.kind === "card" ? assets?.concepts.find(item => item.id === selection.id) : undefined;
  const file = !selection || !assets ? undefined : selection.kind === "profile" ? assets.profile : selection.kind === "recipe" ? assets.recipes.find(item => item.id === selection.id) : concept?.card;
  const sourcePath = selection?.kind === "profile" ? "control/extraction/v2/profile.yaml" : selection?.kind === "recipe" ? `control/extraction/v2/recipes/${selection.id}.yaml` : selection ? `control/extraction/v2/cards/${selection.id}.yaml` : "";
  const pending = pendingFor(assets, sourcePath);
  const dirty = Boolean(key && drafts[key]);
  const change: Update = updater => { if (!selection || !assets) return; setDrafts(current => ({ ...current, [key]: updater(current[key] ?? initialDraft(selection, assets)) })); setSubmitted(null); };
  function select(next: Selection, nextTab: CardTab = "overview") { setView("assets"); setSelection(next); setSubmitted(null); setTab(nextTab); requestAnimationFrame(() => detailRef.current?.focus({ preventScroll: true })); }
  function openFeedback() { setSelection(null); setView("feedback"); setFeedbackRevision(value => value + 1); }
  function suggestionTab(suggestion: Suggestion): CardTab {
    return suggestion.action?.tab === "identity" ? "identity" : suggestion.predicate || suggestion.kind === "property_term" ? "properties" : "overview";
  }
  function applyFeedback(suggestion: Suggestion, choice: string) {
    if (!assets) return;
    const target: Selection = suggestion.kind === "currency_alias" ? { kind: "profile", id: "profile" } : { kind: "card", id: suggestion.concept ?? "" };
    if (target.kind === "card" && !assets.concepts.some(item => item.id === target.id)) return;
    const targetKey = selectionKey(target);
    setDrafts(current => ({ ...current, [targetKey]: applySuggestion(current[targetKey] ?? initialDraft(target, assets), suggestion, choice) }));
    setEditorEpoch(value => value + 1);
    select(target, target.kind === "card" ? suggestionTab(suggestion) : "overview");
    toast.success(`已加入${target.kind === "card" ? "说明卡" : "全局配置"}草稿；检查后提交审核`);
  }
  function openSuggestion(suggestion: Suggestion) {
    if (suggestion.kind === "currency_alias") select({ kind: "profile", id: "profile" });
    else if (suggestion.concept) select({ kind: "card", id: suggestion.concept }, suggestionTab(suggestion));
  }
  function discard() { setDrafts(current => { const next = { ...current }; delete next[key]; return next; }); setEditorEpoch(value => value + 1); }
  function submittedProposal(id: string) {
    setSubmitted({ key, id }); setDrafts(current => { const next = { ...current }; delete next[key]; return next; }); setEditorEpoch(value => value + 1); setRevision(value => value + 1);
    toast.success("已提交审核，等待独立审核人处理");
  }
  const text = query.trim().toLowerCase();
  const concepts = (assets?.concepts ?? []).filter(item => `${item.label} ${item.id} ${Object.values(item.terms.pref).join(" ")}`.toLowerCase().includes(text));
  const recipes = (assets?.recipes ?? []).filter(item => `${item.id} ${strings(object(item.document).document_types).join(" ")}`.toLowerCase().includes(text));
  const draftConceptCount = Object.keys(drafts).length;

  return <div className="changesets-workspace extraction-workspace">
    {!session && <><h1 className="sr-only">抽取配置</h1><div className="workspace-navigation-access"><NavigationToggle /></div></>}
    {!session ? <div className="change-empty"><FileText size={28} strokeWidth={1.3} /><h2>抽取配置</h2><p>从左下角“账户与连接”登录后，维护概念抽取说明卡、属性写法和文档抽取方案。修改通过 ChangeSet 审核后才写入真源。</p></div> : <>
      {error && <div role="alert" className="instance-error"><p>{error}</p><Button variant="outline" size="sm" disabled={loading} onClick={() => setRevision(value => value + 1)}>重试</Button></div>}
      <div className="changesets-layout" data-detail-open={Boolean(selection) || view === "feedback"}>
        <aside className="changeset-directory" aria-label="抽取配置目录">
          <div className="change-list-filters">
            <div className="directory-heading"><NavigationToggle /><h1>抽取配置</h1><Button variant="ghost" size="icon-sm" aria-label="刷新抽取配置" title="刷新抽取配置" disabled={loading} onClick={() => setRevision(value => value + 1)}><RefreshCw className={loading ? "animate-spin" : ""} /></Button></div>
            <div className="instance-search-input"><Search size={16} aria-hidden="true" /><Input aria-label="搜索概念或方案" placeholder="搜索概念、方案…" value={query} onChange={event => setQuery(event.target.value)} /></div>
            <p role="status">{loading ? "正在同步…" : assets ? `${assets.concepts.filter(item => item.card.exists).length} 张说明卡 · ${assets.recipes.length} 个方案${assets.pending.length ? ` · ${assets.pending.length} 项待审核` : ""}${draftConceptCount ? ` · ${draftConceptCount} 份未提交草稿` : ""}` : ""}</p>
            {assets && assets.warnings.length > 0 && <details className="extraction-warnings"><summary><CircleAlert size={14} />{assets.warnings.length} 条配置已随本体变化失效</summary><ul>{assets.warnings.map(item => <li key={item}>{item}</li>)}</ul></details>}
          </div>
          <div className="changeset-list">
            <h2 className="extraction-list-heading">概念抽取说明卡</h2>
            <ul>{concepts.map(item => {
              const itemKey = `card:${item.id}`;
              return <li key={item.id}><button aria-pressed={key === itemKey} onClick={() => select({ kind: "card", id: item.id })}>
                <div><span>{item.id}</span><span>{item.excluded ? "不开放抽取" : item.card.exists ? `v${String(object(item.card.document).card_version ?? "")}` : "未配置"}{drafts[itemKey] ? " · 草稿" : ""}{pendingFor(assets, item.card.source_path).length ? " · 待审核" : ""}</span></div>
                <strong>{item.label}{item.terms.pref.zh ? `（${item.terms.pref.zh}）` : ""}</strong></button></li>;
            })}</ul>
            <h2 className="extraction-list-heading">文档抽取方案</h2>
            <ul>{recipes.map(item => <li key={item.id}><button aria-pressed={key === `recipe:${item.id}`} onClick={() => select({ kind: "recipe", id: item.id })}>
              <div><span>{strings(object(item.document).document_types).join("、")}</span><span>v{String(object(item.document).version ?? "")}{drafts[`recipe:${item.id}`] ? " · 草稿" : ""}{pendingFor(assets, item.source_path).length ? " · 待审核" : ""}</span></div><strong>{item.id}</strong></button></li>)}</ul>
            <form className="extraction-new-recipe" onSubmit={event => { event.preventDefault(); const id = newRecipeId.trim(); if (!isStableId(id) || assets?.recipes.some(item => item.id === id)) return; select({ kind: "recipe", id, isNew: true }); setNewRecipeId(""); }}>
              <Input aria-label="新方案标识" placeholder="新方案标识，如 equipment_ledger" value={newRecipeId} onChange={event => setNewRecipeId(event.target.value)} />
              <Button type="submit" size="sm" variant="outline" disabled={!isStableId(newRecipeId.trim()) || Boolean(assets?.recipes.some(item => item.id === newRecipeId.trim()))}><Plus />新建</Button>
            </form>
            <h2 className="extraction-list-heading">全局</h2>
            <ul><li><button aria-pressed={view === "feedback"} onClick={openFeedback}><div><span><BarChart3 size={12} aria-hidden="true" /> 已提案的抽取</span><span>{feedback ? `${feedback.totals.open_suggestions} 项待处理` : ""}</span></div><strong>抽取反馈与改进建议</strong></button></li>
              <li><button aria-pressed={view === "assets" && key === "profile:profile"} onClick={() => select({ kind: "profile", id: "profile" })}><div><span>profile.yaml</span><span>v{String(object(assets?.profile.document).version ?? "")}{drafts["profile:profile"] ? " · 草稿" : ""}</span></div><strong>验收口径、时区与单位写法</strong></button></li></ul>
          </div>
        </aside>
        {view === "feedback" && assets ? <article className="change-detail outline-none" aria-label="抽取反馈" tabIndex={-1}>
          <div className="change-detail-topline"><span className="detail-navigation-access"><NavigationToggle /></span><Button className="change-back" size="sm" variant="ghost" onClick={() => setView("assets")}>返回目录</Button><span>抽取反馈</span><code>GET /v2/extraction/feedback</code></div>
          <FeedbackView feedback={feedback} assets={assets} loading={feedbackLoading} error={feedbackError} concept={feedbackConcept} onConcept={setFeedbackConcept}
            onRefresh={() => setFeedbackRevision(value => value + 1)} onApply={applyFeedback} onOpen={openSuggestion} />
        </article> :
        selection && assets && draft ? <article ref={detailRef} tabIndex={-1} className="change-detail outline-none" aria-label="抽取配置详情" onKeyDown={event => { if (event.key === "Escape") { event.stopPropagation(); setSelection(null); } }}>
          <div className="change-detail-topline"><span className="detail-navigation-access"><NavigationToggle /></span><Button className="change-back" size="sm" variant="ghost" onClick={() => setSelection(null)}>返回目录</Button><span>{selection.kind === "card" ? "抽取说明卡" : selection.kind === "recipe" ? "文档抽取方案" : "全局配置"}</span><code>{sourcePath}</code></div>
          <div className="change-detail-content">
            <div className="change-title">
              <div className="flex flex-wrap items-center gap-2"><span className="neutral-badge">{file?.exists ? `当前 v${assetVersion(selection.kind, file.document)}` : "尚未创建"}</span>{dirty && <span className="neutral-badge">有未提交修改</span>}{concept?.excluded && <span className="neutral-badge">该概念不开放抽取</span>}</div>
              <h2>{selection.kind === "card" ? concept?.label : selection.kind === "recipe" ? selection.id : "抽取协议 v2 全局配置"}</h2>
              <p>{selection.kind === "card" ? concept?.description || "维护 AI 识别这个概念时使用的说明、叫法、单位写法与身份规则。" : selection.kind === "recipe" ? "声明某类文档中哪些章节、表头或标签对应哪个概念与属性；不绑定具体解析器。" : "契约上限、验收口径、默认时区、新实例 ID 前缀、来源权威等级以及全局单位与币种写法。"}</p>
              {selection.kind === "card" && <span><Link className="logic-link" href="/">在本体白板查看该概念</Link></span>}
            </div>
            {pending.length > 0 && <div className="extraction-pending" role="note">{pending.map(item => <p key={item.id}>该文件有待处理的提案：<Link href={`/changesets?id=${encodeURIComponent(item.id)}`}>{item.title}</Link>（{item.status === "approved" ? "已批准，待发布" : "待审核"}）</p>)}</div>}
            {submitted?.key === key && <div className="extraction-pending" role="status"><p>已提交审核：<Link href={`/changesets?id=${encodeURIComponent(submitted.id)}`}>前往变更审核查看</Link>。批准、应用真源并验证发布后生效。</p></div>}
            <div key={`${key}:${editorEpoch}`}>
              {selection.kind === "card" && concept && <CardEditor concept={concept} draft={draft} onChange={change} assets={assets} disabled={!canPropose} tab={tab} onTab={setTab} />}
              {selection.kind === "card" && concept && tab === "trial" && <TrialPanel kind="card" properties={concept.properties} conceptId={concept.id} draft={draft} canPreview={canPropose} />}
              {selection.kind === "recipe" && <><RecipeEditor draft={draft} onChange={change} assets={assets} disabled={!canPropose} isNew={!file?.exists} /><TrialPanel kind="recipe" properties={[]} draft={draft} canPreview={canPropose} /></>}
              {selection.kind === "profile" && <><ProfileEditor draft={draft} onChange={change} assets={assets} disabled={!canPropose} /><TrialPanel kind="profile" properties={assets.concepts.flatMap(item => item.properties).filter((property, index, all) => all.findIndex(other => other.predicate === property.predicate) === index)} draft={draft} canPreview={canPropose} /></>}
              <details className="change-json"><summary>原始配置（提交内容预览）</summary><pre>{JSON.stringify(cleanDraft(selection.kind, draft), null, 2)}</pre></details>
              {dirty && <div className="change-actions"><Button type="button" variant="ghost" size="sm" onClick={discard}>放弃本项修改</Button></div>}
              <SubmitBar kind={selection.kind} draft={draft} original={file?.exists ? file.document : null} revision={file?.revision ?? ""} canPropose={canPropose}
                onVersion={version => change(current => ({ ...current, [selection.kind === "card" ? "card_version" : "version"]: version }))} onSubmitted={submittedProposal} />
            </div>
          </div>
        </article> : <div className="change-empty"><FileText size={28} strokeWidth={1.3} /><h2>{loading ? "正在读取抽取配置…" : "选择说明卡或方案"}</h2><p>抽取说明卡告诉外部 Agent 如何在文档中识别概念与属性；方案声明文档位置与概念的对应关系。修改先检查、再提交审核，发布后进入抽取契约。</p></div>}
      </div>
    </>}
  </div>;
}

