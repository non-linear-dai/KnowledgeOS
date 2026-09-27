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
const { wireSnapshot, connectionFailure } = load('../app/studio-contract.ts');
const base = { id: 'org', kind: 'concept', label: 'Original', description: 'Before', lifecycle: 'active', sourcePath: 'control/ontology/extension.yaml', config: {}, refs: 0, files: [] };

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
});

test('Studio proxy never substitutes a service token and rejects cross-site writes', async () => {
  const route = load('../app/api/knowledgeos/[...path]/route.ts', { 'cloudflare:workers': { env: { KNOWLEDGEOS_API_BASE_URL: 'https://backend.test', KNOWLEDGEOS_API_TOKEN: 'unused-admin-secret' } } });
  const context = { params: Promise.resolve({ path: ['v1', 'studio'] }) };
  const make = (headers = {}, method = 'GET') => ({ method, headers: new Headers(headers), nextUrl: new URL('https://studio.test/api/knowledgeos/v1/studio'), text: async () => '{}' });
  assert.equal((await route.GET(make(), context)).status, 401);
  const originalFetch = globalThis.fetch;
  let authorization;
  globalThis.fetch = async (_url, init) => { authorization = init.headers.Authorization; return Response.json({ data: {} }); };
  try {
    assert.equal((await route.GET(make({ authorization: 'Bearer reader-personal' }), context)).status, 200);
    assert.equal(authorization, 'Bearer reader-personal');
    const writeContext = { params: Promise.resolve({ path: ['v1', 'propose'] }) };
    assert.equal((await route.POST(make({ authorization: 'Bearer user', origin: 'https://other.test', 'content-type': 'application/json' }, 'POST'), writeContext)).status, 403);
  } finally { globalThis.fetch = originalFetch; }
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
