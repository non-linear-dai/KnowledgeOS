import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
const require = createRequire(import.meta.url), ts = require('typescript');
const source = readFileSync(new URL('../app/templates/decision-editing.ts', import.meta.url), 'utf8');
const compiled = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } }).outputText;
const compiledModule = { exports: {} }; new Function('exports', 'module', compiled)(compiledModule.exports, compiledModule);
const { parseDecisionCell, renderDecisionCell, decisionValue } = compiledModule.exports;

test('decision cells preserve exact decimal thresholds and explicit membership', () => {
  const threshold = parseDecisionCell('>=9007199254740993.01', 'number');
  assert.deepEqual(threshold, { op: 'gte', value: '9007199254740993.01' });
  assert.equal(renderDecisionCell(threshold), '>=9007199254740993.01');
  assert.deepEqual(parseDecisionCell('[4..8]', 'number'), { op: 'between', value: ['4', '8'] });
  assert.deepEqual(parseDecisionCell('A|B', 'string'), { op: 'in', value: ['A', 'B'] });
  assert.equal(parseDecisionCell('*', 'boolean'), null);
  assert.deepEqual(parseDecisionCell('false', 'boolean'), { op: 'eq', value: false });
});
test('decision editing rejects ambiguous types and nonfinite numbers', () => {
  assert.throws(() => parseDecisionCell('>=true', 'boolean'), /数值列/);
  assert.throws(() => parseDecisionCell('[A..Z]', 'string'), /数值列/);
  assert.throws(() => decisionValue('yes', 'boolean'), /true 或 false/);
  assert.throws(() => decisionValue('Infinity', 'number'), /十进制/);
  assert.throws(() => decisionValue('NaN', 'number'), /十进制/);
  assert.equal(decisionValue('  method  ', 'string'), '  method  ');
});
