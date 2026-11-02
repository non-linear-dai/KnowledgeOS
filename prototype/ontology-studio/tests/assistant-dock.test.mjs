import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createRequire, Module } from 'node:module';
import { dirname } from 'node:path';
import { fileURLToPath } from 'node:url';

const require = createRequire(import.meta.url);
const ts = require('typescript');
function load(relative, stubs = {}) {
  const file = fileURLToPath(new URL(relative, import.meta.url));
  const mod = new Module(file); mod.filename = file; mod.paths = Module._nodeModulePaths(dirname(file));
  const parentRequire = mod.require.bind(mod);
  mod.require = id => id in stubs ? stubs[id] : parentRequire(id);
  mod._compile(ts.transpileModule(readFileSync(file, 'utf8'), { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX } }).outputText, file);
  return mod.exports;
}
const source = relative => readFileSync(new URL(relative, import.meta.url), 'utf8');

test('⌘J and Ctrl J toggle the assistant; other chords do not', () => {
  const { isAssistantShortcut } = load('../app/workspace-title-bar.tsx', { react: { useEffect() {} }, 'lucide-react': {}, '@/components/ui/button': {}, './tool-hint': {}, 'react/jsx-runtime': { jsx() {}, jsxs() {}, Fragment: 'f' } });
  const key = (key, extra = {}) => ({ key, metaKey: false, ctrlKey: false, altKey: false, shiftKey: false, ...extra });
  assert.equal(isAssistantShortcut(key('j', { metaKey: true })), true);
  assert.equal(isAssistantShortcut(key('J', { ctrlKey: true })), true);
  assert.equal(isAssistantShortcut(key('j')), false);
  assert.equal(isAssistantShortcut(key('j', { metaKey: true, shiftKey: true })), false);
  assert.equal(isAssistantShortcut(key('k', { metaKey: true })), false);
});

test('every assistant renders in the docked, non-modal panel', () => {
  const panel = source('../app/assistant/assistant-panel.tsx');
  assert.match(panel, /export function AssistantDock/);
  assert(!panel.includes('@/components/ui/sheet'), 'the modal sheet is gone');
  assert.match(panel, /hidden=\{!open\}/, 'a closed dock keeps its drafts mounted');
  for (const file of ['../app/rules/rules-assistant.tsx', '../app/templates/template-interview.tsx', '../app/extraction/card-assistant.tsx', '../app/semantic-assistant.tsx']) {
    assert.match(source(file), /<AssistantDock /, `${file} uses the dock`);
  }
  const css = source('../app/assistant/assistant.css');
  assert.match(css, /\.assistant-dock \{ position: absolute;/);
});

test('rules and templates keep one assistant entry, in the title bar', () => {
  const rules = source('../app/rules/rules-workspace.tsx');
  assert.match(rules, /<WorkspaceTitleBar title="规则与决策"/);
  assert(!rules.includes('aria-label="助手起草"'), 'the rules toolbar sparkle is gone');
  assert(!rules.includes('<Sparkles />助手起草'), 'the directory button is gone');
  assert.match(rules, /useAssistantShortcut\(active && Boolean\(session\), toggleAssistant\)/);
  assert(!rules.includes('logic-canvas-context'), 'the current rule sits in the title bar, not in a floating card');
  const templates = source('../app/templates/template-workspace.tsx');
  assert.match(templates, /<WorkspaceTitleBar title="工艺模板"/);
  assert(!templates.includes('aria-label="工艺访谈助手"'), 'the template toolbar sparkle is gone');
  assert(!templates.includes('canvas-document-label'), 'the template name moved into the title bar');
  assert.match(templates, /hidden=\{!showInspector \|\| \(canEdit && interviewOpen\)\}/, 'details and assistant share the slot');
});

test('a review links back to the object in its own workspace', () => {
  const { workspaceLink } = load('../app/changesets/change-diff.ts');
  assert.deepEqual(workspaceLink('本体与定义', { operations: [{ type: 'update', targetId: 'component_level' }] }), { href: '/?definition=component_level', label: '在白板中打开' });
  assert.equal(workspaceLink('本体与定义', { operations: [{ type: 'create', targetId: 'new_thing' }] }), null, 'nothing to open before it exists');
  assert.deepEqual(workspaceLink('业务逻辑', { operations: [{ type: 'update', targetKind: 'decision_table', after: { id: 'size_selection' } }] }), { href: '/rules?definition=decision_table%3Asize_selection', label: '在规则与决策中打开' });
  assert.deepEqual(workspaceLink('专家模板', { operations: [], patch: { template: { id: 'gearbox' } } }), { href: '/templates?template=gearbox', label: '在工艺模板中打开' });
  assert.equal(workspaceLink('知识变更', { operations: [] }), null);
});

test('the whiteboard says when it shows one card\'s neighbours and offers the whole board back', () => {
  const page = source('../app/ontology-workspace.tsx');
  assert.match(page, /className="canvas-focus-banner"/);
  assert.match(page, /onClick=\{\(\) => setFocusMode\(false\)\}>显示全部<\/Button>/);
});
