/** Connection settings for each supported MCP host (pure; unit tested). */

export type HostId = "claude-code" | "claude-desktop" | "cursor" | "kimi-code" | "codex";
export type McpInfo = { http_url: string; api_base_url: string; stdio: { command: string; args: string[] } };
export type HostConfig = { format: "shell" | "json" | "toml"; file?: string; text: string; steps: string[] };

export const hosts: { id: HostId; label: string; transport: "HTTP" | "stdio"; note: string }[] = [
  { id: "claude-code", label: "Claude Code", transport: "HTTP", note: "命令行，本机 HTTP 直连" },
  { id: "claude-desktop", label: "Claude 桌面版", transport: "stdio", note: "由桌面版启动本机适配进程" },
  { id: "cursor", label: "Cursor", transport: "HTTP", note: "本机 HTTP 直连" },
  { id: "kimi-code", label: "Kimi Code", transport: "HTTP", note: "桌面版与命令行共用配置" },
  { id: "codex", label: "Codex", transport: "stdio", note: "OpenAI 模型；由 Codex 启动本机适配进程" },
];
export const hostLabels = Object.fromEntries(hosts.map(host => [host.id, host.label])) as Record<HostId, string>;

/** Server name shown in the host; one entry per agent so several agents can coexist. */
export function serverName(agentId: string) {
  return `knowledgeos-${agentId.replace(/^agent:/, "").replace(/[^a-z0-9-]/gi, "-")}`;
}

function quote(value: string) {
  return /^[A-Za-z0-9_./:@-]+$/.test(value) ? value : `'${value.replace(/'/g, `'\\''`)}'`;
}

function tomlString(value: string) {
  return JSON.stringify(value);
}

export function hostConfig(host: HostId, agentId: string, token: string, info: McpInfo): HostConfig {
  const name = serverName(agentId);
  const http = { url: info.http_url, headers: { Authorization: `Bearer ${token}` } };
  const stdio = { command: info.stdio.command, args: info.stdio.args, env: { KNOWLEDGEOS_TOKEN: token, KNOWLEDGEOS_API_BASE_URL: info.api_base_url } };
  switch (host) {
    case "claude-code":
      return { format: "shell", text: `claude mcp add --scope user --transport http ${name} ${quote(info.http_url)} --header ${quote(`Authorization: Bearer ${token}`)}`,
        steps: ["在终端运行这条命令（--scope user 表示所有项目可用）。", `运行 claude mcp list，看到 ${name} 显示 Connected 即可。`, "在 Claude Code 中输入 /mcp 可查看工具与提示词。"] };
    case "claude-desktop":
      return { format: "json", file: "~/Library/Application Support/Claude/claude_desktop_config.json", text: JSON.stringify({ mcpServers: { [name]: stdio } }, null, 2),
        steps: ["Claude 桌面版 › 设置 › 开发者 › 编辑配置，打开此文件。", `把 ${name} 合并进已有的 mcpServers（不要覆盖其他条目）。`, "完全退出并重新打开 Claude 桌面版，在输入框的工具菜单中看到 knowledgeos 工具即可。"] };
    case "cursor":
      return { format: "json", file: "~/.cursor/mcp.json", text: JSON.stringify({ mcpServers: { [name]: http } }, null, 2),
        steps: ["打开（或新建）此文件，把条目合并进 mcpServers。", "Cursor › Settings › MCP 中确认显示为已连接（绿色）。"] };
    case "kimi-code":
      return { format: "json", file: "~/.kimi-code/mcp.json", text: JSON.stringify({ mcpServers: { [name]: http } }, null, 2),
        steps: ["打开（或新建）此文件，把条目合并进 mcpServers；Kimi Code 桌面版与命令行共用这份配置。", "新建会话后生效；命令行中可用 /mcp 查看连接状态。"] };
    case "codex": {
      const env = Object.entries(stdio.env).map(([key, value]) => `${key} = ${tomlString(value)}`).join(", ");
      return { format: "toml", file: "~/.codex/config.toml", text: [`[mcp_servers.${name}]`, `command = ${tomlString(stdio.command)}`,
        `args = [${stdio.args.map(tomlString).join(", ")}]`, `env = { ${env} }`].join("\n"),
        steps: ["把这段追加到 Codex 配置文件末尾。", `运行 codex mcp list 确认出现 ${name}。`] };
    }
  }
}
