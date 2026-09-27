"use client";

import {
  ArrowRight,
  Braces,
  CheckCircle2,
  CircleDot,
  DatabaseZap,
  FileJson2,
  GitPullRequestArrow,
  Network,
  Route,
  Scale,
  ShieldCheck,
  Sparkles,
  Workflow,
  XCircle,
} from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { ScrollArea } from "@/components/ui/scroll-area";
import { cn } from "@/lib/utils";
import { kindMeta, type DefinitionKind, type OntologyDefinition } from "./studio-data";
import type { ApiConnectionState, StudioMetadata } from "./knowledgeos-api";

const dimensions = [
  { key: "C", label: "概念", detail: "类型、稳定属性与节点形状", kinds: ["concept", "predicate"] as DefinitionKind[], color: "#2563eb" },
  { key: "R", label: "关系", detail: "有向端点、基数与实体化", kinds: ["relation"] as DefinitionKind[], color: "#d97706" },
  { key: "L", label: "逻辑", detail: "版本化公式、输入单位与追踪", kinds: ["model"] as DefinitionKind[], color: "#dc2626" },
  { key: "T", label: "时态", detail: "生命周期、新鲜度、历史与分层", kinds: ["policy"] as DefinitionKind[], color: "#0891b2" },
  { key: "P", label: "溯源", detail: "来源、证据、权威与审计", kinds: ["policy", "connector"] as DefinitionKind[], color: "#0f766e" },
];

const loopSteps = [
  { label: "Git 真源", detail: "control/ · connectors/", icon: FileJson2 },
  { label: "注册与校验", detail: "引用、形状、端点、模型", icon: ShieldCheck },
  { label: "编译与投影", detail: "SQLite 索引与实体卡", icon: DatabaseZap },
  { label: "统一 API", detail: "/v1/control · Knowledge API", icon: Route },
  { label: "治理变更", detail: "ChangeSet → 审核 → 真源验证发布", icon: GitPullRequestArrow },
];

function DefinitionList({ title, subtitle, items, onOpen }: { title: string; subtitle: string; items: OntologyDefinition[]; onOpen: (id: string) => void }) {
  return <section className="overflow-hidden rounded-2xl border border-[#d9dde5] bg-white shadow-sm">
    <div className="flex items-start justify-between gap-4 border-b border-[#e7e9ee] px-5 py-4">
      <div><h3 className="text-sm font-semibold text-slate-950">{title}</h3><p className="mt-1 text-xs leading-5 text-slate-500">{subtitle}</p></div>
      <Badge variant="outline" className="bg-slate-50">{items.length}</Badge>
    </div>
    <div className="divide-y divide-[#eceef2]">
      {items.map((item) => <button key={item.id} onClick={() => onOpen(item.id)} className="group flex w-full items-center gap-3 px-5 py-3 text-left hover:bg-slate-50">
        <span className="flex size-8 shrink-0 items-center justify-center rounded-lg text-[11px] font-bold text-white" style={{ background: kindMeta[item.kind].color }}>{kindMeta[item.kind].short}</span>
        <span className="min-w-0 flex-1"><span className="block truncate text-sm font-medium text-slate-900">{item.label}</span><span className="mt-0.5 block truncate font-mono text-[11px] text-slate-400">{item.id}</span></span>
        <span className="text-xs text-slate-400 group-hover:text-slate-700">{item.refs} 引用</span><ArrowRight className="size-4 text-slate-300 group-hover:text-slate-700" />
      </button>)}
    </div>
  </section>;
}

export function ControlCenter({ definitions, errors, metadata, connection, onOpenDefinition, onOpenStudio, onOpenExtraction, onOpenReview }: {
  definitions: OntologyDefinition[];
  errors: string[];
  metadata: StudioMetadata | null;
  connection: ApiConnectionState;
  onOpenDefinition: (id: string) => void;
  onOpenStudio: () => void;
  onOpenExtraction: () => void;
  onOpenReview: () => void;
}) {
  const byKind = (kind: DefinitionKind) => definitions.filter((item) => item.kind === kind);
  const expected = (kind: DefinitionKind, fallback: number) => metadata?.coverage[kind] ?? fallback;
  const coverage = [
    ["核心契约", byKind("schema").length, expected("schema", 1)],
    ["概念类型", byKind("concept").length, expected("concept", 7)],
    ["关系类型", byKind("relation").length, expected("relation", 6)],
    ["判断类型", byKind("predicate").length, expected("predicate", 9)],
    ["治理策略", byKind("policy").length, expected("policy", 4)],
    ["确定性模型", byKind("model").length, expected("model", 3)],
    ["工作领域", byKind("domain").length, expected("domain", 3)],
    ["连接器映射", byKind("connector").length, expected("connector", 1)],
  ] as const;
  const complete = coverage.every(([, actual, expected]) => actual === expected) && errors.length === 0;

  return <div className="h-full min-h-0 bg-[#eef1f6]">
    <ScrollArea className="h-full"><div className="mx-auto max-w-[1500px] space-y-6 p-5 pb-16 lg:p-8">
      <section className="overflow-hidden rounded-3xl border border-[#cad1dc] bg-[#111827] text-white shadow-xl">
        <div className="grid gap-8 p-6 lg:grid-cols-[1.2fr_.8fr] lg:p-8">
          <div>
            <div className="flex items-center gap-2 text-xs font-semibold uppercase tracking-[.18em] text-cyan-300"><CircleDot className="size-3.5" />Control Plane · Contract {metadata?.contractVersion ?? "3.2"}</div>
            <h1 className="mt-4 max-w-3xl text-2xl font-semibold tracking-tight sm:text-3xl">KnowledgeOS 完整结构与治理控制面</h1>
            <p className="mt-3 max-w-3xl text-sm leading-7 text-slate-300">以 Git 真源为起点统一管理本体、判断策略、确定性模型、领域行为、连接器映射和 ChangeSet。实例数据不在此处编辑。</p>
            <div className="mt-6 flex flex-wrap gap-3"><Button onClick={onOpenExtraction} className="bg-cyan-300 text-slate-950 hover:bg-cyan-200"><Sparkles />知识抽取</Button><Button onClick={onOpenStudio} variant="outline" className="border-white/20 bg-white/5 text-white hover:bg-white/10 hover:text-white"><Network />进入结构白板</Button><Button onClick={onOpenReview} variant="outline" className="border-white/20 bg-white/5 text-white hover:bg-white/10 hover:text-white"><GitPullRequestArrow />治理审核</Button></div>
          </div>
          <div className="grid grid-cols-2 gap-3">
            <div className="rounded-2xl border border-white/10 bg-white/5 p-4"><p className="text-xs text-slate-400">注册定义</p><p className="mt-2 text-3xl font-semibold">{definitions.length}</p><p className="mt-1 text-xs text-slate-400">{connection === "connected" ? "API 实时映射 · 8 类对象" : "演示快照 · 8 类对象"}</p></div>
            <div className="rounded-2xl border border-white/10 bg-white/5 p-4"><p className="text-xs text-slate-400">结构校验</p><p className={cn("mt-2 text-3xl font-semibold", complete ? "text-emerald-300" : "text-rose-300")}>{complete ? "闭环" : errors.length}</p><p className="mt-1 text-xs text-slate-400">{complete ? "全部引用可解析" : "项错误待处理"}</p></div>
            <div className="col-span-2 rounded-2xl border border-white/10 bg-white/5 p-4"><div className="flex items-center justify-between"><p className="text-xs text-slate-400">持久变更策略</p><Badge className="bg-amber-300 text-slate-950 hover:bg-amber-300">ChangeSet only</Badge></div><p className="mt-3 text-sm leading-6 text-slate-200">批准只记录治理决策；真实源更新、重新编译并登记 source revision 后才完成发布。</p></div>
          </div>
        </div>
      </section>

      <section className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">{coverage.map(([label, actual, expected]) => {
        const ok = actual === expected;
        return <div key={label} className="rounded-2xl border border-[#d9dde5] bg-white p-4 shadow-sm"><div className="flex items-center justify-between"><p className="text-sm font-medium text-slate-700">{label}</p>{ok ? <CheckCircle2 className="size-4 text-emerald-600" /> : <XCircle className="size-4 text-rose-600" />}</div><p className="mt-3 text-2xl font-semibold text-slate-950">{actual}<span className="text-sm font-normal text-slate-400"> / {expected}</span></p></div>;
      })}</section>

      <section className="grid gap-6 xl:grid-cols-[minmax(0,1fr)_390px]">
        <div className="rounded-3xl border border-[#d9dde5] bg-white p-5 shadow-sm sm:p-6">
          <div className="flex items-center gap-3"><span className="flex size-10 items-center justify-center rounded-xl bg-slate-950 text-white"><Braces className="size-5" /></span><div><h2 className="font-semibold text-slate-950">C-R-L-T-P 语义覆盖</h2><p className="mt-1 text-xs text-slate-500">每个维度都落到可注册、可校验的控制面对象。</p></div></div>
          <div className="mt-6 grid gap-3 md:grid-cols-5">{dimensions.map((item) => {
            const count = item.kinds.reduce((sum, kind) => sum + byKind(kind).length, 0);
            return <div key={item.key} className="rounded-2xl border border-slate-200 bg-slate-50/70 p-4"><span className="flex size-9 items-center justify-center rounded-xl text-sm font-bold text-white" style={{ background: item.color }}>{item.key}</span><p className="mt-4 text-sm font-semibold text-slate-900">{item.label}</p><p className="mt-1 min-h-10 text-xs leading-5 text-slate-500">{item.detail}</p><p className="mt-3 text-xs font-medium" style={{ color: item.color }}>{count} 项定义</p></div>;
          })}</div>
        </div>
        <div className="rounded-3xl border border-[#d9dde5] bg-white p-5 shadow-sm sm:p-6">
          <div className="flex items-center gap-3"><span className="flex size-10 items-center justify-center rounded-xl bg-cyan-700 text-white"><Workflow className="size-5" /></span><div><h2 className="font-semibold text-slate-950">结构闭环</h2><p className="mt-1 text-xs text-slate-500">从真源到治理发布的唯一通路。</p></div></div>
          <div className="mt-5 space-y-1">{loopSteps.map((step, index) => <div key={step.label} className="relative flex gap-3 pb-4 last:pb-0">{index < loopSteps.length - 1 && <span className="absolute left-[17px] top-9 h-[calc(100%-20px)] w-px bg-slate-200" />}<span className="z-10 flex size-9 shrink-0 items-center justify-center rounded-xl border border-slate-200 bg-white text-slate-700"><step.icon className="size-4" /></span><div><p className="text-sm font-semibold text-slate-900">{step.label}</p><p className="mt-0.5 text-xs text-slate-500">{step.detail}</p></div></div>)}</div>
        </div>
      </section>

      <section className="grid gap-6 xl:grid-cols-2">
        <DefinitionList title="治理策略" subtitle="权威、新鲜度、溯源与维护预算均已注册。" items={byKind("policy")} onOpen={onOpenDefinition} />
        <DefinitionList title="逻辑与领域" subtitle="领域包引用的模型、关系、判断类型与工具策略可追踪。" items={[...byKind("model"), ...byKind("domain")]} onOpen={onOpenDefinition} />
        <DefinitionList title="契约与集成" subtitle="Canonical Schema 和连接器映射共享同一注册表。" items={[...byKind("schema"), ...byKind("connector")]} onOpen={onOpenDefinition} />
        <section className="rounded-2xl border border-[#d9dde5] bg-white p-5 shadow-sm"><div className="flex items-center gap-3"><Scale className="size-5 text-slate-700" /><div><h3 className="text-sm font-semibold text-slate-950">扩展点状态</h3><p className="mt-1 text-xs text-slate-500">空目录是显式能力位，不伪造已实现定义。</p></div></div><div className="mt-5 grid grid-cols-3 gap-3">{[["Constraints", metadata?.extensions.constraints ?? 0], ["Rules", metadata?.extensions.rules ?? 0], ["Skills", metadata?.extensions.skills ?? 0]].map(([label, count]) => <div key={String(label)} className="rounded-xl border border-dashed border-slate-300 bg-slate-50 p-4 text-center"><p className="text-xl font-semibold text-slate-900">{count}</p><p className="mt-1 text-xs text-slate-500">{label}</p></div>)}</div></section>
      </section>
    </div></ScrollArea>
  </div>;
}
