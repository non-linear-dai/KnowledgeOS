import type { NextRequest } from "next/server";
import { env } from "cloudflare:workers";

export const dynamic = "force-dynamic";

type RouteContext = { params: Promise<{ path?: string[] }> };

const allowedRoutes: Record<string, Set<string>> = {
  GET: new Set(["/health", "/v1/studio", "/v1/changesets"]),
  POST: new Set(["/v1/propose", "/v1/changesets/review", "/v1/changesets/publish", "/v1/extraction/request", "/v1/extraction/candidates"]),
};

function json(status: number, body: Record<string, unknown>) {
  return Response.json(body, { status, headers: { "Cache-Control": "no-store" } });
}

async function forward(request: NextRequest, context: RouteContext) {
  const segments = (await context.params).path ?? [];
  const path = `/${segments.map(encodeURIComponent).join("/")}`;
  if (!allowedRoutes[request.method]?.has(path)) return json(404, { error: "KnowledgeOS API route is not exposed by the Studio proxy" });

  const baseUrl = env.KNOWLEDGEOS_API_BASE_URL?.trim();
  if (!baseUrl) return json(503, { error: "KnowledgeOS 后端尚未配置；当前界面使用只读演示数据。", code: "BACKEND_NOT_CONFIGURED" });

  let target: URL;
  try {
    target = new URL(path, baseUrl.endsWith("/") ? baseUrl : `${baseUrl}/`);
  } catch {
    return json(500, { error: "KNOWLEDGEOS_API_BASE_URL 配置无效" });
  }
  target.search = request.nextUrl.search;
  const apiToken = env.KNOWLEDGEOS_API_TOKEN?.trim();
  if (!apiToken) return json(503, { error: "KnowledgeOS 服务令牌尚未配置", code: "BACKEND_AUTH_NOT_CONFIGURED" });

  try {
    const upstream = await fetch(target, {
      method: request.method,
      headers: { Accept: "application/json", Authorization: `Bearer ${apiToken}`,
        ...(request.headers.get("content-type") ? { "Content-Type": request.headers.get("content-type")! } : {}) },
      body: request.method === "GET" ? undefined : await request.text(),
      cache: "no-store",
    });
    return new Response(upstream.body, {
      status: upstream.status,
      headers: { "Content-Type": upstream.headers.get("content-type") ?? "application/json; charset=utf-8", "Cache-Control": "no-store" },
    });
  } catch {
    return json(502, { error: "无法连接 KnowledgeOS 后端", code: "BACKEND_UNREACHABLE" });
  }
}

export { forward as GET, forward as POST };
