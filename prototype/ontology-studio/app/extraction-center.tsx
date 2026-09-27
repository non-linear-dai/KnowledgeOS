"use client";

import { useMemo, useState } from "react";
import {
  AlertCircle,
  ArrowRight,
  Braces,
  CheckCircle2,
  Clipboard,
  FileSearch,
  Fingerprint,
  LoaderCircle,
  ShieldCheck,
  Sparkles,
} from "lucide-react";
import { toast } from "sonner";

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Textarea } from "@/components/ui/textarea";
import {
  createExtractionCandidates,
  createExtractionRequest,
  type ExtractionCandidates,
  type ExtractionRequest,
  type ExtractionSourceKind,
} from "./knowledgeos-api";

const sourceKinds: { value: ExtractionSourceKind; label: string; hint: string }[] = [
  { value: "text", label: "文本", hint: "文章、笔记或任意纯文本" },
  { value: "web", label: "网页", hint: "填写 URL，并粘贴网页正文" },
  { value: "file", label: "文件", hint: "填写文件名，并粘贴解析后的正文" },
  { value: "audio", label: "语音", hint: "填写录音来源，并粘贴转写文本" },
  { value: "meeting_minutes", label: "会议纪要", hint: "会议纪要或转写后的发言内容" },
  { value: "image", label: "图片", hint: "填写图片来源，并粘贴 OCR / 视觉描述" },
  { value: "video", label: "视频", hint: "填写视频来源，并粘贴字幕或转写" },
  { value: "email", label: "邮件", hint: "邮件正文与必要的头部信息" },
  { value: "chat", label: "对话", hint: "聊天或客服记录" },
];

function pretty(value: unknown) {
  return JSON.stringify(value, null, 2);
}

async function copyJson(value: unknown, label: string) {
  await navigator.clipboard.writeText(pretty(value));
  toast.success(`${label}已复制`);
}

export function ExtractionCenter() {
  const [kind, setKind] = useState<ExtractionSourceKind>("web");
  const [locator, setLocator] = useState("https://zh.wikipedia.org/wiki/特斯拉公司");
  const [content, setContent] = useState("");
  const [request, setRequest] = useState<ExtractionRequest | null>(null);
  const [modelOutput, setModelOutput] = useState("");
  const [result, setResult] = useState<ExtractionCandidates | null>(null);
  const [busy, setBusy] = useState<"request" | "candidates" | null>(null);
  const [error, setError] = useState("");

  const sourceKind = sourceKinds.find((item) => item.value === kind)!;
  const segmentCount = Array.isArray(request?.source?.segments) ? request.source.segments.length : 0;
  const fingerprint = typeof request?.registry_fingerprint === "string" ? request.registry_fingerprint : "";
  const counts = useMemo(() => ({
    candidates: Array.isArray(result?.candidates) ? result.candidates.length : 0,
    rejected: Array.isArray(result?.rejected) ? result.rejected.length : 0,
    unmapped: Array.isArray(result?.unmapped_facts) ? result.unmapped_facts.length : 0,
  }), [result]);

  const prepare = async () => {
    if (!content.trim()) {
      setError("请先粘贴已经转成文本的来源内容。网页、语音和文件的抓取/转写属于上游 materializer。");
      return;
    }
    setBusy("request");
    setError("");
    setResult(null);
    try {
      const next = await createExtractionRequest({ kind, locator: locator.trim() || `inline:${kind}`, content });
      setRequest(next);
      setModelOutput(pretty({
        protocol_version: next.protocol_version,
        registry_fingerprint: next.registry_fingerprint,
        entities: [],
        unmapped_facts: [],
      }));
      toast.success("已生成与当前本体绑定的抽取契约");
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "生成抽取契约失败");
    } finally {
      setBusy(null);
    }
  };

  const finalize = async () => {
    if (!request) return;
    setBusy("candidates");
    setError("");
    try {
      const parsed = JSON.parse(modelOutput) as Record<string, unknown>;
      const next = await createExtractionCandidates(request, parsed);
      setResult(next);
      toast.success("候选已完成确定性校验；未写入知识库");
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "候选校验失败");
    } finally {
      setBusy(null);
    }
  };

  return <div className="h-full min-h-0 bg-[#eef1f6]">
    <ScrollArea className="h-full">
      <div className="mx-auto max-w-[1500px] space-y-6 p-5 pb-20 lg:p-8">
        <section className="overflow-hidden rounded-3xl border border-[#cad1dc] bg-[#111827] text-white shadow-xl">
          <div className="grid gap-6 p-6 lg:grid-cols-[1fr_420px] lg:p-8">
            <div>
              <div className="flex items-center gap-2 text-xs font-semibold uppercase tracking-[.18em] text-cyan-300"><Sparkles className="size-3.5" />Candidate-only extraction</div>
              <h1 className="mt-4 text-2xl font-semibold tracking-tight sm:text-3xl">知识抽取工作台</h1>
              <p className="mt-3 max-w-3xl text-sm leading-7 text-slate-300">把网页、文件、语音转写或会议纪要统一封装为来源信封，再依据当前注册表动态生成模型契约。模型只负责抽取；类型、字段、证据和增量匹配由 KnowledgeOS 确定性校验。</p>
            </div>
            <div className="grid grid-cols-3 gap-3 self-end">
              {[['1', '来源信封'], ['2', '模型抽取'], ['3', '候选校验']].map(([step, label]) => <div key={step} className="rounded-2xl border border-white/10 bg-white/5 p-3"><span className="text-xs text-cyan-300">步骤 {step}</span><p className="mt-1 text-sm font-semibold">{label}</p></div>)}
            </div>
          </div>
        </section>

        {error && <Alert variant="destructive" className="bg-white"><AlertCircle className="size-4" /><AlertTitle>操作未完成</AlertTitle><AlertDescription>{error}</AlertDescription></Alert>}

        <div className="grid gap-6 xl:grid-cols-[minmax(0,1fr)_minmax(420px,.8fr)]">
          <section className="rounded-3xl border border-[#d9dde5] bg-white p-5 shadow-sm sm:p-6">
            <div className="flex items-start justify-between gap-4">
              <div><h2 className="flex items-center gap-2 font-semibold text-slate-950"><FileSearch className="size-5 text-cyan-700" />1. 准备来源</h2><p className="mt-1 text-xs leading-5 text-slate-500">这里只生成候选，不会修改知识库或创建 ChangeSet。</p></div>
              <Badge variant="outline" className="bg-cyan-50 text-cyan-800">{sourceKind.label}</Badge>
            </div>
            <div className="mt-6 grid gap-5 sm:grid-cols-[180px_minmax(0,1fr)]">
              <div className="space-y-2"><Label>来源类型</Label><Select value={kind} onValueChange={(value) => { setKind(value as ExtractionSourceKind); setRequest(null); setResult(null); }}><SelectTrigger className="w-full"><SelectValue /></SelectTrigger><SelectContent>{sourceKinds.map((item) => <SelectItem key={item.value} value={item.value}>{item.label}</SelectItem>)}</SelectContent></Select></div>
              <div className="space-y-2"><Label>来源定位符</Label><Input value={locator} onChange={(event) => { setLocator(event.target.value); setRequest(null); setResult(null); }} placeholder="URL、文件名、录音 ID 或会议 ID" /></div>
            </div>
            <div className="mt-5 space-y-2"><div className="flex items-center justify-between gap-3"><Label>已物化的文本内容</Label><span className="text-[11px] text-slate-400">{content.length.toLocaleString()} 字符</span></div><Textarea value={content} onChange={(event) => { setContent(event.target.value); setRequest(null); setResult(null); }} className="min-h-[260px] resize-y font-mono text-xs leading-6" placeholder={`${sourceKind.hint}。\n\n例如网页测试：在“来源定位符”填写 URL，并把网页正文粘贴到这里。`} /></div>
            <div className="mt-5 flex flex-wrap items-center justify-between gap-3"><p className="max-w-xl text-xs leading-5 text-slate-500">来源正文会被规范化、分段并计算内容哈希；契约在每次操作时从当前本体、predicate、schema 与策略动态生成。</p><Button onClick={prepare} disabled={busy !== null || !content.trim()} className="bg-slate-950 hover:bg-slate-800">{busy === "request" ? <LoaderCircle className="animate-spin" /> : <Braces />}生成抽取契约<ArrowRight /></Button></div>
          </section>

          <section className="rounded-3xl border border-[#d9dde5] bg-white p-5 shadow-sm sm:p-6">
            <div className="flex items-start justify-between gap-4"><div><h2 className="flex items-center gap-2 font-semibold text-slate-950"><Fingerprint className="size-5 text-violet-700" />2. 调用模型</h2><p className="mt-1 text-xs leading-5 text-slate-500">模型/平台只需接收请求 JSON，并按 output_schema 返回 JSON。</p></div>{request ? <Badge className="bg-emerald-100 text-emerald-800 hover:bg-emerald-100">契约就绪</Badge> : <Badge variant="outline">等待来源</Badge>}</div>
            {request ? <>
              <div className="mt-5 grid grid-cols-2 gap-3"><div className="rounded-2xl border bg-slate-50 p-4"><p className="text-xs text-slate-500">来源分段</p><p className="mt-2 text-xl font-semibold">{segmentCount}</p></div><div className="rounded-2xl border bg-slate-50 p-4"><p className="text-xs text-slate-500">注册表指纹</p><p className="mt-2 truncate font-mono text-xs" title={fingerprint}>{fingerprint.slice(0, 16)}…</p></div></div>
              <div className="mt-4 flex flex-wrap gap-2"><Button variant="outline" size="sm" onClick={() => void copyJson(request, "模型请求")}><Clipboard />复制模型请求</Button><Button variant="outline" size="sm" onClick={() => void copyJson(request.output_schema, "输出 Schema")}><ShieldCheck />复制输出 Schema</Button></div>
              <div className="mt-5 space-y-2"><Label>模型输出 JSON</Label><Textarea value={modelOutput} onChange={(event) => { setModelOutput(event.target.value); setResult(null); }} className="min-h-[250px] resize-y font-mono text-xs leading-5" spellCheck={false} /></div>
              <Button onClick={finalize} disabled={busy !== null || !modelOutput.trim()} className="mt-4 w-full bg-violet-700 hover:bg-violet-600">{busy === "candidates" ? <LoaderCircle className="animate-spin" /> : <CheckCircle2 />}校验并生成候选</Button>
            </> : <div className="mt-6 flex min-h-[390px] items-center justify-center rounded-2xl border border-dashed bg-slate-50/60 p-8 text-center"><div><Braces className="mx-auto size-8 text-slate-300" /><p className="mt-4 text-sm font-medium text-slate-600">先从左侧生成抽取契约</p><p className="mt-2 max-w-sm text-xs leading-5 text-slate-400">本体更新后，新请求会自动获得新的注册表指纹和输出 Schema，因此不依赖某个固定模型。</p></div></div>}
          </section>
        </div>

        {result && <section className="rounded-3xl border border-[#d9dde5] bg-white p-5 shadow-sm sm:p-6">
          <div className="flex flex-wrap items-center justify-between gap-4"><div><h2 className="flex items-center gap-2 font-semibold text-slate-950"><CheckCircle2 className="size-5 text-emerald-600" />3. 候选结果</h2><p className="mt-1 text-xs text-slate-500">确定性校验已完成，write_performed = false。</p></div><Button variant="outline" size="sm" onClick={() => void copyJson(result, "候选结果")}><Clipboard />复制结果</Button></div>
          <div className="mt-5 grid gap-3 sm:grid-cols-3">{[["有效候选", counts.candidates, "text-emerald-700"], ["拒绝项", counts.rejected, "text-rose-700"], ["未映射事实", counts.unmapped, "text-amber-700"]].map(([label, count, color]) => <div key={String(label)} className="rounded-2xl border bg-slate-50 p-4"><p className="text-xs text-slate-500">{label}</p><p className={`mt-2 text-2xl font-semibold ${color}`}>{count}</p></div>)}</div>
          <pre className="mt-5 max-h-[520px] overflow-auto rounded-2xl bg-slate-950 p-5 text-xs leading-6 text-cyan-100">{pretty(result)}</pre>
        </section>}
      </div>
    </ScrollArea>
  </div>;
}
