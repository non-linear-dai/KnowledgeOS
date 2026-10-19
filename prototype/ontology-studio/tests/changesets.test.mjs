import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createRequire, Module } from 'node:module';
import { dirname } from 'node:path';
import { fileURLToPath } from 'node:url';
const require = createRequire(import.meta.url);
const ts = require('typescript');
function load(relative, overrides = {}) {
  const file = fileURLToPath(new URL(relative, import.meta.url));
  const mod = new Module(file); mod.filename = file; mod.paths = Module._nodeModulePaths(dirname(file));
  const original = mod.require.bind(mod); mod.require = name => name in overrides ? overrides[name] : original(name);
  mod._compile(ts.transpileModule(readFileSync(file, 'utf8'), { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } }).outputText, file);
  return mod.exports;
}
const api = load('../app/changesets/changesets-api.ts', { '../knowledgeos-api': {} });
const ordinary = { id:'ontology-1', title:'Concept update', target_source:'control/ontology/core.yaml', status:'review_required', actor:'author', risk:'normal', operations:[], patch:{op:'studio_batch'} };
const template = { ...ordinary, id:'template-1', target_source:'knowledge/templates/route.yaml, knowledge/operations/mill.yaml', risk:'high', patch:{op:'template_package',template:{id:'route'}} };
const session = (...permissions) => ({principal:'reviewer',roles:[],permissions});

test('unified queue retains raw template packages, provenance and exact values with cancellation', async () => {
  const record = { ...template, publication:{source_refs:[{locator:'original:42'}]}, patch:{...template.patch, exact:'0.00000000000000001'} };
  const signal = new AbortController().signal; let call;
  const client = load('../app/changesets/changesets-api.ts', {'../knowledgeos-api': { request: async (path, init) => { call={path,init}; return {data:[ordinary,record]}; } }});
  assert.deepEqual(await client.loadChangeRecords(signal), [ordinary,record]);
  assert.equal(call.path,'/v1/changesets?limit=500'); assert.equal(call.init.signal,signal);
});
test('review and publication permissions stay separate for templates and ordinary definitions', () => {
  assert.equal(api.reviewAccess(template,session('review','publish')).canReview,false);
  assert.equal(api.reviewAccess(ordinary,session('template_review','template_publish')).canReview,false);
  assert.equal(api.reviewAccess(template,session('template_review')).canApprove,true);
  assert.equal(api.reviewAccess({...template,status:'approved'},session('template_review')).canPublish,false);
  assert.equal(api.reviewAccess({...template,status:'approved'},session('template_publish')).canPublish,true);
  assert.equal(api.reviewAccess({...ordinary,status:'approved'},session('publish')).canPublish,true);
  assert.deepEqual(api.reviewAccess(template,null),{canReview:false,canApprove:false,canPublish:false});
});
test('high risk self approval is unavailable while a returned or published record has no review action', () => {
  assert.equal(api.reviewAccess({...template,actor:'reviewer'},session('template_review')).canApprove,false);
  assert.equal(api.reviewAccess({...ordinary,actor:'reviewer'},session('review')).canApprove,true);
  for (const status of ['approved','published','rejected','changes_requested']) {
    assert.equal(api.reviewAccess({...ordinary,status},session('review')).canReview,false);
  }
});
test('package paths are classified consistently and excluded from ordinary batch review', () => {
  const operation = {...template,id:'operation-1',target_source:' knowledge/operations/mill.yaml '};
  const decision = {...template,id:'decision-1',target_source:'knowledge/decisions/routing.yaml'};
  const knowledge = {...ordinary,id:'knowledge-1',target_source:'knowledge/entities/acme.md'};
  assert.equal(api.changeCategory(ordinary),'本体与定义'); assert.equal(api.changeCategory(knowledge),'知识变更');
  for (const change of [template,operation,decision]) assert.equal(api.changeCategory(change),'专家模板');
  assert.deepEqual(api.batchReviewRecords([ordinary,template,operation,decision,knowledge]).map(change=>change.id),['ontology-1','knowledge-1']);
});
test('source application is available only for supported packages and complete supported control paths', () => {
  assert.equal(api.supportsSourceApplication(template),true); assert.equal(api.supportsSourceApplication(ordinary),true);
  assert.equal(api.supportsSourceApplication({...ordinary,target_source:'control/ontology/core.yaml, knowledge/entities/acme.md'}),false);
  assert.equal(api.supportsSourceApplication({...ordinary,target_source:'control/policies/authority.yaml'}),false);
  assert.equal(api.supportsSourceApplication({...ordinary,patch:{op:'unrecognized'}}),false);
  assert.equal(api.supportsSourceApplication({...template,patch:{op:'studio_batch'}}),false);
});
test('overview has its own route while retaining the same editor surface as the whiteboard', () => {
  const {workspaces,workspaceForPath,workspaceSurface}=load('../app/workspace-navigation.ts');
  assert.equal(workspaceForPath('/overview/'),'overview'); assert.equal(workspaceForPath('/changesets'),'changesets');
  assert.equal(workspaceSurface('overview'),workspaceSurface('ontology'));
  assert.equal(new Set(workspaces.map(item=>item.href)).size,6);
  assert.equal(workspaceForPath('/rules'),'rules');
  assert.notEqual(workspaceSurface('changesets'),workspaceSurface('templates'));
});
