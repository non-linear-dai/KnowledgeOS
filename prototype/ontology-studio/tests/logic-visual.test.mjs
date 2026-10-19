import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createRequire, Module } from 'node:module';
import { dirname } from 'node:path';
import { fileURLToPath } from 'node:url';
const require = createRequire(import.meta.url), ts = require('typescript');
function load(relative, overrides = {}) {
  const file = fileURLToPath(new URL(relative, import.meta.url)), mod = new Module(file);
  mod.filename = file; mod.paths = Module._nodeModulePaths(dirname(file));
  const original = mod.require.bind(mod); mod.require = name => name in overrides ? overrides[name] : original(name);
  mod._compile(ts.transpileModule(readFileSync(file, 'utf8'), {compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022,esModuleInterop:true}}).outputText,file);
  return mod.exports;
}
const api = load('../app/rules/logic-api.ts', {'../knowledgeos-api':{}});
const visual = load('../app/rules/logic-visual.ts', {'./logic-api':api});
const examples = JSON.parse(readFileSync(new URL('../app/rules/logic-examples.json',import.meta.url),'utf8'));
const assets = [
  {id:'operation_cycle_time',label:'Cycle',ref:'model:operation_cycle_time@1.0.0',kind:'model',lifecycle:'active',definition:examples.model},
  {id:'operation_energy',label:'Energy',ref:'model:operation_energy@1.0.0',kind:'model',lifecycle:'active',definition:{id:'operation_energy',version:'1.0.0',inputs:[{id:'effective_power',unit:'kW'},{id:'elapsed_time',unit:'hour'}],output_unit:'kWh'}},
  {id:'size',label:'Size',ref:'decision:size:1.0.0',kind:'decision_table',lifecycle:'active',definition:examples.decision_table},
];
test('canvas connections change the canonical binding and reject cycles without mutating drafts',()=>{
  const graph=structuredClone(examples.decision_model), original=structuredClone(graph);
  const next=visual.bindGraph(graph,'energy','elapsed_time',{source:'node',node:'duration',path:['output','value']});
  assert.deepEqual(next.nodes[1].bindings.elapsed_time,{source:'node',node:'duration',path:['output','value']});
  assert.deepEqual(graph,original);
  assert.throws(()=>visual.bindGraph(graph,'duration','inputs',{source:'node',node:'energy',path:['output']}),/循环/);
  assert.throws(()=>visual.bindGraph(graph,'energy','effective_power',{source:'node',node:'missing',path:[]}),/不存在/);
  assert.throws(()=>visual.bindGraph(graph,'energy','effective_power',{source:'facts',path:['__proto__']}),/路径/);
});
test('business connections distinguish containers, booleans and raw numeric units',()=>{
  const port=(type,unit)=>({type,unit,key:'p',path:[],label:'p'});
  assert.throws(()=>visual.assertPortCompatibility(port('list'),port('number')),/类型/);
  assert.throws(()=>visual.assertPortCompatibility(port('boolean'),port('number')),/不能/);
  assert.throws(()=>visual.assertPortCompatibility(port('number','minute'),port('number','hour')),/单位/);
  assert.doesNotThrow(()=>visual.assertPortCompatibility(port('number','hour'),port('number','hour')));
  assert.doesNotThrow(()=>visual.assertPortCompatibility(port('quantity','minute'),port('number','hour'),[{id:'unit:minute',config:{dimension:'duration'}},{id:'unit:hour',config:{dimension:'duration'}}]));
  assert.throws(()=>visual.assertPortCompatibility(port('quantity','kW'),port('number','hour')),/单位/);
  assert.doesNotThrow(()=>visual.assertPortCompatibility(port('object'),port('any')));
});
test('deleting referenced nodes requires explicit disconnection',()=>{
  const graph=structuredClone(examples.decision_model);
  assert.throws(()=>visual.deleteGraphNode(graph,'duration'),/仍被使用/);
  let next=visual.bindGraph(graph,'energy','elapsed_time');
  next=visual.bindGraph(next,'$output','duration');
  next=visual.deleteGraphNode(next,'duration');
  assert.equal(next.nodes.length,1); assert.equal(graph.nodes.length,2);
});
test('adding library assets creates real per-field inputs with stable independent node identifiers',()=>{
  const graph={id:'graph',version:'1.0.0',nodes:[],outputs:{}};
  const first=visual.addGraphAsset(graph,assets[2]), second=visual.addGraphAsset(first,assets[2]);
  assert.equal(graph.nodes.length,0); assert.notEqual(second.nodes[0].id,second.nodes[1].id);
  assert.deepEqual(first.nodes[0].bindings.size,{source:'facts',path:['facts','size']});
  assert.throws(()=>visual.addGraphAsset(graph,{...assets[2],lifecycle:'draft'}),/已启用/);
  assert.throws(()=>visual.addGraphAsset(graph,{...assets[2],kind:'decision_model'}),/已启用/);
  assert.throws(()=>visual.addGraphAsset(graph,{...assets[2],definition:{...assets[2].definition,governed_only:true}}),/权威来源/);
  assert.throws(()=>visual.addGraphAsset({...graph,nodes:Array.from({length:30},(_,index)=>({id:`step_${index}`}))},assets[2]),/30/);
});
test('scenario forms infer external fields but preserve exact user inputs and typed quantities',()=>{
  const current={time_inputs:{baseline_time:{literal:'30',unit:'minute'},workload_factor:'9007199254740993.0001'},power:{literal:'3',unit:'kW'}};
  const next=visual.completeScenario('decision_model',examples.decision_model,assets,[],current);
  assert.deepEqual(next,current);
  const shape=visual.scenarioShape('decision_model',examples.decision_model,assets,[]);
  assert.deepEqual(Object.keys(shape).sort(),['power','time_inputs']);
  assert.ok('baseline_time' in shape.time_inputs);
  const ports=visual.flattenValues(current);
  const time=ports.find(port=>port.path.join('.')==='time_inputs.baseline_time');
  assert.equal(time.type,'quantity');assert.equal(time.unit,'minute');
  assert.ok(!ports.some(port=>port.path.join('.')==='time_inputs.baseline_time.literal'));
});
test('business expectations canonicalize declared numeric outputs without changing the assertion',()=>{
  const large='9007199254740993123456789.00100';
  assert.equal(visual.canonicalDecimalString(large),'9007199254740993123456789.001');
  assert.equal(visual.canonicalDecimalString('1000'),'1000.0');
  assert.equal(visual.canonicalDecimalString(' 00012.3000 '),'12.3');
  assert.equal(visual.canonicalDecimalString('small'),'small');
  const model=structuredClone(examples.decision_model);
  model.test_cases[0].expected.output.duration.value='2';
  model.test_cases[0].expected.output.energy.value=large;
  const next=visual.normalizeCaseNumbers('decision_model',model,assets);
  assert.equal(next.test_cases[0].expected.output.duration.value,'2.0');
  assert.equal(next.test_cases[0].expected.output.energy.value,'9007199254740993123456789.001');
  assert.equal(model.test_cases[0].expected.output.duration.value,'2');
  assert.deepEqual(next.test_cases[0].input,model.test_cases[0].input);
});
test('human labels and expected forms preserve unknown definitions and typed results',()=>{
  assert.equal(visual.fieldLabel('size'),'尺寸演示值');
  assert.equal(visual.fieldLabel('custom',[],'投影面积'),'投影面积');
  assert.equal(visual.fieldLabel('custom'),'custom');
  assert.equal(visual.assetLabel(assets[0]),'工时计算');
  assert.equal(visual.caseLabel('large_part',0),'大件选型');
  assert.deepEqual(visual.expectedShape('decision_model',examples.decision_model,assets).output.energy,{value:'',unit:'kWh'});
  assert.equal(visual.formulaText(examples.model.formula),'（基准工时 × 工作量系数）');
});
test('nested form edits cannot pollute prototypes or overwrite unrelated facts',()=>{
  const source={facts:{size:'100',material:'ABS'}};
  const next=visual.setPath(source,['facts','size'],'600');
  assert.deepEqual(next,{facts:{size:'600',material:'ABS'}});assert.equal(source.facts.size,'100');
  assert.throws(()=>visual.setPath(source,['constructor','prototype','bad'],'x'),/路径/);
  assert.deepEqual(visual.removePath(next,['facts','size']),{facts:{material:'ABS'}});
});
test('custom scenario fields are named in business language and require disconnection before removal',()=>{
  const graph={id:'graph',version:'1.0.0',nodes:[],outputs:{}};
  let next=visual.addScenarioField(graph,'第二道工序基准工时','number','hour');
  assert.deepEqual(next.input_fields,[{id:'scenario_1',label:'第二道工序基准工时',type:'number',unit:'hour'}]);
  assert.deepEqual(visual.scenarioShape('decision_model',next,[],[]),{scenario_1:{literal:'',unit:'hour'}});
  next=visual.addGraphAsset(next,assets[0]);
  next=visual.bindGraph(next,'step_1','baseline_time',{source:'facts',path:['scenario_1']});
  assert.throws(()=>visual.deleteScenarioField(next,'scenario_1'),/仍被使用/);
  next=visual.bindGraph(next,'step_1','baseline_time');
  assert.deepEqual(visual.deleteScenarioField(next,'scenario_1').input_fields,[]);
  assert.equal(graph.input_fields,undefined);
});
test('case comparisons show the asserted business fields and retain extra list rows',()=>{
  const actual={status:'complete',output:{duration:{value:'2.0',unit:'hour'},energy:{value:'6.0',unit:'kWh'}},trace:[{internal:'details'}]};
  assert.deepEqual(visual.projectExpected(actual,{output:{duration:{value:'2.0'}}}),{output:{duration:{value:'2.0'}}});
  assert.deepEqual(visual.projectExpected([{verdict:'valid',internal:'a'},{verdict:'invalid'}],[{verdict:'valid'}]),[{verdict:'valid'},{verdict:'invalid'}]);
});
