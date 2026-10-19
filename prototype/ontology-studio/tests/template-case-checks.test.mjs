import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
const require = createRequire(import.meta.url), ts = require('typescript');
const source = readFileSync(new URL('../app/templates/template-case-checks.ts', import.meta.url), 'utf8');
const compiled = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } }).outputText;
const compiledModule = { exports: {} }; new Function('exports', 'module', compiled)(compiledModule.exports, compiledModule);
const { checkTemplateCases, templateCasesPass } = compiledModule.exports;
const sample = (id, expected_operations) => ({ id, facts: {}, sets: {}, expected_operations });

test('a new basic case exposes its stale zero expectation without rewriting the expert assertion', async () => {
  const template = { steps: [{ id: 'step_1' }], cases: [sample('basic', 0)] };
  const before = structuredClone(template);
  const preview = async (route, current) => {
    assert.equal(route, template);
    return { operation_count: 1, matches_expected: current.expected_operations === 1 };
  };
  const checks = await checkTemplateCases(template, preview);
  assert.deepEqual(checks, [{ id: 'basic', expected: 0, actual: 1, passed: false }]);
  assert.equal(templateCasesPass(checks), false);
  assert.deepEqual(template, before);
  // Only the user's explicit confirmation changes the expectation.
  template.cases[0].expected_operations = 1;
  assert.equal(templateCasesPass(await checkTemplateCases(template, preview)), true);
});

test('every case uses the backend expansion, including repeats and conditional skips', async () => {
  const template = { steps: [{}, {}], cases: [sample('repeat', 7), sample('optional', 0), sample('regression', 3)] };
  const visited = [];
  const checks = await checkTemplateCases(template, async (_, current) => {
    visited.push(current.id);
    const actual = { repeat: 7, optional: 0, regression: 4 }[current.id];
    return { operation_count: actual, matches_expected: actual === current.expected_operations };
  });
  assert.deepEqual(visited, ['repeat', 'optional', 'regression']);
  assert.deepEqual(checks.map(check => [check.actual, check.passed]), [[7, true], [0, true], [4, false]]);
  assert.equal(templateCasesPass(checks), false);
});

test('a failed preview cannot pass or replace an expectation and other cases are still checked', async () => {
  const checks = await checkTemplateCases({ cases: [sample('broken', 2), sample('valid', 1)] }, async (_, current) => {
    if (current.id === 'broken') throw new Error('missing iteration set: items');
    return { operation_count: 1, matches_expected: true };
  });
  assert.deepEqual(checks[0], { id: 'broken', expected: 2, actual: null, passed: false, error: 'missing iteration set: items' });
  assert.equal(checks[1].passed, true);
  assert.equal(templateCasesPass(checks), false);
  assert.equal(templateCasesPass([]), false);
});

test('closing or editing a draft stops obsolete case checks', async () => {
  const controller = new AbortController();
  let calls = 0;
  await assert.rejects(checkTemplateCases({ cases: [sample('old', 1), sample('next', 1)] }, async () => {
    calls++;
    controller.abort();
    return { operation_count: 1, matches_expected: true };
  }, controller.signal), { name: 'AbortError' });
  assert.equal(calls, 1);
});
