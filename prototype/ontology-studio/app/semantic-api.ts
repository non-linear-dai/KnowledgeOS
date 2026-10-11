import { request } from './knowledgeos-api';

export type JsonObject = Record<string, unknown>;
export type VocabularyTerm = { id: string; pref_labels: Record<string, string>; alt_labels?: Record<string, string[]>; definition: string; concept_ref?: string; broader?: string[]; scope_note?: string; close_matches?: string[] };
export type Vocabulary = { format: string; id: string; label: string; version: string; terms: VocabularyTerm[] };
export type SemanticSummary = { application_version: string; registry_fingerprint: string; vocabularies: Record<string, Vocabulary>; capabilities: { validation: string } };
export type Impact = { compatible: boolean; issues: { message: string; scope: string }[]; candidate_counts: { entities: number }; changed_sources: { path: string; action: string }[] };
export type OntologyRequest = JsonObject & { request_id: string; request_hash: string; registry_fingerprint: string };
export type OntologyPreview = { operations: JsonObject[]; target_source: string; impact: Impact };
export type ReviewDecision = 'approved' | 'rejected' | 'changes_requested';
export type BatchPreview = { preview_hash: string; can_review: boolean; items: { id: string; title: string; actor: string; risk: string; blockers: string[] }[]; groups: { key: string; ids: string[] }[] };

const post = <T,>(path: string, body: unknown) => request<{ data: T }>(path, { method: 'POST', body: JSON.stringify(body) });
export const loadSemantics = () => request<{ data: SemanticSummary }>('/v1/semantics');
export const ontologyRequest = (goal: string, material: string) => post<OntologyRequest>('/v1/ontology/request', { goal, material });
export const ontologyPreview = (request: OntologyRequest, model_output: JsonObject) => post<OntologyPreview>('/v1/ontology/preview', { request, model_output });
export const ontologyPropose = (request: OntologyRequest, model_output: JsonObject, reason: string, idempotency_key: string) => post<{ id: string }>('/v1/ontology/propose', { request, model_output, reason, idempotency_key });
export const vocabularyPropose = (vocabulary: Vocabulary, base_revision: string, reason: string, idempotency_key: string) => post<{ id: string }>('/v1/semantics/vocabulary/propose', { vocabulary, base_revision, reason, idempotency_key });
export const evaluateSemantics = () => request<{ data: { conforms: boolean; passed: number; total: number; cases: { id: string; question: string; passed: boolean }[] } }>('/v1/semantics/evaluate');
export const exportSemantics = (format: 'turtle' | 'json-ld') => request<{ data: { content: string; triples: number } }>(`/v1/semantics/export?format=${format}`);
export const previewBatchReview = (ids: string[], decision: ReviewDecision) => post<BatchPreview>('/v1/changesets/review-batch/preview', { ids, decision });
export const submitBatchReview = (ids: string[], decision: ReviewDecision, preview_hash: string, note: string) => post('/v1/changesets/review-batch', { ids, decision, preview_hash, note });
export const semanticImpact = (target_source: string, operations: unknown[]) => post<Impact>('/v1/semantics/impact', { target_source, operations, patch: { op: 'studio_batch' } });
