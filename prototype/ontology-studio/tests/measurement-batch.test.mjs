import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
const require = createRequire(import.meta.url), ts = require('typescript');
const source = readFileSync(new URL('../app/templates/measurement-batch.ts', import.meta.url), 'utf8');
const compiled = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } }).outputText;
const compiledModule = { exports: {} }; new Function('exports', 'module', compiled)(compiledModule.exports, compiledModule);
const { createMeasurementBatch } = compiledModule.exports;
function fixture(apply) {
  const callbacks = new Map(); let index = 0;
  const batch = createMeasurementBatch(apply, callback => { callbacks.set(++index, callback); return index; }, id => callbacks.delete(id));
  const tick = () => { const queued = [...callbacks.values()]; callbacks.clear(); for (const callback of queued) callback(); };
  return {batch,callbacks,tick};
}
test('observer notifications never synchronously mutate the flow store; repeated entries share a frame', () => {
  let inObserver = false; const calls = [];
  const {batch,callbacks,tick} = fixture(updates => { assert.equal(inObserver, false); calls.push(updates); });
  inObserver = true;
  batch.update(new Map([['step_1',{element:'old'}]]));
  batch.update(new Map([['step_1',{element:'current'}],['step_2',{element:'added'}]]));
  assert.equal(calls.length,0); assert.equal(callbacks.size,1);
  inObserver = false; tick();
  assert.equal(calls.length,1);
  assert.deepEqual([...calls[0]], [['step_1',{element:'current'}],['step_2',{element:'added'}]]);
});
test('measurements triggered during a commit wait for another frame rather than recurse', () => {
  const calls=[]; let batch;
  const setup=fixture(updates=>{calls.push([...updates.keys()]);if(updates.has('group'))batch.update(new Map([['child',1]]));});
  batch=setup.batch;batch.update(new Map([['group',1]]));setup.tick();
  assert.deepEqual(calls,[['group']]); assert.equal(setup.callbacks.size,1);
  setup.tick(); assert.deepEqual(calls,[['group'],['child']]);
});
test('unmount cancels queued work and ignores callbacks from disconnected observers', () => {
  const calls=[];const {batch,callbacks,tick}=fixture(updates=>calls.push(updates));
  batch.update(new Map([['deleted_step',1]]));batch.dispose();
  assert.equal(callbacks.size,0);tick();batch.update(new Map([['late_step',1]]));tick();
  assert.equal(calls.length,0);assert.equal(callbacks.size,0);
});
test('empty observations do not schedule frames and independent whiteboards own independent queues', () => {
  const first=[],second=[];const a=fixture(updates=>first.push(updates)),b=fixture(updates=>second.push(updates));
  a.batch.update(new Map()); assert.equal(a.callbacks.size,0);
  a.batch.update(new Map([['step',1]]));b.batch.update(new Map([['step',2]]));
  a.batch.dispose();a.tick();b.tick();assert.equal(first.length,0);assert.equal(second[0].get('step'),2);
});
