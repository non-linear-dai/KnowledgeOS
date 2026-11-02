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
const navigation = load('../app/workspace-navigation.ts');

test('navigation groups follow the work: author, look up, then govern', () => {
  const { navigationGroups, workspaces } = navigation;
  assert.deepEqual(navigationGroups.map(group => group.label), ['建模', '查阅', '治理']);
  const members = id => workspaces.filter(item => item.group === id).map(item => item.id);
  assert.deepEqual(members('model'), ['ontology', 'rules', 'templates', 'extraction']);
  assert.deepEqual(members('browse'), ['instances', 'overview']);
  assert.deepEqual(members('govern'), ['changesets', 'agents']);
  assert(workspaces.every(item => navigationGroups.some(group => group.id === item.group)), 'every workspace sits in a group');
  const order = workspaces.map(item => navigationGroups.findIndex(group => group.id === item.group));
  assert.deepEqual(order, [...order].sort((a, b) => a - b), 'workspaces are listed group by group');
});

test('the review badge shows only a positive count and caps long numbers', () => {
  const { reviewBadge } = navigation;
  assert.equal(reviewBadge(null), '');
  assert.equal(reviewBadge(undefined), '');
  assert.equal(reviewBadge(0), '');
  assert.equal(reviewBadge(4), '4');
  assert.equal(reviewBadge(120), '99+');
});

test('review actions announce a change so the navigation count refreshes', () => {
  const shell = source('../app/workspace-shell.tsx');
  assert.match(shell, /addEventListener\(reviewChangedEvent, load\)/);
  assert.match(shell, /"\/v1\/inbox"/);
  assert(source('../app/changesets/changesets-workspace.tsx').match(/announceReviewChange\(\)/g).length >= 2, 'decisions and batch review both announce');
});

test('signing in happens only in the shared account dialog', () => {
  for (const file of ['../app/ontology-workspace.tsx', '../app/instances/instances-workspace.tsx', '../app/templates/template-workspace.tsx']) {
    const page = source(file);
    assert(!page.includes('个人访问凭证'), `${file} must not render its own credential form`);
    assert(!/setSessionToken\(token(Input)?\)/.test(page), `${file} must not sign in by itself`);
  }
  assert(source('../app/account-dialog.tsx').includes('个人访问凭证'), 'access tokens remain available in the account dialog');
  for (const file of ['../app/instances/instances-workspace.tsx', '../app/templates/template-workspace.tsx', '../app/extraction/extraction-workspace.tsx', '../app/changesets/changesets-workspace.tsx', '../app/agents/agents-workspace.tsx']) {
    assert(source(file).includes('<SignInPrompt'), `${file} offers the shared sign-in prompt`);
  }
  assert(source('../app/ontology-workspace.tsx').includes('onClick={() => setAccountOpen(true)}>登录</Button>'));
  assert(source('../app/rules/rules-workspace.tsx').includes('<SignInButton />'));
});

test('the whiteboard keeps one add action and one combined view control', () => {
  const page = source('../app/ontology-workspace.tsx');
  assert(!page.includes('aria-label="新增本体定义"'), 'the catalog no longer repeats the add button');
  assert(page.includes('aria-label="新增定义"'));
  assert(!page.includes('aria-label="选择工作领域"'), 'work domain moved into the view control');
  assert(page.includes('aria-label="视图与筛选"'));
  assert(!page.includes('domainOpen'));
});

test('icon-only workspace tools name themselves on hover, with shortcuts where they exist', () => {
  const page = source('../app/ontology-workspace.tsx');
  for (const [label, shortcut] of [['平移与选择', 'V'], ['框选', 'B'], ['新增定义', 'N'], ['搜索白板', '/'], ['定义目录', '[']]) {
    assert(page.includes(`<ToolHint label="${label}" shortcut="${shortcut}">`), `${label} shows its shortcut`);
  }
  const dock = page.slice(page.indexOf('aria-label="本体白板工具"'), page.indexOf('{workspaceMenu}', page.indexOf('aria-label="本体白板工具"')));
  assert(!/<Button[^>]* title=/.test(dock), 'whiteboard tools use hover hints instead of title attributes');
  const template = source('../app/templates/template-workspace.tsx');
  assert(template.includes('<ToolHint label="工艺目录" shortcut="⌘K">'));
  assert(template.includes('<ToolHint label="路线模板">'));
});

test('templates open on the template list after signing in unless a template link is followed', () => {
  const page = source('../app/templates/template-workspace.tsx');
  assert.match(page, /if \(!params\.get\("template"\)\) setPanel\("templates"\);/);
});

test('in-workspace navigation toggles appear only on compact screens', () => {
  const css = source('../app/globals.css');
  assert.match(css, /@media \(min-width: 901px\) \{ \.workspace-surfaces \.workspace-navigation-toggle \{ display: none; \} \}/);
});
