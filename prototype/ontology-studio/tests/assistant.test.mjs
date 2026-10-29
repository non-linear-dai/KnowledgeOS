import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createRequire, Module } from 'node:module';
import { dirname } from 'node:path';
import { fileURLToPath } from 'node:url';

const require = createRequire(import.meta.url), ts = require('typescript');
function load(relative, overrides = {}) {
  const file = fileURLToPath(new URL(relative, import.meta.url));
  const mod = new Module(file); mod.filename = file; mod.paths = Module._nodeModulePaths(dirname(file));
  const original = mod.require.bind(mod); mod.require = name => name in overrides ? overrides[name] : original(name);
  mod._compile(ts.transpileModule(readFileSync(file, 'utf8'), { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } }).outputText, file);
  return mod.exports;
}
class ApiError extends Error { constructor(message, status, code) { super(message); this.status = status; this.code = code; } }
const calls = [];
const api = load('../app/assistant/assistant-api.ts', { '../knowledgeos-api': {
  ApiError, sessionAuthorization: () => ({ Authorization: 'Bearer t' }),
  request: async (path, init) => { calls.push({ path, init }); return { data: { valid: true } }; },
} });

const assistance = { suggestion_id: 's', task: 'logic.draft', provider: 'p', model: 'm', renderer_version: '1.0.0', instructions_hash: 'a'.repeat(64), context_hash: 'b'.repeat(64), output_hash: 'c'.repeat(64), created_at: 'now', items_total: 4, repair_rounds: 0 };

function suggestion() {
  return {
    assistance, output: { kind: 'decision_table', summary: 'x', test_cases: [] },
    check: { submitted: { id: 'size', version: '1.0.0', rules: [{ id: 'small' }, { id: 'large' }], test_cases: [{ id: 'existing', input: {}, expected: { result: {} } }] },
      analysis: {
        intent_tests: [
          { id: 'existing', label: '与已有案例同名', input: { facts: { size: '100' } }, expected: { result: { m: 'small' } }, passed: true },
          { id: 'wrong', label: '预期不一致', input: { facts: { size: '700' } }, expected: { result: { m: 'large' } }, passed: false },
        ],
        coverage: { boundary_cases: [
          { id: 'boundary_1', label: '恰好 300', rule: 'small', input: { facts: { size: '300' } }, actual: { result: { m: 'small' } } },
          { id: 'boundary_2', label: '无规则', rule: 'small', input: { facts: { size: '900' } }, error: 'no matching rule' },
        ] },
      } },
  };
}

test('adopting keeps confirmed cases only, deduplicates ids and counts accepted assistant items', () => {
  const adopted = api.adoptLogic(suggestion(), ['existing', 'wrong'], ['boundary_1', 'boundary_2']);
  assert.deepEqual(adopted.definition.test_cases.map(item => item.id), ['existing', 'existing_2', 'boundary_1']);
  assert.deepEqual(adopted.definition.test_cases[2].expected, { result: { m: 'small' } });
  assert.match(adopted.definition.test_cases[2].label, /^专家确认/);
  assert.equal(adopted.assistance.items_accepted, 3); // two rules + one passing intent case
  assert.equal(adopted.kind, 'decision_table');
  const none = api.adoptLogic(suggestion(), [], []);
  assert.deepEqual(none.definition.test_cases.map(item => item.id), ['existing']);
  assert.throws(() => api.adoptLogic({ ...suggestion(), check: { submitted: null, analysis: {} } }, [], []), /未通过结构校验/);
});

test('accepted counts are clamped to what the assistant proposed', () => {
  assert.equal(api.withAccepted(assistance, 99).items_accepted, 4);
  assert.equal(api.withAccepted(assistance, -1).items_accepted, 0);
});

test('issues sort by severity and regions read as business text', () => {
  assert.deepEqual(api.sortIssues([{ severity: 'info', code: 'i', message: '' }, { severity: 'error', code: 'e', message: '' }, { severity: 'warning', code: 'w', message: '' }]).map(item => item.code), ['e', 'w', 'i']);
  assert.equal(api.regionText({ size: '> 600', material: '任意' }, { size: '尺寸' }), '尺寸 > 600，material 为任意值');
  assert.equal(api.percent(0.4567), '45.7%'); assert.equal(api.percent(null), '—');
});

test('interview helpers normalize model templates and record assistant turns', () => {
  const template = api.normalizeTemplate({ id: 'r', steps: [{ id: 'a' }], groups: [{ id: 'g' }] });
  assert.deepEqual(template.edges, []); assert.deepEqual(template.cases, []); assert.equal(template.summary, '');
  assert.equal(template.steps[0].parent, null); assert.equal(template.groups[0].join_policy, 'all');
  const turns = api.nextTurns([{ role: 'expert', content: '先蚀刻' }], { reply: '明白', questions: [{ id: 'q', question: '按什么计价？' }] });
  assert.deepEqual(turns[1], { role: 'assistant', content: '明白\n1. 按什么计价？' });
});

test('card check counts suggestions the draft resolves', () => {
  assert.deepEqual(api.resolvedCount({ analysis: { resolves: [{ id: 'x' }], open_before: 3 } }), { resolved: 1, open: 3 });
  assert.deepEqual(api.resolvedCount({ analysis: {} }), { resolved: 0, open: 0 });
});

test('assistant requests go through the Studio proxy with the personal token and readable errors', async () => {
  const original = globalThis.fetch;
  const seen = [];
  globalThis.fetch = async (url, init) => { seen.push({ url, init }); return { ok: false, status: 503, json: async () => ({ error: 'raw', code: 'ASSISTANT_GATEWAY_NOT_CONFIGURED' }) }; };
  try {
    await assert.rejects(api.requestSuggestion('logic.draft', { description: 'x' }), error => error.code === 'ASSISTANT_GATEWAY_NOT_CONFIGURED' && /尚未配置/.test(error.message));
    assert.equal(seen[0].url, '/api/assistant/v1/assistant/suggest');
    assert.equal(seen[0].init.headers.Authorization, 'Bearer t');
    assert.deepEqual(JSON.parse(seen[0].init.body), { task: 'logic.draft', input: { description: 'x' } });
  } finally { globalThis.fetch = original; }
  await api.checkDraft('logic.draft', { kind: 'decision_table' }, { description: 'd' });
  assert.equal(calls.at(-1).path, '/v1/authoring/check');
  assert.deepEqual(JSON.parse(calls.at(-1).init.body), { task: 'logic.draft', draft: { kind: 'decision_table' }, input: { description: 'd' } });
});

test('Studio proxies expose authoring and assistant routes only', () => {
  const kernel = readFileSync(new URL('../app/api/knowledgeos/[...path]/route.ts', import.meta.url), 'utf8');
  for (const route of ['/v1/authoring/context', '/v1/authoring/check', '/v1/authoring/feedback']) assert.ok(kernel.includes(`"${route}"`), route);
  const assistant = readFileSync(new URL('../app/api/assistant/[...path]/route.ts', import.meta.url), 'utf8');
  assert.match(assistant, /GET: new Set\(\["\/v1\/assistant\/status"\]\)/);
  assert.match(assistant, /POST: new Set\(\["\/v1\/assistant\/suggest"\]\)/);
  assert.match(assistant, /KNOWLEDGEOS_ASSISTANT_BASE_URL/);
});
