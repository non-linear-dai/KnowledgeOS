import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { createRequire } from 'node:module';
const require = createRequire(import.meta.url), ts = require('typescript');
const source = readFileSync(fileURLToPath(new URL('../app/templates/route-editing.ts', import.meta.url)), 'utf8');
const compiled = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } }).outputText;
const compiledModule = { exports: {} }; new Function('exports', 'module', compiled)(compiledModule.exports, compiledModule);
const { connectRoute, insertRouteSequence, duplicateRouteItems, removeRouteItems, moveRouteStep, layoutRoute } = compiledModule.exports;
function fixture() {
  return { id: 'test', version: '1.0.0', label: 'Test', family: 'general', output_basis: 'piece', summary: '',
    groups: [{ id: 'faces', label: 'Faces', iteration_set: 'faces', join_policy: 'all' }],
    steps: [{ id: 'cut', label: 'Cut', parent: null, operation_ref: 'operation:cut:1.0.0', cost_basis: 'piece' },
      { id: 'mill', label: 'Mill', parent: 'faces', operation_ref: 'operation:mill:1.0.0', cost_basis: 'face' },
      { id: 'check_face', label: 'Check face', parent: 'faces', operation_ref: 'operation:check:1.0.0', cost_basis: 'face' },
      { id: 'inspect', label: 'Inspect', parent: null, operation_ref: 'operation:check:1.0.0', cost_basis: 'piece' }],
    edges: [{ from: 'cut', to: 'faces' }, { from: 'faces', to: 'inspect' }, { from: 'mill', to: 'check_face' }],
    operations: [], cases: [{ id: 'basic', facts: {}, sets: { faces: [{ id: 'face_1' }] }, expected_operations: 4 }] };
}
test('bulk insertion keeps downstream joins and reuses version-pinned library operations', () => {
  const original = fixture();
  const result = insertRouteSequence(original, ['Wash', 'Dry'], { after: 'cut', library: [{ id: 'operation:wash:2.0.0', label: 'Wash', cost_basis: 'batch' }] });
  assert.equal(original.steps.length, 4);
  assert.equal(result.template.steps.find((item) => item.id === result.ids[0]).operation_ref, 'operation:wash:2.0.0');
  assert.equal(result.template.operations.length, 1);
  assert.deepEqual(result.template.edges.filter((edge) => edge.from === 'cut'), [{ from: 'cut', to: result.ids[0] }]);
  assert(result.template.edges.some((edge) => edge.from === result.ids[1] && edge.to === 'faces'));
  assert(result.template.edges.some((edge) => edge.from === result.ids[0] && edge.to === result.ids[1]));
});
test('whiteboard rejects cross-scope and cycles while allowing optional dependencies', () => {
  const original = fixture();
  assert.throws(() => connectRoute(original, 'mill', 'inspect'), /同一层级/);
  assert.throws(() => connectRoute(original, 'inspect', 'cut'), /循环/);
  original.steps.find((step) => step.id === 'inspect').when = { source: 'facts', field: 'enabled', op: 'eq', value: true };
  assert(connectRoute(original, 'cut', 'inspect').edges.some(e => e.from === 'cut' && e.to === 'inspect'));
});
test('duplicating a group preserves its internal graph without copying external dependencies', () => {
  const original = fixture(), result = duplicateRouteItems(original, ['faces']);
  const copiedGroup = result.ids[0], children = result.template.steps.filter((step) => step.parent === copiedGroup);
  assert.equal(children.length, 2);
  assert.equal(result.template.operations.length, 0);
  assert(result.template.edges.some((edge) => edge.from === children[0].id && edge.to === children[1].id));
  assert(!result.template.edges.some((edge) => edge.from === copiedGroup || edge.to === copiedGroup));
  assert.equal(original.groups.length, 1);
  assert.equal(removeRouteItems(result.template, [copiedGroup]).steps.length, 4);
});
test('moving a stage across scopes removes old links and protects item-dependent conditions', () => {
  const original = fixture(), moved = moveRouteStep(original, 'check_face', null);
  assert.equal(moved.steps.find((step) => step.id === 'check_face').parent, null);
  assert(!moved.edges.some((edge) => edge.to === 'check_face'));
  original.steps.find((step) => step.id === 'mill').when = { source: 'item', field: 'enabled', op: 'eq', value: true };
  assert.throws(() => moveRouteStep(original, 'mill', null), /重复组/);
});
test('deterministic canvas layout shows predecessor columns and every group child', () => {
  const boxes = layoutRoute(fixture()), byId = new Map(boxes.map((box) => [box.id, box]));
  assert(byId.get('cut').x < byId.get('faces').x);
  assert(byId.get('faces').x < byId.get('inspect').x);
  assert(byId.get('mill').x < byId.get('check_face').x);
  assert.equal(byId.get('mill').parent, 'faces');
  assert(byId.get('check_face').x + byId.get('check_face').width < byId.get('faces').width);
});

const { parseRoutePlan, insertRoutePlan, duplicateRouteAfter, routeOutline, selectedRouteSteps, patchRouteSteps } = compiledModule.exports;
test('stage outline creates scoped phases, pins operations and bridges every downstream branch', () => {
  const original = fixture(); original.edges.push({from:'cut',to:'inspect'});
  const plan = parseRoutePlan('[Clean]\n1. Wash\n2. Dry\n[Patterns | 重复 | layers | 顺序]\nExpose\nEtch\n[Board]\nPress');
  assert.equal(plan.step_count, 5); assert.equal(plan.group_count, 3);
  const result = insertRoutePlan(original, plan, {after:'cut', library:[{id:'operation:wash:2.0.0', label:'Wash',cost_basis:'batch'}]});
  const [clean,patterns,board] = result.groupIds;
  assert.deepEqual(result.template.groups.filter(g=>result.groupIds.includes(g.id)).map(g=>g.group_mode), ['conditional','repeat','conditional']);
  assert.equal(result.template.groups.find(g=>g.id===patterns).execution_mode,'sequential');
  assert.equal(result.template.groups.find(g=>g.id===clean).when, undefined);
  assert.equal(result.template.steps.find(s=>s.id===result.ids[0]).operation_ref,'operation:wash:2.0.0');
  assert.deepEqual(result.template.edges.filter(e=>e.from===board), [{from:board,to:'faces'},{from:board,to:'inspect'}]);
  assert(result.template.edges.some(e=>e.from===clean&&e.to===patterns));
  assert(result.template.edges.some(e=>e.from===patterns&&e.to===board));
  assert.deepEqual(result.template.cases[0].sets.layers,[{id:'item_1'}]);
  assert.equal(original.groups.length,1); assert.equal(original.steps.length,4);
});
test('structured paste preserves existing repeat sets and rejects nested stages atomically', () => {
  const original=fixture(), before=JSON.stringify(original);
  const repeated=insertRoutePlan(original,parseRoutePlan('[Reuse | 重复 | faces]\nMill'),{after:'cut',library:[]});
  assert.deepEqual(repeated.template.cases[0].sets.faces,original.cases[0].sets.faces);
  const inside=insertRoutePlan(original,parseRoutePlan('Wash\nDry'),{after:'mill',library:[]});
  assert(inside.template.steps.filter(s=>inside.ids.includes(s.id)).every(s=>s.parent==='faces'));
  assert(inside.template.edges.some(e=>e.from===inside.selected&&e.to==='check_face'));
  assert.throws(()=>insertRoutePlan(original,parseRoutePlan('[Nested]\nWash'),{after:'mill',library:[]}),/主路线/);
  assert.equal(JSON.stringify(original),before);
});
test('version ambiguity and invalid outline input fail before mutating route', () => {
  const original=fixture(), library=[{id:'operation:mill:1.0.0',label:'Mill',cost_basis:'piece'},{id:'operation:mill:2.0.0',label:'Mill',cost_basis:'face'}];
  assert.throws(()=>insertRoutePlan(original,parseRoutePlan('Mill'),{library}),/多个标准版本/);
  const explicit=insertRoutePlan(original,parseRoutePlan('Renamed\toperation:mill:2.0.0'),{library});
  assert.equal(explicit.template.steps.at(-1).operation_ref,library[1].id);
  assert.throws(()=>insertRoutePlan(original,parseRoutePlan('Mill\toperation:missing:1.0.0'),{library}),/不可用/);
  for(const text of ['[Empty]','[A]\n[B]\nMill','[A | 重复]\nMill','[A | 重复 | objects | 非法]\nMill','1.']) assert.throws(()=>parseRoutePlan(text));
  assert.equal(original.steps.length,4);
  assert.deepEqual(parseRoutePlan('0.18μm 图形\n1.5mm 钻孔\n1) Wash').blocks[0].steps.map(step=>step.label), ['0.18μm 图形','1.5mm 钻孔','Wash']);
});
test('duplicate and append preserves incoming graph and outgoing joins for stages and steps', () => {
  const original=fixture(), groupCopy=duplicateRouteAfter(original,'faces'), id=groupCopy.ids[0];
  assert(groupCopy.template.edges.some(e=>e.from==='cut'&&e.to==='faces'));
  assert(groupCopy.template.edges.some(e=>e.from==='faces'&&e.to===id));
  assert(groupCopy.template.edges.some(e=>e.from===id&&e.to==='inspect'));
  assert(!groupCopy.template.edges.some(e=>e.from==='faces'&&e.to==='inspect'));
  const children=groupCopy.template.steps.filter(s=>s.parent===id);
  assert(groupCopy.template.edges.some(e=>e.from===children[0].id&&e.to===children[1].id));
  const stepCopy=duplicateRouteAfter(original,'mill'), copied=stepCopy.template.steps.find(s=>s.id===stepCopy.ids[0]);
  assert.equal(copied.parent,'faces');
  assert(stepCopy.template.edges.some(e=>e.from===copied.id&&e.to==='check_face'));
  assert.throws(()=>duplicateRouteAfter(original,'absent'),/不存在/);
});
test('batch edits deduplicate group selection and only affect explicitly supplied fields', () => {
  const original=fixture(); original.steps[1].when={source:'item',field:'enabled',op:'eq',value:true};
  assert.deepEqual(selectedRouteSteps(original,['faces','mill']).map(s=>s.id),['mill','check_face']);
  const edited=patchRouteSteps(original,['faces','mill'],{cost_basis:'wafer'});
  assert.deepEqual(edited.edges,original.edges);
  assert.equal(edited.steps[0].cost_basis,'piece');
  assert.equal(edited.steps[1].cost_basis,'wafer'); assert.equal(edited.steps[2].cost_basis,'wafer');
  assert.deepEqual(edited.steps[1].when,original.steps[1].when);
  assert.equal(patchRouteSteps(original,['mill'],{when:undefined}).steps[1].when,undefined);
  assert.equal(original.steps[1].cost_basis,'face');
});
test('batch item conditions cannot be applied across incompatible scopes', () => {
  const original=fixture(), condition={all:[{source:'item',field:'enabled',op:'eq',value:true}]};
  assert.throws(()=>patchRouteSteps(original,['faces','cut'],{when:condition}),/重复组/);
  assert(patchRouteSteps(original,['faces'],{when:condition}).steps[1].when);
  original.groups[0].group_mode='conditional';
  assert.throws(()=>patchRouteSteps(original,['faces'],{when:condition}),/重复组/);
  assert.equal(original.steps[1].when,undefined);
});
test('collapsed layout compresses view while outline follows precedence and preserves every child', () => {
  const original=fixture(), expanded=layoutRoute(original), folded=layoutRoute(original,['faces']);
  assert.deepEqual(folded.map(b=>b.id), expanded.map(b=>b.id));
  assert(folded.find(b=>b.id==='faces').width<expanded.find(b=>b.id==='faces').width);
  assert(folded.find(b=>b.id==='inspect').x<expanded.find(b=>b.id==='inspect').x);
  original.steps.reverse();
  assert.deepEqual(routeOutline(original).map(i=>i.id),['cut','faces','mill','check_face','inspect']);
  assert.equal(original.edges.length,3);
});
test('complex staged text enforces bounded authoring before partial generation', () => {
  const original=fixture(), plan=parseRoutePlan(Array.from({length:8},(_,stage)=>`[Stage ${stage}]\n${Array.from({length:20},(_,i)=>`Process ${stage}_${i}`).join('\n')}`).join('\n'));
  const generated=insertRoutePlan(original,plan,{after:'cut',library:[]});
  assert.equal(generated.template.steps.length,164); assert.equal(generated.groupIds.length,8);
  const tooLarge=parseRoutePlan(Array.from({length:197},(_,i)=>`Process ${i}`).join('\n'));
  assert.throws(()=>insertRoutePlan(original,tooLarge,{library:[]}),/200/);
  const full={...original,steps:[...original.steps, ...Array.from({length:195},(_,i)=>({...original.steps[0],id:`extra_${i}`}))]};
  assert.throws(()=>duplicateRouteItems(full,['faces']),/200/);
});
