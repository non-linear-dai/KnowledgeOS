import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createRequire, Module } from 'node:module';
import { dirname } from 'node:path';
import { fileURLToPath } from 'node:url';
const require = createRequire(import.meta.url);
const ts = require('typescript');
function load(relative, overrides = {}) {
  const file = fileURLToPath(new URL(relative, import.meta.url));
  const mod = new Module(file); mod.filename = file; mod.paths = Module._nodeModulePaths(dirname(file));
  const original = mod.require.bind(mod); mod.require = name => name in overrides ? overrides[name] : original(name);
  mod._compile(ts.transpileModule(readFileSync(file, 'utf8'), { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } }).outputText, file);
  return mod.exports;
}
const api = load('../app/extraction/extraction-api.ts', { '../knowledgeos-api': {} });

test('line and mapping editors keep authored order, trim and drop blanks', () => {
  assert.deepEqual(api.lines(' 测试台\n\n机台\r\n测试台 \n'), ['测试台', '机台']);
  assert.deepEqual(api.mapping([[' 千瓦 ', 'kW'], ['', 'W'], ['瓦', ''], ['千瓦', 'kW']]), { 千瓦: 'kW' });
  assert.deepEqual(api.pairs({ 天: 'calendar_days', x: 3 }), [['天', 'calendar_days'], ['x', '3']]);
});

test('drafts bump versions and compact empty values without losing required keys', () => {
  assert.equal(api.bumpVersion('1.2.9'), '1.2.10'); assert.equal(api.bumpVersion('bad'), '1.0.0');
  assert(api.compareVersions('1.10.0', '1.9.9') > 0); assert.equal(api.compareVersions('1.0.0', '1.0.0'), 0);
  const card = api.cleanDraft('card', { format: 'f', concept: 'equipment', card_version: '1.0.1', card: { definition: { zh: ' 设备 ', en: '' }, recognition_cues: [] },
    terms: { alt: [] }, identity: { keys: [{ source: 'natural_key', namespace: '' }], minimum_fields: [] }, properties: { rated_power: { terms: { alt: [] }, unit_aliases: {} } } });
  assert.deepEqual(card, { format: 'f', concept: 'equipment', card_version: '1.0.1', card: { definition: { zh: '设备' } }, identity: { keys: [{ source: 'natural_key' }] }, properties: {} });
  const recipe = api.cleanDraft('recipe', api.newRecipe('equipment_ledger'));
  assert.deepEqual(recipe.mappings, []); assert.deepEqual(recipe.document_types, ['equipment_ledger']);
  assert.deepEqual(api.cleanDraft('profile', { version: '1.0.1', excluded_concepts: [] }).excluded_concepts, []);
  assert.equal(api.assetVersion('card', card), '1.0.1'); assert.equal(api.assetVersion('recipe', recipe), '1.0.0');
});

test('new cards start from the concept terminology and stable identifiers are enforced', () => {
  const card = api.newCard({ id: 'equipment', label: 'Equipment', description: 'Capital equipment', terms: { pref: { zh: '设备' }, alt: [] }, properties: [], excluded: false, card: {} });
  assert.equal(card.concept, 'equipment'); assert.deepEqual(card.card.definition, { zh: '设备', en: 'Capital equipment' });
  assert(api.isStableId('equipment_ledger')); assert(!api.isStableId('Equipment-Ledger')); assert(!api.isStableId('1ledger'));
});

test('cue highlights use code point offsets and skip overlaps', () => {
  const text = '𠀀测试台额定功率 5kW';
  const segments = api.cueSegments(text, [{ type: 'term', match: '测试台', start: 1, end: 4 }, { type: 'term', match: '额定功率', start: 4, end: 8 }, { type: 'term', match: '功率', start: 6, end: 8 }, { type: 'unit', match: '5kW', start: 9, end: 12 }]);
  assert.deepEqual(segments.map(item => [item.text, item.cue?.type ?? null]), [['𠀀', null], ['测试台', 'term'], ['额定功率', 'term'], [' ', null], ['5kW', 'unit']]);
});

test('asset requests target the governed extraction routes', async () => {
  const calls = [];
  const client = load('../app/extraction/extraction-api.ts', { '../knowledgeos-api': { request: async (path, init) => { calls.push({ path, init }); return { data: { id: 'cs-1', valid: true } }; } } });
  await client.loadExtractionAssets(); await client.previewAsset('card', { concept: 'equipment' }, { sampleText: '额定功率', samples: [{ predicate: 'rated_power', raw: '5kW' }] });
  await client.proposeAsset('recipe', { id: 'r' }, '新增方案', 'sha256:abc');
  assert.deepEqual(calls.map(call => call.path), ['/v2/extraction/assets', '/v2/extraction/assets/preview', '/v2/extraction/assets/propose']);
  assert.deepEqual(JSON.parse(calls[1].init.body), { kind: 'card', document: { concept: 'equipment' }, samples: [{ predicate: 'rated_power', raw: '5kW' }], sample_text: '额定功率' });
  assert.deepEqual(JSON.parse(calls[2].init.body), { kind: 'recipe', document: { id: 'r' }, reason: '新增方案', base_revision: 'sha256:abc' });
});

test('extraction asset changes are categorized and applicable to source', () => {
  const changes = load('../app/changesets/changesets-api.ts', { '../knowledgeos-api': {} });
  const change = { id: 'x', target_source: 'control/extraction/v2/cards/equipment.yaml', status: 'approved', actor: 'a', risk: 'high', operations: [], patch: { op: 'extraction_asset', kind: 'card' } };
  assert.equal(changes.changeCategory(change), '抽取配置'); assert.equal(changes.supportsSourceApplication(change), true);
  assert.equal(changes.supportsSourceApplication({ ...change, target_source: 'control/extraction/v2/cards/../profile.yaml' }), false);
  assert.equal(changes.supportsSourceApplication({ ...change, target_source: 'control/extraction/default.yaml' }), false);
});

test('studio proxy exposes only the extraction authoring routes', async () => {
  const route = load('../app/api/knowledgeos/[...path]/route.ts', { 'cloudflare:workers': { env: { KNOWLEDGEOS_API_BASE_URL: 'https://backend.test' } } });
  const original = globalThis.fetch; const calls = [];
  globalThis.fetch = async (url, init) => { calls.push({ url: String(url), init }); return Response.json({ data: {} }); };
  const call = (method, path) => route[method]({ method, headers: new Headers({ authorization: 'Bearer author', 'content-type': 'application/json' }), nextUrl: new URL(`https://studio.test/api/knowledgeos${path}`), text: async () => '{}' },
    { params: Promise.resolve({ path: path.slice(1).split('/') }) });
  try {
    assert.equal((await call('GET', '/v2/extraction/assets')).status, 200);
    assert.equal((await call('POST', '/v2/extraction/assets/preview')).status, 200);
    assert.equal((await call('POST', '/v2/extraction/assets/propose')).status, 200);
    for (const path of ['/v2/extraction/sources', '/v2/extraction/submissions', '/v2/extraction/contract']) assert.equal((await call('POST', path)).status, 404, path);
    assert.equal(calls.length, 3);
  } finally { globalThis.fetch = original; }
});

test('feedback suggestions apply only to drafts and keep existing card content', () => {
  const draft = { concept: 'equipment', properties: { rated_power: { terms: { alt: ['功率'] }, unit_aliases: { 千瓦: 'kW' } } } };
  const unit = { id: 'u', kind: 'unit_alias', concept: 'equipment', predicate: 'rated_power', text: ' 千瓦特 ', status: 'open', action: { target: 'card', field: 'unit_aliases', choices: ['W', 'kW'] } };
  assert.deepEqual(api.applySuggestion(draft, unit, 'kW').properties.rated_power, { terms: { alt: ['功率'] }, unit_aliases: { 千瓦: 'kW', 千瓦特: 'kW' } });
  assert.equal(draft.properties.rated_power.unit_aliases['千瓦特'], undefined, 'the original draft is not mutated');
  const term = { ...unit, kind: 'property_term', predicate: null, text: '电机功率', action: { target: 'card', field: 'terms', choices: ['rated_power'] } };
  assert.deepEqual(api.applySuggestion(draft, term, 'rated_power').properties.rated_power.terms.alt, ['功率', '电机功率']);
  assert.deepEqual(api.applySuggestion(api.applySuggestion(draft, term, 'rated_power'), term, 'rated_power').properties.rated_power.terms.alt, ['功率', '电机功率']);
  const enumAlias = { ...unit, kind: 'enum_alias', predicate: 'risk_level', text: '爆表' };
  assert.deepEqual(api.applySuggestion({}, enumAlias, 'critical').properties.risk_level, { enum_map: { 爆表: 'critical' } });
  const currency = { ...unit, kind: 'currency_alias', text: '块钱' };
  assert.deepEqual(api.applySuggestion({ currency_aliases: { 元: 'CNY' } }, currency, 'CNY').currency_aliases, { 元: 'CNY', 块钱: 'CNY' });
  assert.equal(api.applySuggestion(draft, unit, ''), draft);
  assert.equal(api.applicable(unit), true); assert.equal(api.applicable({ ...unit, status: 'resolved' }), false);
  assert.equal(api.applicable({ ...unit, kind: 'uncovered_cue' }), false);
  assert.equal(api.percent(0.8333), '83.3%'); assert.equal(api.percent(null), '—');
});

test('feedback loader passes filters and the proxy exposes it read-only', async () => {
  const calls = [];
  const client = load('../app/extraction/extraction-api.ts', { '../knowledgeos-api': { request: async path => { calls.push(path); return { data: {} }; } } });
  await client.loadFeedback(); await client.loadFeedback({ concept: 'equipment', since: '' });
  assert.deepEqual(calls, ['/v2/extraction/feedback', '/v2/extraction/feedback?concept=equipment']);
  const route = load('../app/api/knowledgeos/[...path]/route.ts', { 'cloudflare:workers': { env: { KNOWLEDGEOS_API_BASE_URL: 'https://backend.test' } } });
  const original = globalThis.fetch; globalThis.fetch = async () => Response.json({ data: {} });
  const request = method => route[method]({ method, headers: new Headers({ authorization: 'Bearer reader', 'content-type': 'application/json' }), nextUrl: new URL('https://studio.test/api/knowledgeos/v2/extraction/feedback'), text: async () => '{}' },
    { params: Promise.resolve({ path: ['v2', 'extraction', 'feedback'] }) });
  try { assert.equal((await request('GET')).status, 200); assert.equal((await request('POST')).status, 404); } finally { globalThis.fetch = original; }
});
