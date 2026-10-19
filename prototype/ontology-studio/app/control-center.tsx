"use client";

import Link from "next/link";
import type { ReactNode } from "react";
import { ArrowRight, CheckCircle2, ChevronDown, DatabaseZap, FileJson2, GitPullRequestArrow, Layers3, Route, ShieldCheck, Workflow, XCircle } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { ScrollArea } from "@/components/ui/scroll-area";
import { cn } from "@/lib/utils";
import { whiteboardKinds } from "./whiteboard-scope";
import { kindMeta, type DefinitionKind, type OntologyDefinition } from "./studio-data";
import type { ApiConnectionState, StudioMetadata } from "./knowledgeos-api";

const dimensions = [
  { key: "C", label: "概念", detail: "类型与稳定属性", kinds: ["concept", "predicate"] as DefinitionKind[], color: "var(--chart-1)" },
  { key: "R", label: "关系", detail: "有向端点与实体化", kinds: ["relation"] as DefinitionKind[], color: "var(--chart-3)" },
  { key: "L", label: "逻辑", detail: "模型、规则与约束", kinds: ["model", "business_rule", "business_constraint"] as DefinitionKind[], color: "var(--chart-5)" },
  { key: "T", label: "时态", detail: "生命周期与新鲜度", kinds: ["policy"] as DefinitionKind[], color: "var(--chart-1)" },
  { key: "P", label: "溯源", detail: "来源、证据与审计", kinds: ["policy", "connector"] as DefinitionKind[], color: "var(--chart-2)" },
];
const loopSteps = [
  { label: "Git 真源", detail: "control/ · connectors/", icon: FileJson2 },
  { label: "注册与校验", detail: "引用、形状、端点与模型", icon: ShieldCheck },
  { label: "编译与投影", detail: "SQLite 索引与实体卡", icon: DatabaseZap },
  { label: "统一 API", detail: "/v1/control · Knowledge API", icon: Route },
  { label: "治理发布", detail: "审核 → 应用真源 → 验证发布", icon: GitPullRequestArrow },
];

function DefinitionList({ title, subtitle, items, onOpen }: { title: string; subtitle: string; items: OntologyDefinition[]; onOpen: (id: string) => void }) {
  return <section className="panel-surface overflow-hidden">
    <div className="flex items-start justify-between gap-4 border-b border-border px-5 py-4">
      <div><h3 className="text-base font-semibold">{title}</h3><p className="mt-1 text-sm text-muted-foreground">{subtitle}</p></div>
      <Badge variant="secondary">{items.length}</Badge>
    </div>
    <div className="divide-y divide-border">
      {items.map((item) => <button key={item.id} disabled={!whiteboardKinds.includes(item.kind)} onClick={() => onOpen(item.id)} className="group flex w-full items-center gap-3 px-5 py-3.5 text-left enabled:hover:bg-accent/50 disabled:cursor-default">
        <span className="flex size-8 shrink-0 items-center justify-center rounded-lg text-xs font-semibold text-white" style={{ background: kindMeta[item.kind].color }}>{kindMeta[item.kind].short}</span>
        <span className="min-w-0 flex-1"><span className="block text-sm font-medium">{item.label}</span><span className="mt-0.5 block truncate font-mono text-xs text-muted-foreground" title={item.id}>{item.id}</span></span>
        <span className="shrink-0 text-xs text-muted-foreground">{whiteboardKinds.includes(item.kind) ? `${item.refs} 引用` : "控制定义"}</span>{whiteboardKinds.includes(item.kind) && <ArrowRight aria-hidden="true" className="size-4 text-muted-foreground group-hover:text-primary" />}
      </button>)}
      {!items.length && <p className="px-5 py-6 text-sm text-muted-foreground">暂无已加载的定义</p>}
    </div>
  </section>;
}

export function ControlCenter({ definitions, errors, metadata, connection, onOpenDefinition, onOpenKind, actions }: {
  actions?: ReactNode;
  definitions: OntologyDefinition[];
  errors: string[];
  metadata: StudioMetadata | null;
  connection: ApiConnectionState;
  onOpenDefinition: (id: string) => void;
  onOpenKind: (kind: DefinitionKind) => void;
}) {
  const byKind = (kind: DefinitionKind) => definitions.filter((item) => item.kind === kind);
  const expected = (kind: DefinitionKind, fallback: number) => metadata?.coverage[kind] ?? fallback;
  const coverage = [
    ["schema", 1], ["concept", 9], ["relation", 6], ["predicate", 16], ["policy", 4], ["model", 5],
    ["business_constraint", 0], ["business_rule", 0], ["unit", 8], ["currency", 4], ["domain", 3], ["connector", 1],
  ] as const;
  const complete = coverage.every(([kind, fallback]) => byKind(kind).length === expected(kind, fallback)) && errors.length === 0;
  const unavailable = !["connected", "demo"].includes(connection);

  return <div className="h-full min-h-0 bg-background">
    <ScrollArea className="h-full"><div className="mx-auto max-w-[1440px] space-y-6 p-5 pb-8 lg:p-8">
      <section>
        <div className="mb-2 flex items-center gap-2 text-sm text-muted-foreground"><span className="h-1.5 w-1.5 rounded-full bg-primary" />知识参考 · 注册目录与说明</div><div className="flex items-center gap-3"><h1 className="page-title">定义总览</h1><div className="ml-auto flex items-center gap-1">{actions}</div></div><p className="page-description mt-2">查阅概念、关系与逻辑定义，了解结构覆盖、语义约定和真源到发布的完整路径。</p>
      </section>

      <section aria-label="控制面状态" className="panel-surface grid divide-y divide-border sm:grid-cols-3 sm:divide-x sm:divide-y-0">
        <div className="px-6 py-5"><p className="text-sm text-muted-foreground">已注册定义</p><p className="data-number mt-2 text-3xl font-semibold">{definitions.length}<span className="ml-2 text-sm font-normal text-muted-foreground">项 / 12 类对象</span></p><p className="mt-2 text-xs text-muted-foreground">{connection === "connected" ? "API 实时同步" : connection === "demo" ? "当前为演示快照" : "等待连接 · 保留已有快照"}</p></div>
        <div className="px-6 py-5"><p className="text-sm text-muted-foreground">结构校验</p><div className={cn("mt-2 flex items-center gap-2 text-xl font-semibold", complete ? "text-emerald-700" : "text-amber-800")}>{complete ? <CheckCircle2 className="size-5" /> : <XCircle className="size-5" />}{complete ? "引用完整" : errors.length ? `${errors.length} 项待处理` : "等待完整数据"}</div><p className="mt-3 text-xs text-muted-foreground">契约版本 {metadata?.contractVersion ?? "—"} · 注册表覆盖与引用校验</p></div>
        <div className="border-l-4 border-l-primary px-6 py-5 sm:border-l-0"><p className="text-sm text-muted-foreground">持久变更</p><p className="mt-2 text-xl font-semibold">ChangeSet 治理</p><p className="mt-3 text-xs leading-5 text-muted-foreground">批准后应用真源，重新编译并登记修订，才完成发布。</p></div>
      </section>

      {errors.length > 0 && <section aria-label="待处理问题" className="rounded-xl border border-amber-200 bg-amber-50 px-5 py-4"><h2 className="text-sm font-semibold text-amber-900">{errors.length} 项结构问题需要处理</h2><ul className="mt-2 list-inside list-disc space-y-1 text-sm text-amber-900">{errors.map((error) => <li key={error}>{error}</li>)}</ul></section>}
        <section className="panel-surface overflow-hidden">
          <div className="flex items-center justify-between gap-3 border-b border-border px-5 py-4"><div><h2 className="text-base font-semibold">结构目录</h2><p className="mt-1 text-sm text-muted-foreground">覆盖状态与实际注册数量</p></div><Badge variant="outline">{complete ? "完整覆盖" : unavailable ? "等待同步" : "检查中"}</Badge></div>
          <div className="grid sm:grid-cols-2 xl:grid-cols-3">{coverage.map(([kind, fallback]) => {
            const actual = byKind(kind).length, count = expected(kind, fallback), ok = actual === count;
            return <button key={kind} disabled={!whiteboardKinds.includes(kind)} onClick={() => onOpenKind(kind)} title={whiteboardKinds.includes(kind) ? `维护${kindMeta[kind].label}` : "只读注册信息"} className="flex items-center gap-3 border-b border-border px-5 py-4 text-left enabled:hover:bg-accent/50 disabled:cursor-default">
              <span className="flex size-8 shrink-0 items-center justify-center rounded-lg text-xs font-semibold text-white" style={{ background: kindMeta[kind].color }}>{kindMeta[kind].short}</span>
              <span className="min-w-0 flex-1 text-sm font-medium">{kindMeta[kind].label}</span><span className="data-number text-base font-semibold">{actual}<span className="ml-1 text-xs font-normal text-muted-foreground">/ {count}</span></span>
              {ok ? <CheckCircle2 aria-label="覆盖完整" className="size-4 text-emerald-700" /> : <XCircle aria-label="覆盖不完整" className="size-4 text-amber-700" />}
            </button>;
          })}</div>
        </section>
      <details className="overview-details panel-surface group">
        <summary className="flex items-center justify-between gap-3 p-5"><div><h2 className="text-sm font-semibold">架构与注册详情</h2><p className="mt-1 text-xs text-muted-foreground">治理路径、语义覆盖及只读注册信息 · 按需展开</p></div><ChevronDown className="size-4 shrink-0 text-muted-foreground transition-transform group-open:rotate-180" /></summary>
        <div className="space-y-6 border-t border-border p-5">
        <section>
          <h2 className="flex items-center gap-2 text-base font-semibold"><Workflow className="size-4 text-primary" />真源到发布</h2><p className="mt-1 text-sm text-muted-foreground">可追踪的知识治理路径</p>
          <div className="mt-6 space-y-0">{loopSteps.map((step, index) => <div key={step.label} className="relative flex gap-3 pb-5 last:pb-0">{index < loopSteps.length - 1 && <span className="absolute left-4 top-8 h-[calc(100%-16px)] w-px bg-border" />}<span className="z-10 flex size-8 shrink-0 items-center justify-center rounded-lg border border-border bg-muted text-primary"><step.icon aria-hidden="true" className="size-4" /></span><div><p className="text-sm font-medium">{step.label}</p><p className="mt-1 text-xs text-muted-foreground">{step.detail}</p></div></div>)}</div>
        </section>

      <section className="panel-surface overflow-hidden"><div className="flex flex-wrap items-center justify-between gap-2 border-b border-border px-5 py-4"><h2 className="text-base font-semibold">语义覆盖</h2><p className="text-xs text-muted-foreground">C-R-L-T-P · 每个维度落到可注册、可校验的定义</p></div><div className="grid divide-y divide-border sm:grid-cols-5 sm:divide-x sm:divide-y-0">{dimensions.map((item) => <div key={item.key} className="p-5"><div className="flex items-center gap-2"><span className="text-lg font-semibold" style={{ color: item.color }}>{item.key}</span><h3 className="text-sm font-semibold">{item.label}</h3></div><p className="mt-2 text-xs text-muted-foreground">{item.detail}</p><p className="data-number mt-3 text-sm font-medium">{item.kinds.reduce((sum, kind) => sum + byKind(kind).length, 0)} 项定义</p></div>)}</div></section>

      <section className="grid gap-6 xl:grid-cols-2">
        <DefinitionList title="治理策略" subtitle="权威、新鲜度、溯源与维护预算" items={byKind("policy")} onOpen={onOpenDefinition} />
        <DefinitionList title="逻辑与领域" subtitle="确定性模型、业务约束、业务规则与工作领域" items={[...byKind("model"), ...byKind("business_constraint"), ...byKind("business_rule"), ...byKind("domain")]} onOpen={onOpenDefinition} />
        <DefinitionList title="契约与集成" subtitle="核心信封与连接器共享同一注册表" items={[...byKind("schema"), ...byKind("connector")]} onOpen={onOpenDefinition} />
        <section className="panel-surface p-5"><div className="flex items-center gap-3"><span className="flex size-10 items-center justify-center rounded-xl bg-accent text-primary"><Layers3 className="size-5" /></span><div><h3 className="text-base font-semibold">专家模板工作台</h3><p className="mt-1 text-sm text-muted-foreground">工艺编排、决策配置与成本验证</p></div></div><div className="mt-5 flex gap-6 border-y border-border py-4">{[["约束", metadata?.extensions.constraints ?? 0], ["规则", metadata?.extensions.rules ?? 0], ["技能", metadata?.extensions.skills ?? 0]].map(([label, count]) => <div key={String(label)}><p className="data-number text-xl font-semibold">{count}</p><p className="mt-1 text-xs text-muted-foreground">{label}</p></div>)}</div><Button asChild variant="outline" className="mt-5"><Link href="/templates">打开工艺工作台</Link></Button></section>
      </section>
        </div>
      </details>
    </div></ScrollArea>
  </div>;
}
