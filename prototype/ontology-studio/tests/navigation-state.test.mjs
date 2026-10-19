import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createRequire, Module } from 'node:module';
import { dirname } from 'node:path';
import { fileURLToPath } from 'node:url';

const require = createRequire(import.meta.url);
const ts = require('typescript');
const file = fileURLToPath(new URL('../app/navigation-state.ts', import.meta.url));
const mod = new Module(file); mod.filename = file; mod.paths = Module._nodeModulePaths(dirname(file));
mod._compile(ts.transpileModule(readFileSync(file, 'utf8'), { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } }).outputText, file);
const { navigationReducer: reduce, initialNavigationState: initial } = mod.exports;

test('navigation starts unpinned and compact, and returns to compact after working', () => {
  assert.deepEqual(initial, { expanded:false, pinned:false });
  const opened = reduce(initial, {type:'show'});
  assert.equal(opened.expanded,true);
  assert.deepEqual(reduce(opened,{type:'close'}), initial);
  assert.equal(initial.expanded,false);
});
test('a pinned navigation ignores automatic closing, route selection and manual toggles', () => {
  const pinned = reduce(initial,{type:'set-pinned',pinned:true});
  assert.deepEqual(pinned,{expanded:true,pinned:true});
  assert.equal(reduce(pinned,{type:'close'}),pinned);
  assert.equal(reduce(pinned,{type:'toggle'}),pinned);
  assert.equal(reduce(pinned,{type:'show'}),pinned);
});
test('unpinning immediately returns to the icon rail', () => {
  const pinned = reduce(initial,{type:'set-pinned',pinned:true});
  const unpinned = reduce(pinned,{type:'set-pinned',pinned:false});
  assert.deepEqual(unpinned,initial);
  assert.deepEqual(reduce(unpinned,{type:'close'}),initial);
  assert.equal(pinned.pinned,true);
});
test('explicit opening is idempotent, while compact mode can close a restored pin', () => {
  assert.equal(reduce(initial,{type:'close'}),initial);
  const opened = reduce(initial,{type:'show'});
  assert.equal(reduce(opened,{type:'show'}),opened);
  const restored = reduce(initial,{type:'set-pinned',pinned:true});
  const mobile = reduce(reduce(restored,{type:'set-pinned',pinned:false}),{type:'close'});
  assert.deepEqual(mobile,initial);
  assert.deepEqual(reduce(mobile,{type:'set-pinned',pinned:true}),{expanded:true,pinned:true});
});
