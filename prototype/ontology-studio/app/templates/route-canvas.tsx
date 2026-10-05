"use client";

import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { applyNodeChanges, Background, BackgroundVariant, Controls, Handle, MarkerType, Position, ReactFlow, ReactFlowProvider, useStoreApi,
  type Connection, type Edge, type Node, type NodeProps, type ReactFlowInstance } from "@xyflow/react";
import { Copy, GitBranch, Layers3, Plus, SlidersHorizontal, ChevronDown, ChevronRight } from "lucide-react";
import { layoutRoute } from "./route-editing";
import { createMeasurementBatch } from "./measurement-batch";
import type { ExpertTemplate, OperationSummary } from "./template-api";

type StageData = {
  label: string; detail: string; basis: string; isGroup: boolean; editable: boolean; conditional: boolean;
  count?: number; collapsed: boolean; childCount: number; onToggleGroup: (id: string) => void;
  decision: boolean; inactive: boolean;
  onRename: (id: string, label: string) => void; onAppend: (id: string) => void;
  onInside: (id: string) => void; onDuplicate: (id: string) => void;
  onInspect: (id: string) => void;
};
type StageNode = Node<StageData, "stage">;
export type RouteFlow = ReactFlowInstance<StageNode>;
export type RoutePositions = Record<string, { x: number; y: number; parent: string | null }>;

function Stage({ id, data, selected }: NodeProps<StageNode>) {
  return <div data-testid={`route-card-${id}`} className={`relative h-full w-full rounded-2xl border-2 transition-colors [&:hover>.stage-tools]:opacity-100 ${data.inactive?'opacity-45':''} ${data.isGroup
    ? selected ? "border-amber-500 bg-amber-50/70" : "border-amber-200 bg-amber-50/60"
    : selected ? "border-indigo-500 bg-white shadow-[0_4px_24px_rgba(79,70,229,.15)]" : "border-slate-200 bg-white shadow-sm"}`}>
    <Handle type="target" position={Position.Left} className="!size-3 !border-2 !border-white !bg-slate-400" isConnectable={data.editable} />
    <Handle type="source" position={Position.Right} className="!size-3 !border-2 !border-white !bg-indigo-500" isConnectable={data.editable} />
    <div className={`flex items-center gap-2.5 px-4 pt-3.5 ${data.isGroup ? "pb-3" : ""}`}>
      <span className={`grid size-7 shrink-0 place-items-center rounded-lg ${data.isGroup ? "bg-amber-100 text-amber-700" : "bg-indigo-50 text-indigo-600"}`}>
        {data.isGroup ? <Layers3 className="size-4" /> : <GitBranch className="size-4" />}
      </span>
      <input aria-label={`工序名称 ${id}`} title={data.label} className={`nodrag min-w-0 flex-1 border-0 bg-transparent text-sm font-semibold outline-none ${data.editable ? "hover:bg-slate-50 focus:bg-slate-50" : ""}`}
        value={data.label} readOnly={!data.editable} onFocus={() => data.onInspect(id)} onChange={(event) => data.onRename(id, event.target.value)} />
      {data.isGroup && <button className="nodrag grid size-6 shrink-0 place-items-center rounded text-amber-700 hover:bg-amber-100" aria-label={`${data.collapsed ? "展开" : "折叠"}阶段 ${id}`} onClick={event => { event.stopPropagation(); data.onToggleGroup(id); }}>{data.collapsed ? <ChevronRight className="size-4" /> : <ChevronDown className="size-4" />}</button>}
      {data.conditional && <span title="按条件或决策输出启用" className="rounded bg-violet-100 px-1.5 py-0.5 text-[10px] text-violet-700">{data.decision?'决策':'条件'}</span>}
    </div>
    {data.isGroup ? <>
      <p className="px-4 text-[11px] text-amber-700">{data.detail}</p>
      {data.collapsed && <p className="mt-2 px-4 text-xs font-medium text-amber-700">{data.childCount} 道工序 · 点击箭头展开</p>}
      <span className="absolute bottom-3 left-4 text-[10px] text-amber-700/80">全部完成后汇合</span>
      {data.editable && <button aria-label={`添加组内工序 ${id}`} title="添加组内工序" onClick={event => { event.stopPropagation(); data.onInside(id); }}
        className="nodrag absolute bottom-2 right-3 flex items-center gap-1 rounded-lg bg-amber-100 px-2 py-1 text-[11px] text-amber-800 hover:bg-amber-200"><Plus className="size-3.5" /> 组内工序</button>}
    </> : <>
      <p className="truncate px-4 pt-2 text-xs text-slate-500" title={data.detail}>{data.detail}</p>
      <div className="flex items-center justify-between px-4 pt-2"><span className="rounded-md bg-slate-100 px-1.5 py-0.5 text-[10px] text-slate-500">{data.basis}</span>{data.count !== undefined ? <span className="rounded-md bg-emerald-50 px-1.5 py-0.5 text-[10px] font-semibold text-emerald-700">{data.count===0?'跳过':`× ${data.count}`}</span> : <span className="text-[10px] text-slate-300">{id.replace("step_", "#")}</span>}</div>
    </>}
    {data.editable && <div onClick={event => event.stopPropagation()} className={`stage-tools nodrag absolute -right-10 top-1/2 z-10 flex -translate-y-1/2 flex-col gap-1 rounded-xl border border-slate-200 bg-white p-1 shadow-sm ${selected ? "opacity-100" : "opacity-0 focus-within:opacity-100"}`}>
      <button aria-label={`追加下一工序 ${id}`} title="追加下一工序 · N" className="grid size-7 place-items-center rounded-lg text-indigo-600 hover:bg-indigo-50" onClick={() => data.onAppend(id)}><Plus className="size-4" /></button>
      <button aria-label={`复制工序 ${id}`} title="复制 · ⌘D" className="grid size-7 place-items-center rounded-lg text-slate-500 hover:bg-slate-100" onClick={() => data.onDuplicate(id)}><Copy className="size-3.5" /></button>
      <button aria-label={`工序属性 ${id}`} title="查看工序属性" className="grid size-7 place-items-center rounded-lg text-slate-500 hover:bg-slate-100" onClick={() => data.onInspect(id)}><SlidersHorizontal className="size-3.5" /></button>
    </div>}
  </div>;
}

const nodeTypes = { stage: Stage };

/** Install before React Flow creates its node ResizeObserver. Deferring only
 * onNodesChange is too late: React Flow already wrote its internal store inside
 * the observer, which can re-layout ancestors (especially when a sidebar opens).
 * This is scoped to this whiteboard's provider; other observers stay native.
 */
function MeasurementBoundary({ children }: { children: ReactNode }) {
  const store = useStoreApi<StageNode>();
  const [ready, setReady] = useState(false);
  type Updates = Parameters<ReturnType<typeof store.getState>["updateNodeInternals"]>[0];
  const pending = useRef<{ update: (updates: Updates) => void } | null>(null);
  // React Flow's observer captures this function when it mounts. Keep it stable
  // when effects reconnect (Strict Mode or hot reload), and replace its queue.
  const update = useCallback((updates: Updates) => pending.current?.update(updates), []);
  useEffect(() => {
    const original = store.getState().updateNodeInternals;
    const batch = createMeasurementBatch(original, requestAnimationFrame, cancelAnimationFrame);
    pending.current = batch;
    store.setState({ updateNodeInternals: update });
    const frame = requestAnimationFrame(() => setReady(true));
    return () => {
      cancelAnimationFrame(frame);
      batch.dispose();
      pending.current = null;
      if (store.getState().updateNodeInternals === update) store.setState({ updateNodeInternals: original });
    };
  }, [store, update]);
  return ready ? children : null;
}

export function RouteCanvas(props: Parameters<typeof RouteCanvasContent>[0]) {
  return <ReactFlowProvider><MeasurementBoundary><RouteCanvasContent {...props} /></MeasurementBoundary></ReactFlowProvider>;
}

function RouteCanvasContent({ template, operations, editable, selectedIds, selectedEdges, layoutRevision, restoredPositions, counts, resolvedOperations, collapsedGroups, onToggleGroup, onInspect, onSelect, onRename, onAppend,
  onInside, onDuplicate, onConnect, onDeleteEdge, onMove, onDragStart, onDragEnd, onDropOperation, onFlow }: {
  template: ExpertTemplate; operations: OperationSummary[]; editable: boolean; selectedIds: string[]; selectedEdges: string[]; layoutRevision: number;
  collapsedGroups: string[]; onToggleGroup: (id: string) => void;
  counts?: Record<string, number>;
  resolvedOperations?:Record<string,string[]>;
  restoredPositions: RoutePositions | null;
  onSelect: (ids: string[], edges: string[]) => void; onRename: (id: string, label: string) => void;
  onAppend: (id: string) => void; onInside: (id: string) => void; onDuplicate: (id: string) => void;
  onInspect: (id: string) => void;
  onConnect: (connection: Connection) => void; onDeleteEdge: (from: string, to: string) => void;
  onMove: (id: string, parent: string | null) => void;
  onDragStart: () => void; onDragEnd: () => void;
  onDropOperation: (ref: string, parent: string | null) => string | undefined; onFlow: (instance: ReactFlowInstance<StageNode>) => void;
}) {
  const boxes = useMemo(() => layoutRoute(template, collapsedGroups), [template, collapsedGroups]);
  const [nodes, setNodes] = useState<StageNode[]>([]);
  const revision = useRef(layoutRevision);
  const selection = useRef(selectedIds);
  const edgeSelection = useRef(selectedEdges);
  const flowRef = useRef<RouteFlow | null>(null);
  const containerRef = useRef<HTMLDivElement | null>(null);
  const dropPositions = useRef(new Map<string, { x: number; y: number }>());
  const structureKey = boxes.map((box) => `${box.id}:${box.parent}`).join("|");
  const graphKey = `${collapsedGroups.join(",")}|${structureKey}|${template.edges.map((edge) => `${edge.from}>${edge.to}`).join("|")}`;
  const lastGraph = useRef("");
  useEffect(() => { selection.current = selectedIds; edgeSelection.current = selectedEdges; }, [selectedIds, selectedEdges]);
  useEffect(() => {
    const frame = requestAnimationFrame(() => {
      const restore = revision.current !== layoutRevision;
      const reset = restore || lastGraph.current !== graphKey;
      if (restore) dropPositions.current.clear();
      const projected = boxes.map((box): StageNode => {
        const group = template.groups.find((item) => item.id === box.id), step = template.steps.find((item) => item.id === box.id);
        const operation = operations.find((item) => item.id === step?.operation_ref);
        const saved = restore && restoredPositions?.[box.id]?.parent === box.parent ? restoredPositions[box.id] : null;
        return { id: box.id, type: "stage", position: saved ? { x: saved.x, y: saved.y } : dropPositions.current.get(box.id) ?? { x: box.x, y: box.y }, parentId: box.parent ?? undefined,
          width: box.width, height: box.height, hidden: Boolean(box.parent && collapsedGroups.includes(box.parent)),
          style: { width: box.width, height: box.height }, selected: selectedIds.includes(box.id), draggable: editable,
          connectable: editable, zIndex: box.parent ? 2 : group ? 0 : 1,
          data: { collapsed: collapsedGroups.includes(box.id), childCount: template.steps.filter(step => step.parent === box.id).length, onToggleGroup, label: group?.label ?? step!.label, isGroup: Boolean(group), editable,
            detail: group ? group.group_mode==='conditional'?group.when||group.decision_binding?'按条件启用 · 整组执行一次':'整组执行一次':`${group.execution_mode==='sequential'?'逐个顺序加工':'逐个独立加工'} · ${group.iteration_set}` : resolvedOperations?.[step!.id]?.map(ref=>operations.find(o=>o.id===ref)?.label??ref).join(' / ')||operation?.label||step!.operation_ref,
            basis: step?.cost_basis ?? "", conditional: Boolean(group?.when ?? step?.when ?? group?.decision_binding ?? step?.decision_binding),
            decision:Boolean(group?.decision_binding??step?.decision_binding),
            inactive:Boolean(counts&&(step?(counts[step.id]??0)===0:template.steps.filter(s=>s.parent===group?.id).every(s=>(counts[s.id]??0)===0))),
            count: counts && step ? counts[step.id] ?? 0 : undefined,
            onRename, onAppend, onInside, onDuplicate, onInspect } };
      });
      setNodes((current) => projected.map((item) => {
        const previous = current.find((node) => node.id === item.id);
        return previous && !reset && previous.parentId === item.parentId
          ? { ...previous, ...item, position: previous.position, measured: previous.measured } : item;
      }));
      revision.current = layoutRevision; lastGraph.current = graphKey; dropPositions.current.clear();
    });
    return () => cancelAnimationFrame(frame);
  // Callbacks carry current template state, while positions stay local to the whiteboard.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [template, operations, editable, selectedIds, layoutRevision, restoredPositions, counts, resolvedOperations, collapsedGroups]);

  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;
    let timer: ReturnType<typeof setTimeout>;
    const fit = () => {
      const fullWidth = Math.max(0, ...boxes.filter((box) => !box.parent).map((box) => box.x + box.width));
      const needsFocus = fullWidth * 0.65 > container.clientWidth;
      const anchors = selection.current.length ? selection.current : boxes.filter((box) => !box.parent).slice(0, 2).map((box) => box.id);
      const focus = needsFocus ? [...new Set([...anchors, ...template.edges.filter((edge) => anchors.includes(edge.to)).map((edge) => edge.from)])].map((id) => ({ id })) : undefined;
      void flowRef.current?.fitView({ nodes: focus, padding: 0.2, minZoom: 0.55, maxZoom: 1, duration: 250 });
    };
    const schedule = () => { clearTimeout(timer); timer = setTimeout(fit, 100); };
    let width = container.clientWidth, height = container.clientHeight;
    const observer = new ResizeObserver(() => {
      if (width !== container.clientWidth || height !== container.clientHeight) {
        width = container.clientWidth; height = container.clientHeight; schedule();
      }
    });
    observer.observe(container); schedule();
    return () => { clearTimeout(timer); observer.disconnect(); };
  // New stages and window/sidebar resizing stay readable without moving during text edits.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [graphKey, layoutRevision]);

  const edges: Edge[] = useMemo(() => template.edges.map((edge) => ({ id: `${edge.from}>>${edge.to}`, source: edge.from, target: edge.to,
    hidden: [edge.from, edge.to].some(id => collapsedGroups.includes(template.steps.find(step => step.id === id)?.parent ?? "")),
    type: "smoothstep", markerEnd: { type: MarkerType.ArrowClosed, color: "#6366f1", width: 18, height: 18 },
    style: { stroke: "#818cf8", strokeWidth: 2 }, interactionWidth: 20, focusable: true,
    selected: selectedEdges.includes(`${edge.from}>>${edge.to}`) })), [template.edges, template.steps, selectedEdges, collapsedGroups]);

  return <div ref={containerRef} className="h-full w-full"><ReactFlow<StageNode> nodes={nodes} edges={edges} nodeTypes={nodeTypes} fitView fitViewOptions={{ padding: 0.25, maxZoom: 1 }}
    minZoom={0.15} maxZoom={1.8} onInit={(instance) => { flowRef.current = instance; onFlow(instance); }} onNodesChange={(changes) => {
      // Dimension notifications run inside ResizeObserver. Commit them in the
      // next frame so measuring nodes cannot trigger another layout in its callback.
      const dimensions = changes.filter((change) => change.type === "dimensions");
      const immediate = changes.filter((change) => change.type !== "dimensions");
      if (immediate.length) setNodes((current) => applyNodeChanges(immediate, current));
      if (dimensions.length) requestAnimationFrame(() => setNodes((current) => applyNodeChanges(dimensions, current)));
      const selected = changes.filter((change) => change.type === "select");
      if (selected.length) {
        const ids = new Set(selection.current);
        for (const change of selected) if (change.selected) ids.add(change.id); else ids.delete(change.id);
        selection.current = [...ids]; onSelect(selection.current, edgeSelection.current);
      }
    }}
    onEdgesChange={(changes) => {
      const selected = changes.filter((change) => change.type === "select");
      if (selected.length) {
        const ids = new Set(edgeSelection.current);
        for (const change of selected) if (change.selected) ids.add(change.id); else ids.delete(change.id);
        edgeSelection.current = [...ids]; onSelect(selection.current, edgeSelection.current);
      }
    }}
    onConnect={onConnect} onEdgeDoubleClick={(_, edge) => editable && onDeleteEdge(edge.source, edge.target)}
    onPaneClick={() => { selection.current = []; edgeSelection.current = []; onSelect([], []); }}
    onNodeClick={(event, node) => { if (!event.metaKey && !event.ctrlKey && !event.shiftKey) requestAnimationFrame(() => onInspect(node.id)); }}
    onNodeDoubleClick={(_, node) => onInspect(node.id)}
    onNodeDragStart={() => editable && onDragStart()}
    onNodeDragStop={(_, node) => {
      if (!editable) return;
      if (template.groups.some((group) => group.id === node.id)) { onDragEnd(); return; }
      const oldParent = nodes.find((item) => item.id === node.parentId);
      const center = { x: node.position.x + (oldParent?.position.x ?? 0) + 112, y: node.position.y + (oldParent?.position.y ?? 0) + 58 };
      const group = nodes.find((item) => item.data.isGroup && center.x > item.position.x && center.x < item.position.x + Number(item.style?.width)
        && center.y > item.position.y + 64 && center.y < item.position.y + Number(item.style?.height) - 30);
      const parent = group?.id ?? null;
      if (parent !== (node.parentId ?? null)) { dropPositions.current.delete(node.id); onMove(node.id, parent); }
      onDragEnd();
    }}
    onDragOver={(event) => { if (editable) { event.preventDefault(); event.dataTransfer.dropEffect = "copy"; } }}
    onDrop={(event) => {
      if (!editable || !flowRef.current) return; event.preventDefault();
      const ref = event.dataTransfer.getData("application/knowledgeos-operation"); if (!ref) return;
      const point = flowRef.current.screenToFlowPosition({ x: event.clientX, y: event.clientY });
      const group = nodes.find((node) => node.data.isGroup && point.x > node.position.x && point.x < node.position.x + Number(node.style?.width)
        && point.y > node.position.y + 64 && point.y < node.position.y + Number(node.style?.height) - 30);
      const id = onDropOperation(ref, group?.id ?? null);
      if (id) dropPositions.current.set(id, { x: point.x - (group?.position.x ?? 0), y: point.y - (group?.position.y ?? 0) });
    }}
    selectionOnDrag panOnDrag={[1, 2]} panOnScroll selectionKeyCode="Shift" multiSelectionKeyCode={["Meta", "Control", "Shift"]}
    nodesConnectable={editable} deleteKeyCode={null}
    className="bg-[#f7f8fb]" aria-label="工艺编排白板">
    <Background variant={BackgroundVariant.Dots} gap={22} size={1} color="#d6dce8" />
    <Controls showInteractive={false} position="bottom-right" />
  </ReactFlow></div>;
}
