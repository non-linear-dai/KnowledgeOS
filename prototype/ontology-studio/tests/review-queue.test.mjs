import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createRequire, Module } from 'node:module';
import { dirname } from 'node:path';
import { fileURLToPath } from 'node:url';

const require = createRequire(import.meta.url);
const ts = require('typescript');
function load(relative) {
  const file = fileURLToPath(new URL(relative, import.meta.url));
  const mod = new Module(file); mod.filename = file; mod.paths = Module._nodeModulePaths(dirname(file));
  mod._compile(ts.transpileModule(readFileSync(file, 'utf8'), { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } }).outputText, file);
  return mod.exports;
}
const source = relative => readFileSync(new URL(relative, import.meta.url), 'utf8');
const queue = load('../app/changesets/review-queue.ts');
const diff = load('../app/changesets/change-diff.ts');

const change = (id, extra) => ({ id, title: id, reason: '', actor: 'agent:a', status: 'review_required', risk: 'normal', target_source: 'control/ontology/core.yaml', created_at: '2026-11-01T00:00:00Z', operations: [], patch: {}, ...extra });
const records = [
  change('normal-old', { governance: { allowed: true, reason: null, self_review: false }, created_at: '2026-10-01T00:00:00Z' }),
  change('high-new', { risk: 'high', governance: { allowed: true, reason: null, self_review: false }, created_at: '2026-11-02T00:00:00Z' }),
  change('needs-other', { risk: 'high', actor: 'user:ricky', governance: { allowed: false, reason: '需要另一独立责任方', self_review: false } }),
  change('published-mine', { status: 'published', actor: 'agent:a', actor_info: { kind: 'agent', label: 'A', owner: 'user:ricky', accountable: 'user:ricky', host: null, model: null }, created_at: '2026-09-01T00:00:00Z' }),
  change('approved-other', { status: 'approved', actor: 'user:someone' }),
];

test('"待我处理" lists what the reviewer may approve, highest risk first, then oldest first', () => {
  assert.deepEqual(queue.queueFor('mine', records, 'user:ricky').map(item => item.id), ['high-new', 'normal-old']);
});

test('"需他人批准" holds pending proposals the reviewer may not approve', () => {
  assert.deepEqual(queue.queueFor('others', records, 'user:ricky').map(item => item.id), ['needs-other']);
});

test('"我提交的" includes the reviewer\'s own proposals and those of agents they are accountable for', () => {
  assert.deepEqual(queue.queueFor('submitted', records, 'user:ricky').map(item => item.id), ['needs-other', 'published-mine']);
  assert.deepEqual(queue.queueFor('submitted', records, undefined), []);
});

test('"全部" keeps every record, newest first, and counts follow each view', () => {
  assert.equal(queue.queueFor('all', records, 'user:ricky')[0].id, 'high-new');
  assert.deepEqual(queue.viewCounts(records, 'user:ricky'), { mine: 2, others: 1, submitted: 2, all: 5 });
});

test('after a decision the next item in the same list opens, falling back to the previous one', () => {
  const list = [{ id: 'a' }, { id: 'b' }, { id: 'c' }];
  assert.equal(queue.nextAfter('a', list), 'b');
  assert.equal(queue.nextAfter('c', list), 'b');
  assert.equal(queue.nextAfter('a', [{ id: 'a' }]), '');
  assert.equal(queue.nextAfter('missing', list), 'a');
});

test('only known views are accepted from links', () => {
  assert.equal(queue.parseReviewView('mine'), 'mine');
  assert.equal(queue.parseReviewView('inbox'), null);
  assert.equal(queue.parseReviewView(null), null);
});

test('a definition operation reads as field rows, with empty fields left out of a creation', () => {
  const rows = diff.operationRows({ type: 'create', targetKind: 'predicate', targetId: 'component_level', after: { id: 'component_level', label: '部件层级', description: '', config: { value_type: 'enum', allowed: ['part', 'material'], minimum: null } } });
  assert.deepEqual(rows.map(row => [row.field, row.after]), [['标识', 'component_level'], ['名称', '部件层级'], ['config · value_type', 'enum'], ['config · allowed', 'part、material']]);
  assert.deepEqual(diff.operationTarget({ type: 'create', targetKind: 'predicate', targetId: 'component_level', after: { label: '部件层级' } }), { id: 'component_level', label: '部件层级', kind: '判断类型', type: '新建' });
});

test('an update lists changed fields first and keeps unchanged ones for context', () => {
  const rows = diff.operationRows({ type: 'update', before: { id: 'x', label: 'Old', lifecycle: 'active' }, after: { id: 'x', label: 'New', lifecycle: 'active' } });
  assert.deepEqual(rows[0], { field: '名称', before: 'Old', after: 'New', changed: true });
  assert.equal(rows.filter(row => row.changed).length, 1);
});

test('knowledge patches become location, operation and value rows', () => {
  assert.deepEqual(diff.patchRows([{ op: 'replace', path: '/knowledge/attrs/summary', value: '定位为中端伺服模块' }]), [{ op: '替换', path: '/knowledge/attrs/summary', value: '定位为中端伺服模块' }]);
  assert.deepEqual(diff.patchRows({ ops: [{ op: 'add', path: '/a', value: 2 }] }), [{ op: '添加', path: '/a', value: '2' }]);
  assert.deepEqual(diff.patchRows({ op: 'studio_batch' }), []);
});

test('the review workspace keeps the decision in view and replaces the separate inbox', () => {
  const page = source('../app/changesets/changesets-workspace.tsx');
  assert.match(page, /className="change-decision-bar" aria-label="审核决定"/);
  assert.match(page, /nextAfter\(selectedId, visible\)/);
  assert.match(page, /<ReviewSettings \/>/);
  assert(!source('../app/workspace-shell.tsx').includes('InboxWorkspace'));
  assert(source('../app/workspace-shell.tsx').includes('workspace.id === "changesets" && canReview'));
  assert(!source('../app/agents/agents-workspace.tsx').includes('updatePolicy'), 'the approval policy is changed in review settings');
});
