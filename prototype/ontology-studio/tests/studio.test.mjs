import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { dirname, resolve } from 'node:path';
import Module, { createRequire } from 'node:module';

const require = createRequire(import.meta.url);
const ts = require('typescript');
function load(relative, overrides = {}) {
  const file = fileURLToPath(new URL(relative, import.meta.url));
  const mod = new Module(file);
  mod.filename = file;
  mod.paths = Module._nodeModulePaths(dirname(file));
  const defaultRequire = mod.require.bind(mod);
  mod.require = (name) => {
    if (name in overrides) return overrides[name];
    if (name.startsWith('.') && !name.endsWith('.json')) {
      return load(pathToFileURL(`${resolve(dirname(file), name)}.ts`).href, overrides);
    }
    return defaultRequire(name);
  };
  const output = ts.transpileModule(readFileSync(file, 'utf8'), { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, esModuleInterop: true } });
  mod._compile(output.outputText, file);
  return mod.exports;
}

const { rebaseDraft } = load('../app/draft-state.ts');
const { initialWorkspaceLayout, visibleWorkspacePanels, workspaceLayoutReducer } = load('../app/workspace-layout.ts');
const { canvasViewportForNodes, focusedDefinitionEdges, focusedDefinitionIds, layoutFilteredNodes, layoutFocusedNodes, matchesDefinitionFilters, reconcileCanvasNodes, selectionAfterChanges, uniqueEdgesById } = load('../app/canvas-state.ts');
const { wireSnapshot, connectionFailure } = load('../app/studio-contract.ts');
const { authorityOptions, initialDefinitions } = load('../app/studio-data.ts');
const { whiteboardDefinitions, whiteboardKinds } = load('../app/whiteboard-scope.ts');
const { domainDefinitionIds, workDomainLabel, workDomains } = load('../app/work-domains.ts');
const { dimensionOptions, physicalUnitOptions, currencyOptions, modelUnitOptions, conceptOptions, outputPredicateOptions } = load('../app/registered-options.ts');
const base = { id: 'org', kind: 'concept', label: 'Original', description: 'Before', lifecycle: 'active', sourcePath: 'control/ontology/extension.yaml', config: {}, refs: 0, files: [] };

test('workspaces start with only their primary content and automatic panel hiding enabled', () => {
  assert.deepEqual(visibleWorkspacePanels(initialWorkspaceLayout), { left: false, right: false });
  assert.equal(initialWorkspaceLayout.autoHide, true);
});

test('selection reveals the inspector and dismisses an automatically managed catalog', () => {
  const next = workspaceLayoutReducer({ ...initialWorkspaceLayout, leftOpen: true }, { type: 'select' });
  assert.deepEqual(visibleWorkspacePanels(next), { left: false, right: true });
});

test('focused workspace restores previous panels without altering selection or draft data', () => {
  const before = { ...initialWorkspaceLayout, leftOpen: true, rightOpen: true };
  const focused = workspaceLayoutReducer(before, { type: 'toggle-focus' });
  assert.deepEqual(visibleWorkspacePanels(focused), { left: false, right: false });
  assert.deepEqual(workspaceLayoutReducer(focused, { type: 'toggle-focus' }), before);
});

test('selecting a card does not interrupt explicit focused mode', () => {
  const next = workspaceLayoutReducer({ ...initialWorkspaceLayout, focused: true }, { type: 'select' });
  assert.equal(next.focused, true);
  assert.deepEqual(visibleWorkspacePanels(next), { left: false, right: false });
});

test('manual inspector toggle is reversible and can recall a panel from focused mode', () => {
  const open = workspaceLayoutReducer(initialWorkspaceLayout, { type: 'open', side: 'right' });
  const closed = workspaceLayoutReducer(open, { type: 'toggle', side: 'right' });
  assert.equal(closed.rightOpen, false);
  assert.deepEqual(workspaceLayoutReducer(closed, { type: 'toggle', side: 'right' }), open);
  const recalled = workspaceLayoutReducer({ ...open, focused: true }, { type: 'toggle', side: 'right' });
  assert.equal(recalled.focused, false);
  assert.equal(recalled.rightOpen, true);
});

test('canvas focus hides both panels but can be disabled independently of manual focus', () => {
  const open = { ...initialWorkspaceLayout, leftOpen: true, rightOpen: true };
  const hidden = workspaceLayoutReducer(open, { type: 'canvas-focus' });
  assert.deepEqual(visibleWorkspacePanels(hidden), { left: false, right: false });
  const pinned = workspaceLayoutReducer(open, { type: 'toggle-auto-hide' });
  assert.equal(workspaceLayoutReducer(pinned, { type: 'canvas-focus' }), pinned);
  assert.equal(workspaceLayoutReducer(pinned, { type: 'select' }).leftOpen, true);
  assert.equal(workspaceLayoutReducer(pinned, { type: 'close', side: 'right' }).rightOpen, false);
});

test('panel reducer preserves unrelated caller state and never mutates its input', () => {
  const state = { ...initialWorkspaceLayout, selectedId: 'org', draft: { label: 'Unsaved' } };
  const next = workspaceLayoutReducer(state, { type: 'open', side: 'left' });
  assert.equal(state.leftOpen, false);
  assert.equal(next.selectedId, 'org');
  assert.equal(next.draft, state.draft);
});

test('overview navigation is not duplicated in its hero and hidden inspectors remain mounted', () => {
  const overview = readFileSync(new URL('../app/control-center.tsx', import.meta.url), 'utf8');
  const studio = readFileSync(new URL('../app/ontology-workspace.tsx', import.meta.url), 'utf8');
  const template = readFileSync(new URL('../app/templates/template-workspace.tsx', import.meta.url), 'utf8');
  assert(!overview.includes('onOpenStudio'));
  assert(!overview.includes('onOpenReview'));
  assert(!overview.includes('进入结构白板'));
  assert(studio.includes('hidden={!panels.right}'));
  assert(template.includes('hidden={!showInspector}'));
  assert(!studio.includes('onCollapse={clearSelection}'));
});

test('Studio accepts the governed FX market authority', () => {
  assert(authorityOptions.includes('fx_market'));
});

test('whiteboard shows editable ontology and schema cards, excluding fixed registries and operations', () => {
  const definitions = ['schema', 'domain', 'concept', 'relation', 'predicate', 'model', 'business_constraint', 'business_rule', 'unit', 'currency', 'policy', 'connector']
    .map((kind) => ({ ...base, id: kind, kind }));
  assert.deepEqual(whiteboardDefinitions(definitions).map((item) => item.kind), whiteboardKinds);
  assert(!whiteboardKinds.includes('unit'));
  assert(!whiteboardKinds.includes('currency'));
  assert(whiteboardDefinitions(initialDefinitions).every((item) => !item.readOnly));
});

test('work domain selection uses registered scopes without expanding through shared predicates', () => {
  const definitions = [
    { ...base, id: 'domain:cost', kind: 'domain', label: 'Cost analysis', config: { definition_scope: ['org', 'summary', 'model:cost'] } },
    { ...base, id: 'org' }, { ...base, id: 'project' },
    { ...base, id: 'summary', kind: 'predicate' }, { ...base, id: 'model:cost', kind: 'model' },
  ];
  assert.deepEqual([...domainDefinitionIds(definitions, 'cost')], ['org', 'summary', 'model:cost']);
  assert(!domainDefinitionIds(definitions, 'cost').has('project'));
  assert.deepEqual([...domainDefinitionIds(definitions, 'missing')], []);
  assert.equal(workDomainLabel(workDomains(definitions)[0]), '成本分析');
  assert.equal(workDomainLabel({ id: 'custom', label: '自定义领域' }), '自定义领域');
  const demoIds = domainDefinitionIds(initialDefinitions, 'cost');
  assert(demoIds.has('product') && demoIds.has('organization') && demoIds.has('model:cost_rollup'));
  assert(!demoIds.has('project') && !demoIds.has('task') && !demoIds.has('model:project_risk_score'));
});

test('registered selectors restrict dimensions, currencies and model outputs to valid definitions', () => {
  const definitions = [
    { ...base, id: 'unit:kW', kind: 'unit', label: 'Kilowatt', config: { id: 'kW', dimension: 'power' } },
    { ...base, id: 'unit:hour', kind: 'unit', label: 'Hour', config: { id: 'hour', dimension: 'duration' } },
    { ...base, id: 'currency:CNY', kind: 'currency', label: 'Chinese yuan', config: { id: 'CNY' } },
    { ...base, id: 'equipment', kind: 'concept', label: 'Equipment', bindings: [{ predicateId: 'rated_power' }, { predicateId: 'equipment_name' }] },
    { ...base, id: 'rated_power', kind: 'predicate', label: 'Rated power', config: { storage_mode: 'assertion' } },
    { ...base, id: 'equipment_name', kind: 'predicate', label: 'Name', config: { storage_mode: 'attr' } },
  ];
  assert.deepEqual(dimensionOptions(definitions).map((item) => item.value), ['duration', 'power']);
  assert.deepEqual(physicalUnitOptions(definitions, 'power').map((item) => item.value), ['kW']);
  assert.deepEqual(currencyOptions(definitions).map((item) => item.value), ['CNY']);
  assert(modelUnitOptions(definitions).some((item) => item.value === 'currency_per_hour'));
  assert(modelUnitOptions(definitions).some((item) => item.value === 'kW'));
  assert.equal(modelUnitOptions([...definitions, { ...base, id: 'unit:one', kind: 'unit', label: 'Ratio', config: { id: 'one', dimension: 'dimensionless' } }]).filter((item) => item.value === 'one').length, 1);
  assert(!modelUnitOptions(definitions).some((item) => item.value === 'CNY'));
  assert.deepEqual(conceptOptions(definitions).map((item) => item.value), ['equipment']);
  assert.deepEqual(outputPredicateOptions(definitions, 'equipment').map((item) => item.value), ['rated_power']);
  assert.deepEqual(outputPredicateOptions(definitions, 'missing'), []);
});

test('canvas keeps measured nodes and dragged positions while definitions update', () => {
  const current = [{ id: 'concept:organization', position: { x: 420, y: 260 }, measured: { width: 248, height: 148 }, selected: true, data: { label: 'Old' } }];
  const projected = [{ id: 'concept:organization', position: { x: 80, y: 60 }, selected: false, data: { label: 'New' } }];
  assert.deepEqual(reconcileCanvasNodes(current, projected)[0].position, { x: 420, y: 260 });
  assert.deepEqual(reconcileCanvasNodes(current, projected)[0].measured, { width: 248, height: 148 });
  assert.equal(reconcileCanvasNodes(current, projected)[0].data.label, 'New');
  assert.deepEqual(reconcileCanvasNodes(current, projected, true)[0].position, { x: 80, y: 60 });
});

test('canvas filters intersect search, type, lifecycle and storage without keeping unmatched cards', () => {
  const definitions = [
    { ...base, id: 'equipment', label: 'Equipment', description: '设备', kind: 'concept' },
    { ...base, id: 'rated_power', label: 'Rated power', description: 'Equipment power', kind: 'predicate', config: { storage_mode: 'assertion' } },
    { ...base, id: 'equipment_name', label: 'Name', kind: 'predicate', lifecycle: 'draft', config: { storage_mode: 'attr' } },
  ];
  const all = { search: '', kind: 'all', lifecycle: 'all', storage: 'all' };
  const ids = filters => definitions.filter(definition => matchesDefinitionFilters(definition, { ...all, ...filters })).map(definition => definition.id);
  assert.deepEqual(ids({}), ['equipment', 'rated_power', 'equipment_name']);
  assert.deepEqual(ids({ kind: 'concept' }), ['equipment']);
  assert.deepEqual(ids({ search: '  EQUIPMENT ', kind: 'predicate', lifecycle: 'active', storage: 'assertion' }), ['rated_power']);
  assert.deepEqual(ids({ search: '设备' }), ['equipment']);
  assert.deepEqual(ids({ lifecycle: 'draft', storage: 'assertion' }), []);
  assert.deepEqual(ids({ search: 'not found' }), []);
});

test('filtered cards use a compact grid and preserve both global and temporary dragged positions', () => {
  const original = Array.from({ length: 34 }, (_, index) => ({ id: `p${index}`, position: { x: 2200, y: index * 212 }, data: {} }));
  const before = structuredClone(original);
  const result = layoutFilteredNodes(original, { width: 1100, height: 620 });
  assert.equal(new Set(result.map(node => `${node.position.x},${node.position.y}`)).size, 34);
  assert(new Set(result.map(node => node.position.x)).size > 1);
  assert(Math.max(...result.map(node => node.position.y)) < 1500);
  assert.deepEqual(original, before);
  assert.deepEqual(layoutFilteredNodes(original, { width: 1100, height: 620 }, { p0: { x: 15, y: 23 } })[0].position, { x: 15, y: 23 });
  assert.deepEqual(layoutFilteredNodes([], { width: 390, height: 740 }), []);
});

test('filtered viewport centers every result inside the free canvas on desktop, phone and overlay layouts', () => {
  for (const area of [
    { x: 76, y: 24, width: 1124, height: 624 },
    { x: 392, y: 24, width: 432, height: 624 },
    { x: 76, y: 24, width: 298, height: 748 },
  ]) {
    const nodes = layoutFilteredNodes(Array.from({ length: 34 }, (_, index) => ({ id: `p${index}`, position: { x: 2000, y: index * 1000 }, data: {} })), area);
    const view = canvasViewportForNodes(nodes, area);
    const bounds = nodes.map(node => ({ left: view.x + node.position.x * view.zoom, top: view.y + node.position.y * view.zoom, right: view.x + (node.position.x + 248) * view.zoom, bottom: view.y + (node.position.y + 160) * view.zoom }));
    assert(bounds.every(box => box.left >= area.x && box.right <= area.x + area.width && box.top >= area.y && box.bottom <= area.y + area.height));
    assert(Math.abs((Math.min(...bounds.map(box => box.left)) + Math.max(...bounds.map(box => box.right))) / 2 - (area.x + area.width / 2)) < .001);
    assert(Math.abs((Math.min(...bounds.map(box => box.top)) + Math.max(...bounds.map(box => box.bottom))) / 2 - (area.y + area.height / 2)) < .001);
  }
  const single = canvasViewportForNodes([{ id: 'one', position: { x: 10000, y: 20000 }, data: {} }], { x: 76, y: 24, width: 1000, height: 624 });
  assert.equal(single.zoom, .9);
  assert.equal(canvasViewportForNodes([], { x: 0, y: 0, width: 1000, height: 720 }), null);
});

test('focus layout fits types into compact vertical columns without changing global positions', () => {
  const global = [
    { id: 'concept:a', position: { x: 900, y: 200 }, data: { kind: 'concept' } },
    { id: 'predicate:p', position: { x: 2400, y: 900 }, data: { kind: 'predicate' } },
    { id: 'concept:b', position: { x: 900, y: 2400 }, data: { kind: 'concept' } },
    { id: 'relation:r', position: { x: 1800, y: 500 }, data: { kind: 'relation' } },
  ];
  const focused = layoutFocusedNodes(global, (node) => node.data.kind, whiteboardKinds, 'concept:b');
  const byId = Object.fromEntries(focused.map((node) => [node.id, node.position]));
  assert.equal(byId['concept:a'].x, byId['concept:b'].x);
  assert(byId['concept:b'].y < byId['concept:a'].y);
  assert(byId['concept:a'].x < byId['relation:r'].x);
  assert(byId['relation:r'].x < byId['predicate:p'].x);
  assert(Math.max(...focused.map((node) => node.position.x)) - Math.min(...focused.map((node) => node.position.x)) < 700);
  assert.deepEqual(global[2].position, { x: 900, y: 2400 });
  assert.deepEqual(layoutFocusedNodes(global, (node) => node.data.kind, whiteboardKinds, 'concept:b', { 'concept:a': { x: 42, y: 43 } })[0].position, { x: 42, y: 43 });
});

test('concept focus includes the opposite concept of each connected relation only', () => {
  const definitions = [
    { ...base, id: 'organization', kind: 'concept' },
    { ...base, id: 'market', kind: 'concept' },
    { ...base, id: 'product', kind: 'concept' },
    { ...base, id: 'task', kind: 'concept' },
    { ...base, id: 'participates_in', kind: 'relation', endpoints: [
      { sourceConceptId: 'organization', targetConceptId: 'market' },
      { sourceConceptId: 'product', targetConceptId: 'market' },
    ] },
    { ...base, id: 'finding', kind: 'predicate' },
  ];
  const edges = [
    { id: 'relation-source-participates_in-0', source: 'organization', target: 'participates_in' },
    { id: 'relation-target-participates_in-0', source: 'participates_in', target: 'market' },
    { id: 'relation-source-participates_in-1', source: 'product', target: 'participates_in' },
    { id: 'relation-target-participates_in-1', source: 'participates_in', target: 'market' },
    { id: 'd', source: 'organization', target: 'finding' },
  ];
  const focused = focusedDefinitionIds('organization', definitions, edges);
  assert.deepEqual([...focused].sort(), ['finding', 'market', 'organization', 'participates_in']);
  assert(!focused.has('product'));
  assert(!focused.has('task'));
  assert.deepEqual(focusedDefinitionEdges('organization', definitions, edges).map((edge) => edge.id).sort(), [
    'd', 'relation-source-participates_in-0', 'relation-target-participates_in-0',
  ]);
  assert.deepEqual([...focusedDefinitionIds('participates_in', definitions, edges)].sort(), ['market', 'organization', 'participates_in', 'product']);
});

test('marquee and modifier selections track all selected ontology cards', () => {
  assert.deepEqual(selectionAfterChanges(['a'], [{ type: 'select', id: 'a', selected: false }, { type: 'select', id: 'b', selected: true }, { type: 'select', id: 'c', selected: true }]), ['b', 'c']);
  assert.deepEqual(selectionAfterChanges(['a', 'b'], [{ type: 'select', id: 'b', selected: false }]), ['a']);
});

test('shared business scope and predicate inputs create one visible edge per id', () => {
  const edges = [
    { id: 'rule-business_scope-equipment', source: 'rule', target: 'equipment', role: 'subject' },
    { id: 'rule-business_scope-equipment', source: 'rule', target: 'equipment', role: 'candidate' },
    { id: 'rule-business_input-rated_power', source: 'rule', target: 'rated_power', role: 'subject' },
    { id: 'rule-business_input-rated_power', source: 'rule', target: 'rated_power', role: 'candidate' },
  ];
  assert.deepEqual(uniqueEdgesById(edges).map((edge) => edge.id), [
    'rule-business_scope-equipment', 'rule-business_input-rated_power',
  ]);
});

test('rebase preserves local edits while accepting independent server changes', () => {
  const local = { ...base, label: 'Local' };
  const remote = { ...base, description: 'Server' };
  const result = rebaseDraft([remote], [{ targetId: base.id, before: base, after: local, type: 'update' }]);
  assert.equal(result.definitions[0].label, 'Local');
  assert.equal(result.definitions[0].description, 'Server');
  assert.deepEqual(result.operations[0].before, remote);
});

test('same-field and deletion conflicts preserve inputs and reject rebase', () => {
  const operations = [{ targetId: base.id, before: base, after: { ...base, label: 'Local' } }];
  assert.throws(() => rebaseDraft([{ ...base, label: 'Remote' }], operations), /冲突/);
  assert.throws(() => rebaseDraft([], operations), /冲突/);
  assert.equal(operations[0].after.label, 'Local');
});

test('connection failures never enter demo mode', () => {
  assert.equal(connectionFailure(401), 'unauthorized');
  assert.equal(connectionFailure(403), 'forbidden');
  assert.equal(connectionFailure(503, 'BACKEND_NOT_CONFIGURED'), 'unconfigured');
  assert.equal(connectionFailure(502), 'offline');
});

test('wire contract rejects silently lost definitions and missing baseline', () => {
  const data = { contract_version: '3.4', registry_fingerprint: 'hash', definitions: [], changesets: [], coverage: {}, extensions: {}, capabilities: {} };
  assert(wireSnapshot.safeParse({ data }).success);
  assert(!wireSnapshot.safeParse({ data: { ...data, registry_fingerprint: undefined } }).success);
  assert(!wireSnapshot.safeParse({ data: { ...data, definitions: [{ ...base, kind: 'invented' }] } }).success);
  assert(wireSnapshot.safeParse({ data: { ...data, definitions: ['business_constraint', 'business_rule'].map((kind) => ({ ...base, kind, source_path: `control/${kind}.yaml` })) } }).success);
});

test('Studio proxy never substitutes a service token and rejects cross-site writes', async () => {
  const route = load('../app/api/knowledgeos/[...path]/route.ts', { 'cloudflare:workers': { env: { KNOWLEDGEOS_API_BASE_URL: 'https://backend.test', KNOWLEDGEOS_API_TOKEN: 'unused-admin-secret' } } });
  const context = { params: Promise.resolve({ path: ['v1', 'studio'] }) };
  const make = (headers = {}, method = 'GET') => ({ method, headers: new Headers(headers), nextUrl: new URL('https://studio.test/api/knowledgeos/v1/studio'), text: async () => '{}' });
  assert.equal((await route.GET(make(), context)).status, 401);
  const originalFetch = globalThis.fetch;
  let authorization;
  let redirect;
  globalThis.fetch = async (_url, init) => { authorization = init.headers.Authorization; redirect = init.redirect; return Response.json({ data: {} }); };
  try {
    assert.equal((await route.GET(make({ authorization: 'Bearer reader-personal' }), context)).status, 200);
    assert.equal(authorization, 'Bearer reader-personal');
    assert.equal(redirect, 'manual');
    globalThis.fetch = async () => new Response(null, { status: 302, headers: { Location: 'https://other.test/' } });
    assert.equal((await route.GET(make({ authorization: 'Bearer reader-personal' }), context)).status, 502);
    const writeContext = { params: Promise.resolve({ path: ['v1', 'propose'] }) };
    assert.equal((await route.POST(make({ authorization: 'Bearer user', origin: 'https://other.test', 'content-type': 'application/json' }, 'POST'), writeContext)).status, 403);
    globalThis.fetch = async (url, init) => { authorization = init.headers.Authorization; return Response.json({ data: { results: [] }, path: String(url) }); };
    for (const endpoint of ['preview', 'impact', 'evaluate']) {
      const businessContext = { params: Promise.resolve({ path: ['v1', 'business', endpoint] }) };
      assert.equal((await route.POST(make({ authorization: 'Bearer author', origin: 'https://studio.test', 'content-type': 'application/json' }, 'POST'), businessContext)).status, 200);
      assert.equal(authorization, 'Bearer author');
    }
    const extractionContext = { params: Promise.resolve({ path: ['v1', 'extraction', 'request'] }) };
    assert.equal((await route.POST(make({ authorization: 'Bearer agent', origin: 'https://studio.test', 'content-type': 'application/json' }, 'POST'), extractionContext)).status, 404);
  } finally { globalThis.fetch = originalFetch; }
});

test('Studio product surface excludes the knowledge extraction workspace', () => {
  const page = readFileSync(fileURLToPath(new URL('../app/ontology-workspace.tsx', import.meta.url)), 'utf8');
  const control = readFileSync(fileURLToPath(new URL('../app/control-center.tsx', import.meta.url)), 'utf8');
  assert(!page.includes('ExtractionCenter'));
  assert(!page.includes('知识抽取'));
  assert(!control.includes('onOpenExtraction'));
});

test('live metadata takes priority over demo content and personal credentials stay in request headers', async () => {
  const api = load('../app/knowledgeos-api.ts');
  const originalFetch = globalThis.fetch;
  const headers = [];
  globalThis.fetch = async (_url, init) => {
    headers.push(init.headers.Authorization);
    return Response.json({ data: { contract_version: '3.4', registry_fingerprint: 'current',
      definitions: [
        { ...base, id: 'organization', source_path: base.sourcePath, label: 'Live backend label', binding_usage: { finding: ['org:acme'] } },
        { ...base, id: 'supplies', kind: 'relation', source_path: base.sourcePath, endpoint_usage: { 'organization->product': ['org:acme → product:servo-module'] },
          reification: { node_type: 'relation', identity: 'required', properties: [{ predicate: 'lead_time_days', required: false, cardinality: 'inherit', group: 'measurement' }] } },
      ],
      changesets: [], coverage: {}, extensions: {}, capabilities: {} } });
  };
  try {
    api.setSessionToken('personal');
    const definitions = (await api.loadStudioSnapshot()).definitions;
    assert.equal(definitions[0].label, 'Live backend label');
    assert.deepEqual(definitions[0].bindingUsage.finding, ['org:acme']);
    assert.deepEqual(definitions[1].endpointUsage['organization->product'], ['org:acme → product:servo-module']);
    assert.equal(definitions[1].reification.properties[0].predicateId, 'lead_time_days');
    assert.equal(headers[0], 'Bearer personal');
    api.setSessionToken('');
    await api.loadStudioSnapshot();
    assert.equal(headers[1], undefined);
  } finally { globalThis.fetch = originalFetch; }
});
