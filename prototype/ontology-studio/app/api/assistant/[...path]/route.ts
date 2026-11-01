import type { NextRequest } from "next/server";
import { env } from "cloudflare:workers";

export const dynamic = "force-dynamic";

type RouteContext = { params: Promise<{ path?: string[] }> };

// The assistant gateway only drafts; it never writes. Adopted drafts are
// submitted through the KnowledgeOS proxy with the user's own token.
const allowedRoutes: Record<string, Set<string>> = {
  GET: new Set(["/v1/assistant/status"]),
  POST: new Set(["/v1/assistant/suggest"]),
};

function json(status: number, body: Record<string, unknown>) {
  return Response.json(body, { status, headers: { "Cache-Control": "no-store" } });
}

async function forward(request: NextRequest, context: RouteContext) {
  const segments = (await context.params).path ?? [];
  const path = `/${segments.map(encodeURIComponent).join("/")}`;
  if (!allowedRoutes[request.method]?.has(path)) return json(404, { error: "助手接口未通过 Studio 代理开放" });

  const baseUrl = env.KNOWLEDGEOS_ASSISTANT_BASE_URL?.trim();
  if (!baseUrl) return json(503, { error: "助手网关尚未配置；仍可手工编制并使用确定性检查。", code: "ASSISTANT_GATEWAY_NOT_CONFIGURED" });

  let target: URL;
  try {
    target = new URL(path, baseUrl.endsWith("/") ? baseUrl : `${baseUrl}/`);
  } catch {
    return json(500, { error: "KNOWLEDGEOS_ASSISTANT_BASE_URL 配置无效" });
  }
  const authorization = request.headers.get("authorization");
  if (path !== "/v1/assistant/status" && !authorization?.match(/^Bearer\s+\S+$/i)) return json(401, { error: "请使用个人访问凭证登录", code: "AUTHENTICATION_REQUIRED" });
  if (request.method === "POST") {
    const origin = request.headers.get("origin");
    if (origin && origin !== request.nextUrl.origin) return json(403, { error: "不允许跨站请求" });
    if (!request.headers.get("content-type")?.startsWith("application/json")) return json(415, { error: "需要 JSON 请求" });
  }

  try {
    const body = request.method === "GET" ? undefined : await request.text();
    if (body && new TextEncoder().encode(body).byteLength > 1_048_576) return json(413, { error: "请求超过 1 MiB" });
    const upstream = await fetch(target, {
      method: request.method,
      headers: { Accept: "application/json", ...(authorization ? { Authorization: authorization } : {}),
        ...(request.headers.get("content-type") ? { "Content-Type": request.headers.get("content-type")! } : {}) },
      body,
      // Model calls with bounded repair rounds can take a few minutes.
      signal: AbortSignal.timeout(300_000),
      redirect: "manual",
      cache: "no-store",
    });
    if (upstream.status >= 300 && upstream.status < 400) return json(502, { error: "助手网关返回了意外跳转", code: "ASSISTANT_REDIRECT" });
    return new Response(upstream.body, {
      status: upstream.status,
      headers: { "Content-Type": upstream.headers.get("content-type") ?? "application/json; charset=utf-8", "Cache-Control": "no-store" },
    });
  } catch (error) {
    console.error("Assistant gateway request failed:", error);
    return json(502, { error: "无法连接助手网关", code: "ASSISTANT_UNREACHABLE" });
  }
}

export { forward as GET, forward as POST };
