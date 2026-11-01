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
const read = relative => readFileSync(new URL(relative, import.meta.url), 'utf8');
const hosts = load('../app/agents/host-config.ts');
const info = { http_url: 'http://127.0.0.1:8791/mcp', api_base_url: 'http://127.0.0.1:8787', stdio: { command: '/Users/me/KnowledgeOS preview/.venv/bin/knowledgeos-mcp', args: ['--stdio'] } };
const token = 'kos_abc-DEF_123';

test('every first-phase host gets a ready-to-paste configuration for one agent', () => {
  assert.deepEqual(hosts.hosts.map(item => item.id), ['claude-code', 'claude-desktop', 'cursor', 'kimi-code', 'codex']);
  assert.equal(hosts.serverName('agent:rotary_actuator'), 'knowledgeos-rotary-actuator');
  const code = hosts.hostConfig('claude-code', 'agent:rotary', token, info);
  assert.equal(code.format, 'shell');
  assert.equal(code.text, "claude mcp add --scope user --transport http knowledgeos-rotary http://127.0.0.1:8791/mcp --header 'Authorization: Bearer kos_abc-DEF_123'");
  const desktop = JSON.parse(hosts.hostConfig('claude-desktop', 'agent:rotary', token, info).text);
  assert.deepEqual(desktop.mcpServers['knowledgeos-rotary'], { command: info.stdio.command, args: ['--stdio'], env: { KNOWLEDGEOS_TOKEN: token, KNOWLEDGEOS_API_BASE_URL: 'http://127.0.0.1:8787' } });
  for (const host of ['cursor', 'kimi-code']) {
    const config = hosts.hostConfig(host, 'agent:rotary', token, info);
    assert.deepEqual(JSON.parse(config.text), { mcpServers: { 'knowledgeos-rotary': { url: info.http_url, headers: { Authorization: `Bearer ${token}` } } } });
    assert.match(config.file, host === 'cursor' ? /\.cursor\/mcp\.json$/ : /\.kimi-code\/mcp\.json$/);
  }
  const codex = hosts.hostConfig('codex', 'agent:rotary', token, info);
  assert.equal(codex.file, '~/.codex/config.toml');
  assert.equal(codex.text, '[mcp_servers.knowledgeos-rotary]\ncommand = "/Users/me/KnowledgeOS preview/.venv/bin/knowledgeos-mcp"\nargs = ["--stdio"]\nenv = { KNOWLEDGEOS_TOKEN = "kos_abc-DEF_123", KNOWLEDGEOS_API_BASE_URL = "http://127.0.0.1:8787" }');
  for (const host of hosts.hosts) assert.ok(hosts.hostConfig(host.id, 'agent:x', token, info).steps.length >= 2, host.id);
});

test('agent helpers summarise roles, credentials and proposals', () => {
  const api = load('../app/agents/agents-api.ts', { '../knowledgeos-api': { ApiError: Error, request: async () => ({}) } });
  assert.equal(api.presetFor(['template_author', 'agent']).id, 'authoring');
  assert.equal(api.presetFor(['admin']), undefined);
  assert.equal(api.slugFromLabel('Rotary Actuator #2'), 'rotary-actuator-2');
  assert.equal(api.slugFromLabel('旋转执行器'), '');
  assert.deepEqual(api.activeCredentials({ credentials: [{ id: 'a', revoked_at: null }, { id: 'b', revoked_at: 'x' }] }).map(item => item.id), ['a']);
  assert.deepEqual(api.proposalSummary({ review_required: 2, proposed: 1, approved: 1, published: 2, rejected: 1, changes_requested: 1 }),
    { total: 8, pending: 3, approved: 3, rejected: 2 });
});

test('Studio exposes sign-in, agent and inbox routes; tokens never persist', () => {
  const proxy = read('../app/api/knowledgeos/[...path]/route.ts');
  for (const route of ['/v1/auth/login', '/v1/auth/setup', '/v1/auth/logout', '/v1/participants', '/v1/participants/credentials', '/v1/inbox', '/v1/events']) assert.ok(proxy.includes(`"${route}"`), route);
  assert.match(proxy, /publicRoutes = new Set\(\["\/health", "\/v1\/auth\/status", "\/v1\/auth\/login", "\/v1\/auth\/setup"\]\)/);
  assert.match(read('../app/api/mcp-info/route.ts'), /KNOWLEDGEOS_MCP_BASE_URL/);
  const nav = load('../app/workspace-navigation.ts');
  assert.equal(nav.workspaceForPath('/agents'), 'agents'); assert.equal(nav.workspaceForPath('/inbox'), 'inbox');
  for (const file of ['../app/account-dialog.tsx', '../app/agents/agents-workspace.tsx', '../app/knowledgeos-api.ts']) {
    assert.doesNotMatch(read(file), /localStorage|sessionStorage|document\.cookie/, file);
  }
  const workspace = read('../app/agents/agents-workspace.tsx');
  assert.match(workspace, /只显示这一次/);
  assert.match(read('../app/account-dialog.tsx'), /创建账号并登录/);
});
