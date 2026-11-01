import { env } from "cloudflare:workers";

export const dynamic = "force-dynamic";

function json(status: number, body: Record<string, unknown>) {
  return Response.json(body, { status, headers: { "Cache-Control": "no-store" } });
}

/** Connection facts for agent hosts (address and stdio command); contains no credentials. */
export async function GET() {
  const baseUrl = env.KNOWLEDGEOS_MCP_BASE_URL?.trim();
  if (!baseUrl) return json(503, { error: "MCP 服务尚未配置（KNOWLEDGEOS_MCP_BASE_URL）。", code: "MCP_NOT_CONFIGURED" });
  try {
    const response = await fetch(new URL("/info", baseUrl), { cache: "no-store", redirect: "manual", signal: AbortSignal.timeout(5_000) });
    if (!response.ok) return json(502, { error: `MCP 服务返回 ${response.status}`, code: "MCP_UNAVAILABLE" });
    return json(200, { data: await response.json() });
  } catch {
    return json(502, { error: "无法连接 MCP 服务，请确认它已启动。", code: "MCP_UNREACHABLE" });
  }
}
