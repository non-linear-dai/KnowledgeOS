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
};
export type ValueSample = { predicate: string; raw: string; concept?: string };

export async function loadExtractionAssets(signal?: AbortSignal) {
  return (await request<{ data: ExtractionAssets }>("/v2/extraction/assets", { signal })).data;
}
export async function previewAsset(kind: AssetKind, document: JsonRecord, options: { samples?: ValueSample[]; sampleText?: string } = {}) {
  return (await request<{ data: AssetPreview }>("/v2/extraction/assets/preview", { method: "POST",
    body: JSON.stringify({ kind, document, samples: options.samples ?? [], sample_text: options.sampleText ?? "" }) })).data;
}
export async function proposeAsset(kind: AssetKind, document: JsonRecord, reason: string, baseRevision?: string) {
  return (await request<{ data: { id: string; status: string; target_source: string } }>("/v2/extraction/assets/propose", { method: "POST",
    body: JSON.stringify({ kind, document, reason, base_revision: baseRevision || undefined }) })).data;
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
