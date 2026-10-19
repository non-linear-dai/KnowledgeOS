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
const api = load('../app/rules/logic-api.ts', {'../knowledgeos-api':{}});
test('asset filters compose kind lifecycle scope and literal business search', () => {
  const assets = [
    {ref:'rule:part',kind:'business_rule',label:'尺寸 & 能力',definition:{description:'设备'},bindings:['part','equipment'],lifecycle:'active'},
    {ref:'rule:other',kind:'business_rule',label:'其他',definition:{},bindings:['equipment'],lifecycle:'draft'},
  ];
  assert.deepEqual(api.filterLogic(assets,' & ','business_rule','active','part').map(item=>item.ref),['rule:part']);
  assert.equal(api.filterLogic(assets,'','model','','').length,0);
  assert.equal(api.bumpVersion('2.3.9'),'2.3.10');
});
test('graph dependencies retain every upstream node and deduplicate shared inputs', () => {
  const graph={id:'selection',version:'1.0.0',nodes:[{id:'score',bindings:{subject:{source:'facts',path:['subject']},first:{source:'node',node:'filter',path:['eligible']},second:{source:'node',node:'filter',path:['checks']}}}]};
  assert.deepEqual(api.graphDependencies(graph),[{source:'filter',target:'score'}]);
});
test('logic clients retain exact values, baseline, cancellation and idempotency identity', async () => {
  const calls=[];
  const client=load('../app/rules/logic-api.ts',{'../knowledgeos-api':{request:async(path,init)=>{calls.push({path,init});return {data:{ok:true}};}}});
  const signal=new AbortController().signal;
  await client.loadLogic(signal); assert.equal(calls[0].init.signal,signal);
  const definition={id:'size',version:'1.0.0',limit:'0.000000000000001'};
  await client.proposeLogic('decision_table',definition,'expert evidence','fingerprint','request-key');
  assert.deepEqual(JSON.parse(calls[1].init.body),{kind:'decision_table',definition,reason:'expert evidence',base_revision:'fingerprint',idempotency_key:'request-key'});
  await client.runLogic('decision:size:1.0.0','run-key',undefined,{subject_id:'part:甲/1',context_ids:{operation:'operation:1'}});
  assert.equal(JSON.parse(calls[2].init.body).idempotency_key,'run-key');
  assert.equal(JSON.parse(calls[2].init.body).facts,undefined);
  await client.loadLogicRuns('decision:size:1.0.0','part:甲/1',signal);
  const params=new URL(calls[3].path,'http://local.test').searchParams;
  assert.equal(params.get('subject_id'),'part:甲/1');assert.equal(calls[3].init.signal,signal);
});
test('new dimension examples distinguish small and large parts with explicit expert cases', () => {
  const table=api.newLogic('decision_table','size',[],[],[]);
  assert.equal(table.hit_policy,'UNIQUE');
  assert.equal(table.test_cases.length,2);
  assert.equal(table.test_cases[0].expected.result.equipment_class,'small');
  assert.equal(table.test_cases[1].expected.result.equipment_class,'large');
  assert.notEqual(table.test_cases[0].input.facts.size,table.test_cases[1].input.facts.size);
  assert.equal(table.inputs[0].type,'number');
});
test('instance sample inputs honor registered units and every context role', () => {
  const definition={id:'power',version:'1.0.0',inputs:[{id:'required',role:'subject',predicate:'rated_power'},{id:'capacity',role:'candidate',predicate:'rated_power'},{id:'process',role:'operation',predicate:'rated_power'}]};
  const sample=api.sampleInput('business_rule',definition,[{id:'rated_power',config:{value_type:'quantity',default_unit:'kW'}}]);
  assert.deepEqual(sample.subject.rated_power,{literal:'10',unit:'kW'});
  assert.equal(sample.candidates[0].id,'candidate_demo');
  assert.equal(sample.contexts.operation.rated_power.unit,'kW');
});
test('logic changes share review queue while table author review and publication permissions remain distinct', () => {
  const changes=load('../app/changesets/changesets-api.ts',{'../knowledgeos-api':{}});
  const change={target_source:'knowledge/decisions/test/v1.0.0.md',patch:{op:'logic_asset',kind:'decision_table'},status:'review_required',risk:'high',actor:'author'};
  assert.equal(changes.changeCategory(change),'业务逻辑');assert.equal(changes.supportsSourceApplication(change),true);
  assert.equal(changes.reviewAccess(change,{principal:'reviewer',permissions:['review']}).canApprove,false);
  assert.equal(changes.reviewAccess(change,{principal:'reviewer',permissions:['template_review']}).canApprove,true);
  assert.equal(changes.batchReviewRecords([change]).length,0);
});
