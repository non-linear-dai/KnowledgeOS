import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname } from 'node:path';
import Module, { createRequire } from 'node:module';

const require = createRequire(import.meta.url);
const ts = require('typescript');
function load(relative, overrides = {}) {
  const file = fileURLToPath(new URL(relative, import.meta.url));
  const mod = new Module(file);
  mod.filename = file;
  mod.paths = Module._nodeModulePaths(dirname(file));
  const original = mod.require.bind(mod);
  mod.require = name => name in overrides ? overrides[name] : original(name);
  mod._compile(ts.transpileModule(readFileSync(file, 'utf8'), { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } }).outputText, file);
  return mod.exports;
}

test('directory pagination encodes literal search and every independent filter', async () => {
  const calls = [];
  const api = load('../app/instances/instances-api.ts', { '../knowledgeos-api': { request: async (path, init) => { calls.push({ path, init }); return { data: { items: [], total: 71 } }; } } });
  const signal = new AbortController().signal;
  const result = await api.loadInstances({ ...api.defaultInstanceFilters, query: '设备 & org:acme_%', domain: 'cost', kind: 'predicate', type: 'equipment', source_class: 'enterprise_operational', lifecycle: 'active', offset: 50, limit: 25 }, signal);
  const params = new URL(calls[0].path, 'https://test.local').searchParams;
  assert.equal(params.get('query'), '设备 & org:acme_%');
  assert.equal(params.get('offset'), '50');
  assert.equal(params.get('type'), 'equipment');
  assert.equal(params.get('kind'), 'predicate');
  assert.equal(params.get('domain'), 'cost');
  assert.equal(params.get('source_class'), 'enterprise_operational');
  assert.equal(params.get('lifecycle'), 'active');
  assert.equal(calls[0].init.signal, signal);
  assert.equal(result.total, 71);
});

test('instance detail retains history provenance and passes cancellation to all reads', async () => {
  const calls = [];
  const source = { source_system: 'erp', locator: 'record:42' };
  const assertion = { id: 'assertion:1', provenance: { source_refs: [source] } };
  const api = load('../app/instances/instances-api.ts', { '../knowledgeos-api': { request: async (path, init) => {
    calls.push({ path, init });
    return { data: path.startsWith('/v1/history') ? { assertions: [assertion] } : path.startsWith('/v1/get') ? { id: '设备:1/2' } : { nodes: [], edges: [] } };
  } } });
  const signal = new AbortController().signal;
  const detail = await api.loadInstanceDetail('设备:1/2', signal);
  assert.equal(detail.card.id, '设备:1/2');
  assert.deepEqual(detail.assertions[0].provenance.source_refs, [source]);
  assert.equal(calls.length, 3);
  assert(calls.every(call => call.init.signal === signal));
  assert(calls.every(call => new URL(call.path, 'https://test.local').searchParams.get('id') === '设备:1/2'));
});

test('detail read failures reject the request instead of rendering a partial success', async () => {
  const api = load('../app/instances/instances-api.ts', { '../knowledgeos-api': { request: async path => {
    if (path.startsWith('/v1/history')) throw new Error('connection lost');
    return { data: {} };
  } } });
  await assert.rejects(api.loadInstanceDetail('org:acme'), /connection lost/);
});

test('display values preserve exact numeric literals and their units', () => {
  const api = load('../app/instances/instances-api.ts', { '../knowledgeos-api': {} });
  assert.equal(api.displayValue({ literal: '0.0000000000000001', unit: 'kW' }), '0.0000000000000001 kW');
  assert.equal(api.displayValue({ literal: false }), 'false');
  assert.equal(api.displayValue({ literal: 0, unit: 'CNY' }), '0 CNY');
  assert.equal(api.displayValue(null), '—');
  assert.equal(api.displayValue({ ref: 'source:erp', locator: 'row:42' }), JSON.stringify({ ref: 'source:erp', locator: 'row:42' }, null, 2));
});

test('workspace links resolve direct routes and exclude unrelated pages', () => {
  const { workspaceForPath } = load('../app/workspace-navigation.ts');
  assert.equal(workspaceForPath('/'), 'ontology');
  assert.equal(workspaceForPath('/instances/'), 'instances');
  assert.equal(workspaceForPath('/templates'), 'templates');
  assert.equal(workspaceForPath('/unknown'), null);
});

test('instance proxy forwards reader identity and query parameters with no write exposure', async () => {
  const route = load('../app/api/knowledgeos/[...path]/route.ts', { 'cloudflare:workers': { env: { KNOWLEDGEOS_API_BASE_URL: 'https://backend.test' } } });
  const original = globalThis.fetch;
  const calls = [];
  globalThis.fetch = async (url, init) => { calls.push({ url: String(url), init }); return Response.json({ data: {} }); };
  try {
    for (const endpoint of ['instances', 'instances/get', 'get', 'history', 'neighbors']) {
      const context = { params: Promise.resolve({ path: ['v1', ...endpoint.split('/')] }) };
      const req = { method: 'GET', headers: new Headers({ authorization: 'Bearer reader-personal' }), nextUrl: new URL(`https://studio.test/api/knowledgeos/v1/${endpoint}?offset=50&limit=25&query=%E8%AE%BE%E5%A4%87`), text: async () => '{}' };
      assert.equal((await route.GET(req, context)).status, 200);
      assert.equal(new URL(calls.at(-1).url).searchParams.get('offset'), '50');
      assert.equal(new URL(calls.at(-1).url).searchParams.get('query'), '设备');
      assert.equal(calls.at(-1).init.headers.Authorization, 'Bearer reader-personal');
      assert.equal((await route.POST({ ...req, method: 'POST' }, context)).status, 404);
      assert.equal((await route.GET({ ...req, headers: new Headers() }, context)).status, 401);
    }
  } finally { globalThis.fetch = original; }
});


test('mixed record detail encodes composite keys without losing provenance or numeric precision', async () => {
  const calls = [];
  const record = { summary: { kind: 'predicate' }, data: { value: { literal: '0.0000000000000001', unit: 'kW' } }, source_refs: [{ ref: 'source:erp', locator: 'row:42' }] };
  const api = load('../app/instances/instances-api.ts', { '../knowledgeos-api': { request: async (path, init) => { calls.push({ path, init }); return { data: record }; } } });
  const key = 'attribute:设备:a/b?c&d';
  const signal = new AbortController().signal;
  assert.deepEqual(await api.loadRecordedInstance(key, signal), record);
  assert.equal(new URL(calls[0].path, 'https://test.local').searchParams.get('key'), key);
  assert.equal(calls[0].init.signal, signal);
  assert.equal(api.defaultInstanceFilters.kind, '');
  assert.deepEqual(Object.keys(api.instanceKindLabels), ['concept', 'relation', 'predicate', 'model']);
});
