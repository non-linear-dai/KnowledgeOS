import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createRequire, Module } from 'node:module';
import { dirname } from 'node:path';
import { fileURLToPath } from 'node:url';

const require = createRequire(import.meta.url), ts = require('typescript');
const file = fileURLToPath(new URL('../app/rules/logic-guides.ts', import.meta.url));
const mod = new Module(file); mod.filename = file; mod.paths = Module._nodeModulePaths(dirname(file));
mod._compile(ts.transpileModule(readFileSync(file, 'utf8'), { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, esModuleInterop: true } }).outputText, file);
const { logicGuides, guideExample } = mod.exports;
const kinds = ['business_rule', 'business_constraint', 'model', 'decision_table', 'decision_model'];
const registry = ['equipment', 'rated_power', 'unit:kW', 'unit:W', 'unit:hour', 'unit:minute', 'unit:one'].map(id => ({ id }));
const assets = [
  { id: 'operation_cycle_time', kind: 'model', lifecycle: 'active', ref: 'model:operation_cycle_time@2.0.0', definition: { inputs: [{id:'baseline_time',unit:'hour'},{id:'workload_factor',unit:'one'}], output_unit:'hour' } },
  { id: 'operation_energy', kind: 'model', lifecycle: 'active', ref: 'model:operation_energy@3.0.0', definition: { inputs: [{id:'effective_power',unit:'kW'},{id:'elapsed_time',unit:'hour'}], output_unit:'kWh' } },
];

test('every business logic kind explains its distinct contract, maintenance and complete examples', () => {
  assert.deepEqual(Object.keys(logicGuides).sort(), [...kinds].sort());
  assert.equal(new Set(Object.values(logicGuides).map(guide => guide.question)).size, 5);
  for (const kind of kinds) {
    const guide = logicGuides[kind], example = guideExample(kind, assets, registry);
    for (const field of ['question','definition','choose','difference','input','output','scenario','result']) assert.ok(guide[field].length > 5, `${kind}.${field}`);
    assert.ok(guide.maintenance.length >= 4); assert.ok(guide.checks.length >= 3); assert.ok(guide.fields.length >= 3);
    assert.deepEqual(example.missing, []); assert.ok(example.definition.test_cases.length >= 2);
    assert.deepEqual(example.input, example.definition.test_cases[0].input);
    assert.deepEqual(example.expected, example.definition.test_cases[0].expected);
  }
});

test('examples report missing registered semantics and reject incompatible graph dependencies', () => {
  assert.ok(guideExample('business_rule', [], []).missing.some(item => item.includes('rated_power')));
  assert.ok(guideExample('model', [], []).missing.some(item => item.includes('hour')));
  const unbound = registry.map(item => item.id === 'equipment' ? {...item, bindings:[{predicateId:'other'}]} : item);
  assert.ok(guideExample('business_constraint', [], unbound).missing.some(item => item.includes('属性绑定')));
  assert.equal(guideExample('decision_model', [], registry).missing.length, 2);
  const incompatible = structuredClone(assets); incompatible[1].definition.inputs[1].unit = 'minute';
  assert.deepEqual(guideExample('decision_model', incompatible, registry).missing, ['已启用且接口匹配的模型 operation_energy']);
  const inactive = structuredClone(assets); inactive[0].lifecycle = 'draft';
  assert.ok(guideExample('decision_model', inactive, registry).missing.some(item => item.includes('operation_cycle_time')));
});

test('graph examples pin available versions while separate drafts retain independent definitions', () => {
  const first = guideExample('decision_model', assets, registry);
  assert.deepEqual(first.definition.nodes.map(node => node.ref), assets.map(asset => asset.ref));
  first.definition.nodes[0].bindings.inputs.path[0] = 'changed';
  first.input.power.literal = '999';
  const second = guideExample('decision_model', assets, registry);
  assert.deepEqual(second.definition.nodes[0].bindings.inputs.path, ['time_inputs']);
  assert.equal(second.input.power.literal, '3');
  assert.equal(second.expected.output.energy.value, '6.0');
});

test('teaching cases distinguish invalid facts, unknown facts and exact size boundaries', () => {
  const constraint = guideExample('business_constraint', [], registry).definition;
  assert.equal(constraint.test_cases.find(item => item.id === 'zero_power').expected.results[0].verdict, 'invalid');
  assert.equal(constraint.test_cases.find(item => item.id === 'missing_power').expected.status, 'incomplete');
  const table = guideExample('decision_table', [], registry).definition;
  assert.equal(table.test_cases.find(item => item.id === 'boundary_part').expected.result.equipment_class, 'small');
  assert.equal(table.test_cases.find(item => item.id === 'large_part').expected.result.equipment_class, 'large');
  assert.equal(table.inputs[0].unit, undefined);
});
