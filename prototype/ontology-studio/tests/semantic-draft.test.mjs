import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createRequire, Module } from 'node:module';
import { fileURLToPath } from 'node:url';
const require = createRequire(import.meta.url); const ts = require('typescript');
function load(file, overrides = {}) {
  const path = fileURLToPath(new URL(file, import.meta.url)); const mod = new Module(path); mod.filename = path;
  mod.require = name => name in overrides ? overrides[name] : require(name);
  mod._compile(ts.transpileModule(readFileSync(path, 'utf8'), { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } }).outputText, path);
  return mod.exports;
}
const { draftIdentifier, conceptOutput, updateVocabulary } = load('../app/semantic-draft.ts');
const packet = { request_id: 'ontology-request:1', request_hash: 'h', registry_fingerprint: 'fp' };
const definitions = [{ id: 'summary', kind: 'predicate', lifecycle: 'active' }];
test('business labels produce stable identifiers without requiring English input', () => {
  for (const label of ['检验报告', 'Order Report', '123 工单']) {
    assert.match(draftIdentifier(label), /^[a-z][a-z0-9_-]{0,63}$/);
    assert.equal(draftIdentifier(label), draftIdentifier(label));
  }
  assert.notEqual(draftIdentifier('检验报告'), draftIdentifier('质量问题'));
});
test('assisted draft cites material and binds registered attributes without changing the envelope', () => {
  const output = conceptOutput(packet, '检验报告', '记录检验结论', 'inspection_report', ['summary', 'summary'], definitions, '记录检验结论');
  const change = output.operations[0]; assert.equal(change.operation.after.lifecycle, 'draft');
  assert.deepEqual(change.operation.after.bindings.map(b => b.predicateId), ['summary']);
  assert.deepEqual(change.evidence, [{ segment_id: 'material:1', quote: '记录检验结论' }]);
  assert.equal(output.request_id, packet.request_id); assert(!('base' in change.operation.after));
  assert.throws(() => conceptOutput(packet, '对象', '说明', 'inspection_report', ['fake'], definitions, '材料'), /所选属性/);
  assert.throws(() => conceptOutput(packet, '对象', '说明', '../bad', [], definitions, '材料'), /有效的标识/);
  assert.throws(() => conceptOutput(packet, '对象', '说明', 'existing', [], [{ id: 'existing', kind: 'concept' }], '材料'), /已存在/);
});
test('terminology revision preserves multilingual labels and other terms', () => {
  const term = { id: 'org', pref_labels: { zh: '企业', en: 'Organization' }, definition: '组织身份', concept_ref: 'organization' };
  const scheme = { id: 'core', version: '1.0.9', terms: [term, { id: 'task', pref_labels: { zh: '任务' }, definition: '工作单元' }] };
  const next = updateVocabulary(scheme, { ...term, alt_labels: { zh: ['公司'] } });
  assert.equal(next.version, '1.0.10'); assert.equal(next.terms.find(t => t.id === 'org').pref_labels.en, 'Organization');
  assert.equal(next.terms.length, 2); assert.equal(scheme.version, '1.0.9'); assert(!scheme.terms[0].alt_labels);
  assert.throws(() => updateVocabulary(scheme, { ...term, definition: '' }), /术语名称和定义/);
});
test('ontology and batch review clients pass explicit preview identities and use server actor identity', async () => {
  const calls = []; const api = load('../app/semantic-api.ts', { './knowledgeos-api': { request: async (path, init) => { calls.push({ path, ...JSON.parse(init.body) }); return { data: {} }; } } });
  await api.ontologyPropose(packet, { operations: [] }, '依据', 'key');
  await api.submitBatchReview(['one', 'two'], 'approved', 'preview-version', '已审核');
  assert.equal(calls[0].path, '/v1/ontology/propose'); assert.equal(calls[0].idempotency_key, 'key'); assert(!('actor' in calls[0]));
  assert.equal(calls[1].preview_hash, 'preview-version'); assert.equal(calls[1].note, '已审核');
});
