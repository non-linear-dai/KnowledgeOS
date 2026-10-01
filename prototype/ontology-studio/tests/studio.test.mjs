import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';
import Module, { createRequire } from 'node:module';

const require = createRequire(import.meta.url);
const ts = require('typescript');
function load(relative, overrides = {}) {
  const file = fileURLToPath(new URL(relative, import.meta.url));
  const mod = new Module(file);
  mod.filename = file;
  mod.paths = Module._nodeModulePaths(dirname(file));
  const defaultRequire = mod.require.bind(mod);
  mod.require = (name) => {
    if (name in overrides) return overrides[name];
    if (name.startsWith('.') && !name.endsWith('.json')) {
      return load(new URL(`file://${resolve(dirname(file), name)}.ts`).href, overrides);
    }
    return defaultRequire(name);
  };
  const output = ts.transpileModule(readFileSync(file, 'utf8'), { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, esModuleInterop: true } });
  mod._compile(output.outputText, file);
  return mod.exports;
}

const { rebaseDraft } = load('../app/draft-state.ts');
const { reconcileCanvasNodes, selectionAfterChanges, uniqueEdgesById } = load('../app/canvas-state.ts');
const { wireSnapshot, connectionFailure } = load('../app/studio-contract.ts');
const { authorityOptions, initialDefinitions } = load('../app/studio-data.ts');
const { whiteboardDefinitions, whiteboardKinds } = load('../app/whiteboard-scope.ts');
const { dimensionOptions, physicalUnitOptions, currencyOptions, modelUnitOptions, conceptOptions, outputPredicateOptions } = load('../app/registered-options.ts');
const base = { id: 'org', kind: 'concept', label: 'Original', description: 'Before', lifecycle: 'active', sourcePath: 'control/ontology/extension.yaml', config: {}, refs: 0, files: [] };

test('Studio accepts the governed FX market authority', () => {
  assert(authorityOptions.includes('fx_market'));
});

test('whiteboard shows editable ontology and schema cards, excluding fixed registries and operations', () => {
  const definitions = ['schema', 'domain', 'concept', 'relation', 'predicate', 'model', 'business_constraint', 'business_rule', 'unit', 'currency', 'policy', 'connector']
    .map((kind) => ({ ...base, id: kind, kind }));
  assert.deepEqual(whiteboardDefinitions(definitions).map((item) => item.kind), whiteboardKinds);
  assert(!whiteboardKinds.includes('unit'));
  assert(!whiteboardKinds.includes('currency'));
  assert(whiteboardDefinitions(initialDefinitions).every((item) => !item.readOnly));
});

test('registered selectors restrict dimensions, currencies and model outputs to valid definitions', () => {
  const definitions = [
    { ...base, id: 'unit:kW', kind: 'unit', label: 'Kilowatt', config: { id: 'kW', dimension: 'power' } },
    { ...base, id: 'unit:hour', kind: 'unit', label: 'Hour', config: { id: 'hour', dimension: 'duration' } },
    { ...base, id: 'currency:CNY', kind: 'currency', label: 'Chinese yuan', config: { id: 'CNY' } },
    { ...base, id: 'equipment', kind: 'concept', label: 'Equipment', bindings: [{ predicateId: 'rated_power' }, { predicateId: 'equipment_name' }] },
    { ...base, id: 'rated_power', kind: 'predicate', label: 'Rated power', config: { storage_mode: 'assertion' } },
    { ...base, id: 'equipment_name', kind: 'predicate', label: 'Name', config: { storage_mode: 'attr' } },
  ];
  assert.deepEqual(dimensionOptions(definitions).map((item) => item.value), ['duration', 'power']);
  assert.deepEqual(physicalUnitOptions(definitions, 'power').map((item) => item.value), ['kW']);
  assert.deepEqual(currencyOptions(definitions).map((item) => item.value), ['CNY']);
  assert(modelUnitOptions(definitions).some((item) => item.value === 'currency_per_hour'));
  assert(modelUnitOptions(definitions).some((item) => item.value === 'kW'));
  assert.equal(modelUnitOptions([...definitions, { ...base, id: 'unit:one', kind: 'unit', label: 'Ratio', config: { id: 'one', dimension: 'dimensionless' } }]).filter((item) => item.value === 'one').length, 1);
  assert(!modelUnitOptions(definitions).some((item) => item.value === 'CNY'));
  assert.deepEqual(conceptOptions(definitions).map((item) => item.value), ['equipment']);
  assert.deepEqual(outputPredicateOptions(definitions, 'equipment').map((item) => item.value), ['rated_power']);
  assert.deepEqual(outputPredicateOptions(definitions, 'missing'), []);
});

test('canvas keeps measured nodes and dragged positions while definitions update', () => {
  const current = [{ id: 'concept:organization', position: { x: 420, y: 260 }, measured: { width: 248, height: 148 }, selected: true, data: { label: 'Old' } }];
  const projected = [{ id: 'concept:organization', position: { x: 80, y: 60 }, selected: false, data: { label: 'New' } }];
  assert.deepEqual(reconcileCanvasNodes(current, projected)[0].position, { x: 420, y: 260 });
  assert.deepEqual(reconcileCanvasNodes(current, projected)[0].measured, { width: 248, height: 148 });
  assert.equal(reconcileCanvasNodes(current, projected)[0].data.label, 'New');
  assert.deepEqual(reconcileCanvasNodes(current, projected, true)[0].position, { x: 80, y: 60 });
});

test('marquee and modifier selections track all selected ontology cards', () => {
  assert.deepEqual(selectionAfterChanges(['a'], [{ type: 'select', id: 'a', selected: false }, { type: 'select', id: 'b', selected: true }, { type: 'select', id: 'c', selected: true }]), ['b', 'c']);
  assert.deepEqual(selectionAfterChanges(['a', 'b'], [{ type: 'select', id: 'b', selected: false }]), ['a']);
});

test('shared business scope and predicate inputs create one visible edge per id', () => {
  const edges = [
    { id: 'rule-business_scope-equipment', source: 'rule', target: 'equipment', role: 'subject' },
    { id: 'rule-business_scope-equipment', source: 'rule', target: 'equipment', role: 'candidate' },
    { id: 'rule-business_input-rated_power', source: 'rule', target: 'rated_power', role: 'subject' },
    { id: 'rule-business_input-rated_power', source: 'rule', target: 'rated_power', role: 'candidate' },
  ];
  assert.deepEqual(uniqueEdgesById(edges).map((edge) => edge.id), [
    'rule-business_scope-equipment', 'rule-business_input-rated_power',
  ]);
});

test('rebase preserves local edits while accepting independent server changes', () => {
  const local = { ...base, label: 'Local' };
  const remote = { ...base, description: 'Server' };
  const result = rebaseDraft([remote], [{ targetId: base.id, before: base, after: local, type: 'update' }]);
  assert.equal(result.definitions[0].label, 'Local');
  assert.equal(result.definitions[0].description, 'Server');
  assert.deepEqual(result.operations[0].before, remote);
});

test('same-field and deletion conflicts preserve inputs and reject rebase', () => {
  const operations = [{ targetId: base.id, before: base, after: { ...base, label: 'Local' } }];
  assert.throws(() => rebaseDraft([{ ...base, label: 'Remote' }], operations), /冲突/);
  assert.throws(() => rebaseDraft([], operations), /冲突/);
  assert.equal(operations[0].after.label, 'Local');
});

test('connection failures never enter demo mode', () => {
  assert.equal(connectionFailure(401), 'unauthorized');
  assert.equal(connectionFailure(403), 'forbidden');
  assert.equal(connectionFailure(503, 'BACKEND_NOT_CONFIGURED'), 'unconfigured');
  assert.equal(connectionFailure(502), 'offline');
});

test('wire contract rejects silently lost definitions and missing baseline', () => {
  const data = { contract_version: '3.4', registry_fingerprint: 'hash', definitions: [], changesets: [], coverage: {}, extensions: {}, capabilities: {} };
  assert(wireSnapshot.safeParse({ data }).success);
  assert(!wireSnapshot.safeParse({ data: { ...data, registry_fingerprint: undefined } }).success);
  assert(!wireSnapshot.safeParse({ data: { ...data, definitions: [{ ...base, kind: 'invented' }] } }).success);
  assert(wireSnapshot.safeParse({ data: { ...data, definitions: ['business_constraint', 'business_rule'].map((kind) => ({ ...base, kind, source_path: `control/${kind}.yaml` })) } }).success);
});

test('Studio proxy never substitutes a service token and rejects cross-site writes', async () => {
  const route = load('../app/api/knowledgeos/[...path]/route.ts', { 'cloudflare:workers': { env: { KNOWLEDGEOS_API_BASE_URL: 'https://backend.test', KNOWLEDGEOS_API_TOKEN: 'unused-admin-secret' } } });
  const context = { params: Promise.resolve({ path: ['v1', 'studio'] }) };
  const make = (headers = {}, method = 'GET') => ({ method, headers: new Headers(headers), nextUrl: new URL('https://studio.test/api/knowledgeos/v1/studio'), text: async () => '{}' });
  assert.equal((await route.GET(make(), context)).status, 401);
  const originalFetch = globalThis.fetch;
  let authorization;
  let redirect;
  globalThis.fetch = async (_url, init) => { authorization = init.headers.Authorization; redirect = init.redirect; return Response.json({ data: {} }); };
  try {
    assert.equal((await route.GET(make({ authorization: 'Bearer reader-personal' }), context)).status, 200);
    assert.equal(authorization, 'Bearer reader-personal');
    assert.equal(redirect, 'manual');
    globalThis.fetch = async () => new Response(null, { status: 302, headers: { Location: 'https://other.test/' } });
    assert.equal((await route.GET(make({ authorization: 'Bearer reader-personal' }), context)).status, 502);
    const writeContext = { params: Promise.resolve({ path: ['v1', 'propose'] }) };
    assert.equal((await route.POST(make({ authorization: 'Bearer user', origin: 'https://other.test', 'content-type': 'application/json' }, 'POST'), writeContext)).status, 403);
    globalThis.fetch = async (url, init) => { authorization = init.headers.Authorization; return Response.json({ data: { results: [] }, path: String(url) }); };
    for (const endpoint of ['preview', 'impact', 'evaluate']) {
      const businessContext = { params: Promise.resolve({ path: ['v1', 'business', endpoint] }) };
      assert.equal((await route.POST(make({ authorization: 'Bearer author', origin: 'https://studio.test', 'content-type': 'application/json' }, 'POST'), businessContext)).status, 200);
      assert.equal(authorization, 'Bearer author');
    }
    const extractionContext = { params: Promise.resolve({ path: ['v1', 'extraction', 'request'] }) };
    assert.equal((await route.POST(make({ authorization: 'Bearer agent', origin: 'https://studio.test', 'content-type': 'application/json' }, 'POST'), extractionContext)).status, 404);
  } finally { globalThis.fetch = originalFetch; }
});

test('Studio product surface excludes the knowledge extraction workspace', () => {
  const page = readFileSync(fileURLToPath(new URL('../app/page.tsx', import.meta.url)), 'utf8');
  const control = readFileSync(fileURLToPath(new URL('../app/control-center.tsx', import.meta.url)), 'utf8');
  assert(!page.includes('ExtractionCenter'));
  assert(!page.includes('知识抽取'));
  assert(!control.includes('onOpenExtraction'));
});

test('live metadata takes priority over demo content and personal credentials stay in request headers', async () => {
  const api = load('../app/knowledgeos-api.ts');
  const originalFetch = globalThis.fetch;
  const headers = [];
  globalThis.fetch = async (_url, init) => {
    headers.push(init.headers.Authorization);
    return Response.json({ data: { contract_version: '3.4', registry_fingerprint: 'current',
      definitions: [{ ...base, id: 'organization', source_path: base.sourcePath, label: 'Live backend label' }],
      changesets: [], coverage: {}, extensions: {}, capabilities: {} } });
  };
  try {
    api.setSessionToken('personal');
    assert.equal((await api.loadStudioSnapshot()).definitions[0].label, 'Live backend label');
    assert.equal(headers[0], 'Bearer personal');
    api.setSessionToken('');
    await api.loadStudioSnapshot();
    assert.equal(headers[1], undefined);
  } finally { globalThis.fetch = originalFetch; }
});
