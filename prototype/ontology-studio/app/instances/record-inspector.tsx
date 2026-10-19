"use client";

import { NavigationToggle } from "../workspace-navigation-context";
import { useEffect, useRef, useState } from "react";
import { ChevronRight, Copy, Loader2, X } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { displayDate, displayValue, instanceKindLabels, instanceSourceLabels, instanceStatusLabels, loadRecordedInstance, type InstanceSelection, type RecordedInstanceDetail } from "./instances-api";

function record(value: unknown): Record<string, unknown> { return value && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : {}; }
function Properties({ values }: { values: Record<string, unknown> }) {
  return <dl className="property-list">{Object.entries(values).map(([label, value]) => <div key={label}><dt>{label}</dt><dd>{displayValue(value)}</dd></div>)}</dl>;
}

export function RecordInspector({ selection, onClose, onNavigate, revision }: { selection: InstanceSelection; onClose: () => void; onNavigate: (selection: InstanceSelection) => void; revision: number }) {
  const [detail, setDetail] = useState<RecordedInstanceDetail | null>(null);
  const [busy, setBusy] = useState(true);
  const [error, setError] = useState("");
  const [retry, setRetry] = useState(0);
  const closeRef = useRef<HTMLButtonElement>(null);
  useEffect(() => { closeRef.current?.focus({ preventScroll: true }); }, [selection.key]);
  useEffect(() => {
    const controller = new AbortController();
    // Reset the inspector before fetching a different external record snapshot.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setBusy(true); setError(""); setDetail(null);
    void loadRecordedInstance(selection.key, controller.signal).then((next) => { if (!controller.signal.aborted) setDetail(next); })
      .catch((cause) => { if (!controller.signal.aborted) setError(cause instanceof Error ? cause.message : "无法读取实例详情"); })
      .finally(() => { if (!controller.signal.aborted) setBusy(false); });
    return () => controller.abort();
  }, [selection.key, revision, retry]);
  async function copy() {
    try { await navigator.clipboard.writeText(selection.id); toast.success("已复制实例标识"); }
    catch { toast.error("无法复制，请手动选择标识"); }
  }
  const data = detail?.data ?? {};
  const temporal = record(data.temporal);
  const provenance = record(data.provenance);
  const summary = detail?.summary;
  return <aside className="instance-inspector" aria-label="实例详情" onKeyDown={(event) => { if (event.key === "Escape") { event.stopPropagation(); onClose(); } }}>
    <div className="inspector-topline"><span className="detail-navigation-access"><NavigationToggle /></span><span>{instanceKindLabels[selection.kind]}详情</span><div className="flex gap-1"><Button variant="ghost" size="icon-sm" aria-label="复制实例标识" onClick={() => void copy()}><Copy /></Button><Button ref={closeRef} variant="ghost" size="icon-sm" aria-label="关闭实例详情" onClick={onClose}><X /></Button></div></div>
    {busy ? <div className="instance-detail-empty" role="status"><Loader2 className="size-5 animate-spin" /><p>正在读取实例…</p></div> : error ? <div className="instance-detail-empty"><p role="alert">{error}</p><Button variant="outline" onClick={() => setRetry((value) => value + 1)}>重试</Button></div> : detail && summary && <>
      <div className="instance-detail-heading"><div className="flex flex-wrap gap-2"><span className={`instance-kind-badge instance-kind-${summary.kind}`}>{instanceKindLabels[summary.kind]}</span><span className="neutral-badge">{summary.type_label}</span></div><h2>{summary.label}</h2><code>{summary.id}</code></div>
      <div className="instance-detail-body border-t border-border">
        <section className="detail-section"><h3>{summary.kind === "relation" ? "关系信息" : summary.kind === "model" ? "运行与输出" : "判断信息"}</h3>
          {summary.kind === "relation" ? <Properties values={{ "关系类型": summary.type_label, "起点标识": data.src, "终点标识": data.dst, "状态": instanceStatusLabels[summary.lifecycle] ?? summary.lifecycle, "有效起始": displayDate(String(data.valid_from ?? "")), "有效结束": displayDate(String(data.valid_to ?? "")), "实体化关系": data.rel_id || "—", "权重": data.weight }} /> : summary.kind === "predicate" ? <Properties values={{ "记录形式": summary.record_kind === "attribute" ? "稳定属性" : "判断断言", "判断类型": summary.type_label, "值": data.value, "主体标识": data.node_id, "状态": instanceStatusLabels[summary.lifecycle] ?? summary.lifecycle, ...(summary.record_kind === "assertion" ? { "观测时间": displayDate(String(temporal.observed_at ?? "")), "有效起始": displayDate(String(temporal.valid_from ?? "")), "有效结束": displayDate(String(temporal.valid_to ?? "")), "溯源等级": provenance.tier, "限定条件": data.qualifiers } : {}) }} /> : <Properties values={{ "模型": summary.type_label, "模型版本": data.model_version, "场景": data.scenario, "状态": instanceStatusLabels[summary.lifecycle] ?? summary.lifecycle, "计算时间": displayDate(String(data.calculated_at ?? "")), "输出": data.output }} />}
        </section>
        {!!detail.references.length && <section className="detail-section"><h3>关联主体</h3>{detail.references.map((ref) => <button className="instance-relation" key={ref.key} onClick={() => onNavigate({ key: ref.key, id: ref.id, kind: "concept" })}><strong>{ref.label}</strong><code>{ref.id}</code><ChevronRight className="relation-chevron" size={15} /></button>)}</section>}
        {summary.kind === "model" && <section className="detail-section"><h3>计算依据</h3><details className="record-json"><summary>输入来源引用</summary><pre>{JSON.stringify(data.input_refs, null, 2)}</pre></details><details className="record-json"><summary>计算过程</summary><pre>{JSON.stringify(data.trace, null, 2)}</pre></details><details className="record-json"><summary>执行时的模型快照</summary><pre>{JSON.stringify(data.model_snapshot, null, 2)}</pre></details></section>}
        <section className="detail-section"><h3>来源与证据</h3><Properties values={{ "来源类别": instanceSourceLabels[summary.source_class] ?? summary.source_class, "来源位置": summary.source_path }} />
          {detail.source_refs.map((ref, index) => <pre className="record-source" key={`source-${index}`}>{typeof ref === "string" ? ref : JSON.stringify(ref, null, 2)}</pre>)}
          {detail.evidence_refs.map((ref, index) => <pre className="record-source" key={`evidence-${index}`}>{typeof ref === "string" ? ref : JSON.stringify(ref, null, 2)}</pre>)}
          {!detail.source_refs.length && !detail.evidence_refs.length && <p className="detail-empty-copy">此记录没有附带独立来源或证据引用，来源位置如上。</p>}
        </section>
        <section className="detail-section"><details className="record-json"><summary>完整记录</summary><pre>{JSON.stringify(data, null, 2)}</pre></details></section>
      </div>
    </>}
  </aside>;
}
