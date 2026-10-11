import type { OntologyDefinition } from './studio-data';
import type { JsonObject, OntologyRequest, Vocabulary, VocabularyTerm } from './semantic-api';

export function draftIdentifier(label: string): string {
  const latin = label.trim().toLowerCase().replace(/[^a-z0-9]+/g, '_').replace(/^_|_$/g, '').slice(0, 48);
  if (/^[a-z]/.test(latin)) return latin;
  let hash = 2166136261;
  for (const code of label.trim()) hash = Math.imul(hash ^ code.codePointAt(0)!, 16777619) >>> 0;
  return `concept_${hash.toString(16)}`;
}
export function conceptOutput(request: OntologyRequest, label: string, description: string, id: string, predicateIds: string[], definitions: OntologyDefinition[], material: string): JsonObject {
  if (!/^[a-z][a-z0-9_-]{0,63}$/.test(id) || !label.trim() || !description.trim()) throw new Error('请填写对象名称和说明，并使用有效的标识');
  if (definitions.some(d => d.kind === 'concept' && d.id === id)) throw new Error('该对象已存在，请从白板选择并修改');
  if (predicateIds.some(p => !definitions.some(d => d.kind === 'predicate' && d.id === p))) throw new Error('所选属性已变化，请刷新');
  const after = { id, kind: 'concept', label: label.trim(), description: description.trim(), lifecycle: 'draft', sourcePath: 'control/ontology/core.yaml', config: {}, refs: 0, files: ['control/ontology/core.yaml'], bindings: [...new Set(predicateIds)].map(predicateId => ({ predicateId, required: false, cardinality: 'inherit', group: 'other' })) };
  return { protocol_version: 'knowledgeos.ontology-assist.v1', request_id: request.request_id, operations: [{ operation: { type: 'create', targetKind: 'concept', targetId: id, after }, evidence: [{ segment_id: 'material:1', quote: material.trim() }] }] };
}
export function updateVocabulary(vocabulary: Vocabulary, term: VocabularyTerm): Vocabulary {
  if (!term.id || !term.definition.trim() || !Object.values(term.pref_labels).some(label => label.trim())) throw new Error('请填写术语名称和定义');
  const parts = vocabulary.version.split('.').map(Number);
  return { ...vocabulary, version: `${parts[0]}.${parts[1]}.${parts[2] + 1}`, terms: [...vocabulary.terms.filter(t => t.id !== term.id), term] };
}
export function downloadText(name: string, content: string, mime = 'text/plain;charset=utf-8') {
  const url = URL.createObjectURL(new Blob([content], { type: mime }));
  const anchor = document.createElement('a'); anchor.href = url; anchor.download = name; anchor.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
