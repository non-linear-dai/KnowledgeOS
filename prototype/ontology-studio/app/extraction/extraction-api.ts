import { request } from "../knowledgeos-api";

/** Editing view of control/extraction/v2 served by GET /v2/extraction/assets. */
export type AssetKind = "profile" | "card" | "recipe";
export type JsonRecord = Record<string, unknown>;
export type AssetFile = { kind: AssetKind; id: string; source_path: string; exists: boolean; revision: string; document: JsonRecord | null };
export type PropertyInfo = {
  predicate: string; label: string; description: string; type: string; cardinality?: string | null; dimension?: string | null;
  units: string[]; default_unit?: string | null; allowed: string[]; storage: string; required: boolean;
  publication_route: "governed_changeset" | "upstream_source_system";
};
export type ConceptInfo = {
  id: string; label: string; description: string; terms: { pref: Record<string, string>; alt: string[] }; excluded: boolean;
  properties: PropertyInfo[]; card: AssetFile;
};
export type PendingChange = { id: string; status: string; target_source: string; title: string; actor: string; created_at: string };
export type ExtractionAssets = {
  registry_fingerprint: string; profile: AssetFile; effective_profile: JsonRecord; concepts: ConceptInfo[]; recipes: AssetFile[];
  relations: { id: string; label: string; endpoints: { source_type: string; target_type: string }[] }[];
  units: { id: string; label: string; dimension: string }[]; currencies: string[]; warnings: string[]; pending: PendingChange[];
};
export type Cue = { type: string; match: string; concept?: string; predicate?: string; node_id?: string; start: number; end: number };
export type AssetPreview = {
  kind: AssetKind; valid: boolean; errors: string[]; warnings: string[]; source_path?: string; revision?: string; exists?: boolean;
  contract?: JsonRecord; trials?: { predicate: string; raw: string; normalized?: JsonRecord; notes?: string[]; error?: string; code?: string }[];
  cue_preview?: { block_id: string; text: string; cues: Cue[] }[];
  instructions?: Instructions;
};
export type Instructions = {
  format: string; renderer_version: string; language: string; content_hash: string; markdown: string;
  sections: { id: string; stage: string; title: string; markdown: string }[];
  concepts?: Record<string, { mentions: string; consolidate: string; fill_fields: string }>;
};
export type SuggestionKind = "unit_alias" | "currency_alias" | "enum_alias" | "value_format" | "property_term" | "identity_rule" | "uncovered_cue" | "unmapped_fact";
export type Suggestion = {
  id: string; kind: SuggestionKind; concept: string | null; concept_label: string | null; predicate: string | null; predicate_label: string | null;
  text: string; count: number; documents: number; last_seen: string; status: "open" | "resolved" | "obsolete"; advice: string;
  action: { target: "card" | "profile"; field: string; choices?: string[]; tab?: string } | null;
  samples: { document_id: string; document_version: string; submission_id: string; block_id: string | null; text: string }[];
  raw_values?: string[]; reasons?: string[]; facts?: string[]; cue_types?: string[];
};
export type FeedbackVersion = {
  registry_fingerprint: string; label: string; current: boolean; first_seen: string; last_seen: string; submissions: number; documents: number;
  fields: number; fields_ok: number; field_acceptance_rate: number | null; normalization_failures: number; uncovered_cues: number; unmapped_facts: number; needs_resolution: number;
};
export type FeedbackConcept = {
  concept: string; label: string; submissions: number; instances: Record<string, number>; fields: Record<string, number>; issues: Record<string, number>;
};
export type ExtractionFeedback = {
  registry_fingerprint: string; totals: { submissions: number; documents: number; open_suggestions: number };
  concepts: FeedbackConcept[]; versions: FeedbackVersion[]; suggestions: Suggestion[];
};
export type ValueSample = { predicate: string; raw: string; concept?: string };

export async function loadExtractionAssets(signal?: AbortSignal) {
  return (await request<{ data: ExtractionAssets }>("/v2/extraction/assets", { signal })).data;
}
export async function previewAsset(kind: AssetKind, document: JsonRecord, options: { samples?: ValueSample[]; sampleText?: string } = {}) {
  return (await request<{ data: AssetPreview }>("/v2/extraction/assets/preview", { method: "POST",
    body: JSON.stringify({ kind, document, samples: options.samples ?? [], sample_text: options.sampleText ?? "" }) })).data;
}
export async function loadFeedback(options: { concept?: string; since?: string } = {}, signal?: AbortSignal) {
  const query = new URLSearchParams(Object.entries(options).filter(([, value]) => value) as [string, string][]).toString();
  return (await request<{ data: ExtractionFeedback }>(`/v2/extraction/feedback${query ? `?${query}` : ""}`, { signal })).data;
}
export async function proposeAsset(kind: AssetKind, document: JsonRecord, reason: string, baseRevision?: string, assistance?: Record<string, unknown>) {
  return (await request<{ data: { id: string; status: string; target_source: string } }>("/v2/extraction/assets/propose", { method: "POST",
    body: JSON.stringify({ kind, document, reason, base_revision: baseRevision || undefined, ...(assistance ? { assistance } : {}) }) })).data;
}

// ---------------------------------------------------------------------------
// Pure helpers shared by the editor and tests.

export const routeLabels = { governed_changeset: "经审核写入真源", upstream_source_system: "业务系统维护" } as const;
export const selectorKinds = { heading: "章节标题", section_path: "章节路径", table_header: "表头或行标签", key_value_label: "键值标签", speaker: "发言人" } as const;
export const mappingRoles = { instance_label: "实例名称", instance_key: "实例编码", field: "属性取值", relation: "关系" } as const;
export const cueTypes: Record<string, string> = { term: "术语", value_pattern: "值模式", unit: "单位", lexicon: "已有实例", recipe: "抽取方案" };

export function object(value: unknown): JsonRecord {
  return value && typeof value === "object" && !Array.isArray(value) ? value as JsonRecord : {};
}
export function strings(value: unknown): string[] {
  return Array.isArray(value) ? value.filter((item): item is string => typeof item === "string") : [];
}
/** One entry per non-empty line, trimmed and de-duplicated in order. */
export function lines(text: string): string[] {
  return Array.from(new Set(text.split(/\r?\n/).map(item => item.trim()).filter(Boolean)));
}
export function joinLines(value: unknown): string { return strings(value).join("\n"); }

export function pairs(value: unknown): [string, string][] {
  return Object.entries(object(value)).map(([key, item]) => [key, typeof item === "string" ? item : String(item ?? "")]);
}
/** Rows → mapping; blank rows are ignored and the last duplicate key wins. */
export function mapping(rows: [string, string][]): Record<string, string> {
  return Object.fromEntries(rows.map(([key, value]) => [key.trim(), value.trim()] as [string, string]).filter(([key, value]) => key && value));
}

/** Higher patch version: 1.2.3 -> 1.2.4; invalid input falls back to 1.0.0. */
export function bumpVersion(value: unknown): string {
  const match = /^(\d+)\.(\d+)\.(\d+)$/.exec(String(value ?? ""));
  return match ? `${match[1]}.${match[2]}.${Number(match[3]) + 1}` : "1.0.0";
}
export function compareVersions(left: string, right: string): number {
  const parse = (value: string) => value.split(".").map(part => Number(part));
  const [a, b] = [parse(left), parse(right)];
  for (let index = 0; index < 3; index++) if ((a[index] ?? 0) !== (b[index] ?? 0)) return (a[index] ?? 0) - (b[index] ?? 0);
  return 0;
}

export function assetVersion(kind: AssetKind, document: JsonRecord | null | undefined): string {
  return String(object(document)[kind === "card" ? "card_version" : "version"] ?? "");
}

/** Remove empty strings, arrays and objects so drafts serialize to tidy YAML. */
export function compact<T>(value: T): T {
  if (Array.isArray(value)) return value.map(item => compact(item)).filter(item => !isEmpty(item)) as T;
  if (value && typeof value === "object") {
    return Object.fromEntries(Object.entries(value as JsonRecord).map(([key, item]) => [key, compact(item)] as [string, unknown]).filter(([, item]) => !isEmpty(item))) as T;
  }
  return typeof value === "string" ? value.trim() as T : value;
}
function isEmpty(value: unknown) {
  return value === undefined || value === null || value === "" || Array.isArray(value) && value.length === 0 ||
    typeof value === "object" && !Array.isArray(value) && Object.keys(value as JsonRecord).length === 0;
}

export function newCard(concept: ConceptInfo): JsonRecord {
  return { format: "knowledgeos.extraction-card.v1", concept: concept.id, card_version: "1.0.0",
    card: { definition: { zh: concept.terms.pref.zh ?? "", en: concept.description || concept.label } },
    terms: { alt: [] }, identity: { keys: [{ source: "natural_key" }], minimum_fields: [] }, properties: {} };
}
export function newRecipe(id: string): JsonRecord {
  return { format: "knowledgeos.extraction-recipe.v1", id, version: "1.0.0", document_types: [id], match: {}, mappings: [] };
}
/** Draft ready for review: keeps required keys even when empty, tidies the rest. */
export function cleanDraft(kind: AssetKind, draft: JsonRecord): JsonRecord {
  const tidy = compact(draft);
  if (kind === "card") return { ...tidy, properties: object(tidy.properties) };
  if (kind === "recipe") return { ...tidy, document_types: strings(draft.document_types), mappings: Array.isArray(tidy.mappings) ? tidy.mappings : [] };
  return { ...tidy, excluded_concepts: strings(draft.excluded_concepts) };
}

export function isStableId(value: string) { return /^[a-z][a-z0-9_]*$/.test(value); }

/** Highlight segments for a block. Offsets are Unicode code points (as on the server); overlaps keep the first, longest cue. */
export function cueSegments(text: string, cues: Cue[]): { text: string; cue?: Cue }[] {
  const chars = Array.from(text);
  const ordered = [...cues].filter(cue => cue.end > cue.start).sort((a, b) => a.start - b.start || b.end - a.end);
  const result: { text: string; cue?: Cue }[] = [];
  let cursor = 0;
  for (const cue of ordered) {
    if (cue.start < cursor || cue.end > chars.length) continue;
    if (cue.start > cursor) result.push({ text: chars.slice(cursor, cue.start).join("") });
    result.push({ text: chars.slice(cue.start, cue.end).join(""), cue });
    cursor = cue.end;
  }
  if (cursor < chars.length) result.push({ text: chars.slice(cursor).join("") });
  return result;
}

export function pendingFor(assets: ExtractionAssets | null, path: string) {
  return (assets?.pending ?? []).filter(change => change.target_source === path);
}

export const suggestionKinds: Record<SuggestionKind, string> = {
  unit_alias: "单位写法", currency_alias: "币种写法", enum_alias: "枚举写法", value_format: "取值格式", property_term: "属性叫法",
  identity_rule: "身份规则", uncovered_cue: "未覆盖线索", unmapped_fact: "未映射事实",
};
export const suggestionStatus = { open: "待处理", resolved: "已解决", obsolete: "已失效" } as const;

/** Whether a suggestion can be written into a draft directly (it needs a chosen target such as a unit, value or predicate). */
export function applicable(suggestion: Suggestion) {
  return suggestion.status === "open" && Boolean(suggestion.action?.choices?.length) &&
    ["unit_alias", "currency_alias", "enum_alias", "property_term"].includes(suggestion.kind);
}

/**
 * Apply a suggestion to a card or profile draft. Only edits the draft; publishing still needs review.
 * unit_alias/enum_alias: choice is the registered unit / allowed value. property_term: choice is the predicate.
 * currency_alias: applies to the profile draft; choice is the currency code.
 */
export function applySuggestion(draft: JsonRecord, suggestion: Suggestion, choice: string): JsonRecord {
  const text = suggestion.text.trim();
  if (!text || !choice) return draft;
  if (suggestion.kind === "currency_alias") return { ...draft, currency_aliases: { ...object(draft.currency_aliases), [text]: choice } };
  const properties = object(draft.properties);
  if (suggestion.kind === "property_term") {
    const entry = object(properties[choice]), terms = object(entry.terms);
    const alt = strings(terms.alt);
    return { ...draft, properties: { ...properties, [choice]: { ...entry, terms: { ...terms, alt: alt.includes(text) ? alt : [...alt, text] } } } };
  }
  if (!suggestion.predicate) return draft;
  const field = suggestion.kind === "unit_alias" ? "unit_aliases" : suggestion.kind === "enum_alias" ? "enum_map" : "";
  if (!field) return draft;
  const entry = object(properties[suggestion.predicate]);
  return { ...draft, properties: { ...properties, [suggestion.predicate]: { ...entry, [field]: { ...object(entry[field]), [text]: choice } } } };
}

export function percent(value: number | null | undefined) {
  return value === null || value === undefined ? "—" : `${Math.round(value * 1000) / 10}%`;
}
