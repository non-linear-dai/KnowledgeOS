"use client";

import Link from "next/link";
import { useEffect, useMemo, useRef, useState } from "react";
import {
  BaseEdge,
  applyNodeChanges,
  Background,
  Controls,
  EdgeLabelRenderer,
  Handle,
  MarkerType,
  NodeToolbar,
  Panel,
  Position,
  ReactFlow,
  ReactFlowProvider,
  type Edge,
  type EdgeProps,
  type Node,
  type NodeChange,
  type NodeProps,
  type ReactFlowInstance,
} from "@xyflow/react";
import {
  AlertTriangle,
  ArrowRight,
  BookOpen,
  Braces,
  Check,
  CheckCircle2,
  Code2,
  FileCode2,
  GitCompareArrows,
  GitPullRequestArrow,
  GripVertical,
  Focus,
  LayoutGrid,
  Layers3,
  Link2,
  ListChecks,
  MousePointer2,
  Network,
  PanelLeftClose,
  PanelLeftOpen,
  PanelRightClose,
  PencilLine,
  Plus,
  PlusCircle,
  RefreshCw,
  RotateCcw,
  Scan,
  Search,
  Send,
  ShieldCheck,
  Trash2,
  Unlink,
  Undo2,
  X,
  XCircle,
} from "lucide-react";
import { toast } from "sonner";

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Command, CommandDialog, CommandEmpty, CommandGroup, CommandInput, CommandItem, CommandList, CommandShortcut } from "@/components/ui/command";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { ScrollArea } from "@/components/ui/scroll-area";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Textarea } from "@/components/ui/textarea";
import { Toaster } from "@/components/ui/sonner";
import { TooltipProvider } from "@/components/ui/tooltip";
import { cn } from "@/lib/utils";
import { conceptOptions, currencyOptions, dimensionOptions, modelUnitOptions, outputPredicateOptions, physicalUnitOptions, type RegistryOption } from "./registered-options";
import {
  authorityOptions,
  freshnessOptions,
  initialChangeSets,
  initialDefinitions,
  initialEdges,
  kindMeta,
  provenanceOptions,
  type ChangeOperation,
  type ChangeOperationType,
  type ChangeSet,
  type ChangeSetStatus,
  type ConfigValue,
  type ConceptBinding,
  type DefinitionKind,
  type Lifecycle,
  type OntologyDefinition,
  type RelationEndpoint,
  type RelationMode,
} from "./studio-data";
import { ControlCenter } from "./control-center";
import { BusinessEditor } from "./business-editor";
import { rebaseDraft } from "./draft-state";
import { reconcileCanvasNodes, selectionAfterChanges, uniqueEdgesById } from "./canvas-state";
import { whiteboardDefinitions, whiteboardKinds } from "./whiteboard-scope";
import {
  applyChangeSet,
  loadStudioSnapshot,
  proposeChangeSet,
  previewModel,
  loadModelHistory,
  type ModelRevision,
  publishChangeSet,
  reviewChangeSet,
  ApiError, setSessionToken, loadSession, type UserSession,
  type ApiConnectionState,
  type StudioMetadata,
} from "./knowledgeos-api";

type StudioNodeData = {
  definition: OntologyDefinition;
  dimmed: boolean;
  linked: boolean;
  onShowRelated: (id: string) => void;
  onManageBindings: (id: string) => void;
  onManageEndpoints: (id: string) => void;
};
type StudioNode = Node<StudioNodeData, "studio">;
type EdgeSide = "left" | "right" | "top" | "bottom";
type StudioEdgeData = {
  active: boolean;
  label?: string;
  sourceSide: EdgeSide;
  targetSide: EdgeSide;
  sourceOffset: number;
  targetOffset: number;
};
type StudioEdge = Edge<StudioEdgeData, "dependencyCurve">;
type ModelContextRegistration = {
  registerTool: (
    tool: {
      name: string;
      description: string;
      inputSchema: Record<string, unknown>;
      annotations?: Record<string, boolean>;
      execute: (input: Record<string, unknown>) => unknown;
    },
    options?: { signal?: AbortSignal },
  ) => Promise<void>;
};

const editableKinds: DefinitionKind[] = ["concept", "relation", "predicate", "model", "business_constraint", "business_rule"];
const allKinds = whiteboardKinds;
const columns: Record<DefinitionKind, number> = { schema: 30, domain: 30, concept: 360, relation: 690, predicate: 1020, model: 1350, business_constraint: 1680, business_rule: 2010, unit: 2340, currency: 2670, policy: 3000, connector: 3330 };
const nodeSize = { width: 248, height: 160 };
const nodeLayout = { top: 84, verticalGap: 52 };
const handlePosition: Record<EdgeSide, Position> = { left: Position.Left, right: Position.Right, top: Position.Top, bottom: Position.Bottom };
type NodePositions = Record<string, { x: number; y: number }>;

function relationModeLabel(mode?: RelationMode) {
  return ({ simple: "简单边", reifiable: "可实体化", reified: "必须实体化" } as const)[mode ?? "simple"];
}

function DefinitionNode({ data, selected }: NodeProps) {
  const { definition, dimmed, linked, onShowRelated, onManageBindings, onManageEndpoints } = data as StudioNodeData;
  const meta = kindMeta[definition.kind];
  return (
    <>
      <NodeToolbar isVisible={selected} position={Position.Top} offset={9} className="flex overflow-hidden rounded-lg border border-[#d8d8d4] bg-white p-1 shadow-[0_8px_24px_rgba(35,35,32,.14)]">
        {definition.kind === "concept" && <button className="nodrag flex items-center gap-1.5 rounded-md px-2.5 py-1.5 text-xs font-medium text-[#373733] hover:bg-[#f0f0ed]" onClick={(event) => { event.stopPropagation(); onManageBindings(definition.id); }}><ListChecks className="size-3.5" />属性</button>}
        {definition.kind === "relation" && <button className="nodrag flex items-center gap-1.5 rounded-md px-2.5 py-1.5 text-xs font-medium text-[#373733] hover:bg-[#f0f0ed]" onClick={(event) => { event.stopPropagation(); onManageEndpoints(definition.id); }}><Network className="size-3.5" />建模</button>}
        <button className="nodrag flex items-center gap-1.5 rounded-md px-2.5 py-1.5 text-xs font-medium text-[#373733] hover:bg-[#f0f0ed]" title="在右侧查看关联定义" onClick={(event) => { event.stopPropagation(); onShowRelated(definition.id); }}><Focus className="size-3.5" />关联</button>
      </NodeToolbar>
      <div
        className={cn(
          "studio-node flex h-[160px] w-[248px] cursor-grab flex-col rounded-[10px] border border-[#deded9] bg-white p-3.5 shadow-[0_5px_16px_rgba(44,44,40,.09)] transition-[box-shadow,opacity] active:cursor-grabbing",
          selected && "ring-2 ring-[#6956d9] ring-offset-2 ring-offset-[#f4f4f0] shadow-[0_10px_26px_rgba(70,62,128,.16)]",
          linked && !selected && "ring-1 ring-[#b7acef] ring-offset-1",
          dimmed && "opacity-20 grayscale",
        )}
        style={{ borderLeftColor: meta.color, borderLeftWidth: 4 }}
      >
      {(Object.keys(handlePosition) as EdgeSide[]).flatMap((side) => [
        <Handle key={`target-${side}`} id={`target-${side}`} type="target" position={handlePosition[side]} className="!size-2 !border-0 !bg-transparent !opacity-0" />,
        <Handle key={`source-${side}`} id={`source-${side}`} type="source" position={handlePosition[side]} className="!size-2 !border-0 !bg-transparent !opacity-0" />,
      ])}
      <div className="mb-2.5 flex items-center justify-between gap-3">
        <span className="inline-flex size-7 items-center justify-center rounded-md text-[11px] font-bold text-white" style={{ background: meta.color }}>{meta.short}</span>
        <span className="flex min-w-0 items-center gap-1.5 font-mono text-[11px] text-slate-400"><span className="truncate">{definition.id}</span><GripVertical className="size-4 shrink-0 text-slate-300" aria-hidden="true" /></span>
      </div>
      <p className="truncate text-sm font-semibold text-[#242421]">{definition.label}</p>
      <p className="mt-1.5 line-clamp-2 text-xs leading-[1.55] text-[#73736c]">{definition.description}</p>
      <div className="mt-auto flex items-center justify-between border-t border-[#efefeb] pt-2.5 text-[11px] text-[#85857d]">
        <span>{meta.label}</span><span>{definition.kind === "concept" ? `${definition.bindings?.length ?? 0} 个判断类型` : definition.kind === "relation" ? `${relationModeLabel(definition.relationMode)} · ${definition.endpoints?.length ?? 0} 个方向` : definition.kind === "domain" ? `${definition.conceptScopes?.length ?? 0} 个入口` : `${definition.refs} 个引用`}</span>
      </div>
      </div>
    </>
  );
}

const nodeTypes = { studio: DefinitionNode };

function definitionEdges(definitions: OntologyDefinition[], showReification: boolean): typeof initialEdges {
  const ids = new Set(definitions.map((item) => item.id));
  const bindingEdges = definitions
    .filter((item) => item.kind === "concept")
    .flatMap((concept) => (concept.bindings ?? [])
      .filter((binding) => ids.has(binding.predicateId))
      .map((binding) => ({
        id: `concept-binding-${concept.id}-${binding.predicateId}`,
        source: concept.id,
        target: binding.predicateId,
        relation: "applies_to" as const,
        editable: true,
      })));
  const relationEdges = definitions
    .filter((item) => item.kind === "relation")
    .flatMap((relation) => (relation.endpoints ?? []).flatMap((endpoint, index) => [
      {
        id: `relation-source-${relation.id}-${index}`,
        source: endpoint.sourceConceptId,
        target: relation.id,
        relation: "relation_source" as const,
        editable: true,
      },
      {
        id: `relation-target-${relation.id}-${index}`,
        source: relation.id,
        target: endpoint.targetConceptId,
        relation: "relation_target" as const,
        editable: true,
      },
    ]))
    .filter((edge) => ids.has(edge.source) && ids.has(edge.target));
  const reificationEdges = showReification ? definitions
    .filter((item) => item.kind === "relation" && item.relationMode !== "simple" && item.reification)
    .flatMap((relation) => [
      {
        id: `reifies-as-${relation.id}`,
        source: relation.id,
        target: relation.reification!.nodeType,
        relation: "reifies_as" as const,
        editable: true,
      },
      ...(relation.reification?.properties ?? []).map((binding) => ({
        id: `reification-property-${relation.id}-${binding.predicateId}`,
        source: relation.id,
        target: binding.predicateId,
        relation: "reification_property" as const,
        editable: true,
      })),
    ])
    .filter((edge) => ids.has(edge.source) && ids.has(edge.target)) : [];
  const liveEdges: typeof initialEdges = [];
  const add = (source: string, target: string, relation: typeof initialEdges[number]["relation"]) => {
    if (ids.has(source) && ids.has(target)) liveEdges.push({ id: `${source}-${relation}-${target}`, source, target, relation, editable: false });
  };
  definitions.forEach((item) => {
    if (item.kind === "domain") {
      (item.conceptScopes ?? []).forEach((id) => add(item.id, id, "domain_scope"));
      asStringArray(item.config.required_models).forEach((id) => add(item.id, `model:${id}`, "requires_model"));
      const retrieval = asRecord(item.config.retrieval);
      asStringArray(retrieval.relation_types).forEach((id) => add(item.id, id, "uses_relation"));
      asStringArray(retrieval.predicate_priority).forEach((id) => add(item.id, id, "prioritizes"));
    }
    if (item.kind === "predicate") ["authority", "freshness", "provenance"].forEach((id) => add(item.id, `policy:${id}`, "governed_by"));
    if (item.kind === "model") {
      if (item.config.applies_to) add(item.id, String(item.config.applies_to), "model_scope");
      if (item.config.output_predicate) add(item.id, String(item.config.output_predicate), "model_output");
      (Array.isArray(item.config.inputs) ? item.config.inputs : []).forEach((input) => {
        const predicate = asRecord(input).predicate;
        if (predicate) add(item.id, String(predicate), "model_input");
      });
    }
    if (item.kind === "business_constraint" || item.kind === "business_rule") {
      const scope = asRecord(item.config.scope);
      if (scope.subject_concept) add(item.id, String(scope.subject_concept), "business_scope");
      if (scope.candidate_concept) add(item.id, String(scope.candidate_concept), "business_scope");
      if (scope.relation_type) add(item.id, String(scope.relation_type), "business_scope");
      (Array.isArray(item.config.inputs) ? item.config.inputs : []).forEach((input) => {
        const predicate = asRecord(input).predicate;
        if (predicate) add(item.id, String(predicate), "business_input");
      });
    }
    if (item.kind === "connector") {
      add(item.id, String(asRecord(item.config.node).type), "maps_to");
      [...Object.keys(asRecord(item.config.attrs)), ...Object.keys(asRecord(item.config.assertions))].forEach((id) => add(item.id, id, "maps_to"));
    }
  });
  return uniqueEdgesById([...liveEdges, ...bindingEdges, ...relationEdges, ...reificationEdges]);
}

function CurvedDependencyEdge({ id, sourceX, sourceY, targetX, targetY, markerEnd, style, data }: EdgeProps<StudioEdge>) {
  const sourceSide = data?.sourceSide ?? "right";
  const targetSide = data?.targetSide ?? "left";
  const sourceOffset = data?.sourceOffset ?? 0;
  const targetOffset = data?.targetOffset ?? 0;
  const sourceVertical = sourceSide === "top" || sourceSide === "bottom";
  const targetVertical = targetSide === "top" || targetSide === "bottom";
  const startX = sourceX + (sourceVertical ? sourceOffset : 0);
  const startY = sourceY + (sourceVertical ? 0 : sourceOffset);
  const endX = targetX + (targetVertical ? targetOffset : 0);
  const endY = targetY + (targetVertical ? 0 : targetOffset);
  const distance = Math.hypot(endX - startX, endY - startY);
  const controlDistance = Math.min(168, Math.max(44, distance * .3));
  const direction: Record<EdgeSide, { x: number; y: number }> = {
    left: { x: -1, y: 0 },
    right: { x: 1, y: 0 },
    top: { x: 0, y: -1 },
    bottom: { x: 0, y: 1 },
  };
  const control1X = startX + direction[sourceSide].x * controlDistance;
  const control1Y = startY + direction[sourceSide].y * controlDistance;
  const control2X = endX + direction[targetSide].x * controlDistance;
  const control2Y = endY + direction[targetSide].y * controlDistance;
  const path = `M ${startX} ${startY} C ${control1X} ${control1Y}, ${control2X} ${control2Y}, ${endX} ${endY}`;
  const labelX = (startX + 3 * control1X + 3 * control2X + endX) / 8;
  const labelY = (startY + 3 * control1Y + 3 * control2Y + endY) / 8;

  return <>
    <BaseEdge id={id} path={path} markerEnd={markerEnd} style={style} interactionWidth={18} />
    {data?.active && data.label && <EdgeLabelRenderer>
      <div
        className="pointer-events-none absolute rounded-full border border-[#d9d6ec] bg-white/95 px-2 py-1 text-[10px] font-semibold text-[#5b50a5] shadow-sm"
        style={{ transform: `translate(-50%, -50%) translate(${labelX}px, ${labelY}px)` }}
      >
        {data.label}
      </div>
    </EdgeLabelRenderer>}
  </>;
}

const edgeTypes = { dependencyCurve: CurvedDependencyEdge };

function graphNodes(definitions: OntologyDefinition[], dependencyEdges: typeof initialEdges, search: string, filterKind: "all" | DefinitionKind, lifecycle: "all" | Lifecycle, storage: string, selectedId: string | null, selectedIds: string[], positions: NodePositions, onShowRelated: (id: string) => void, onManageBindings: (id: string) => void, onManageEndpoints: (id: string) => void): StudioNode[] {
  const q = search.trim().toLowerCase();
  const selected = new Set(selectedIds);
  const linked = new Set<string>();
  dependencyEdges.forEach((edge) => {
    if (edge.source === selectedId) linked.add(edge.target);
    if (edge.target === selectedId) linked.add(edge.source);
  });
  const nextY: Partial<Record<DefinitionKind, number>> = {};
  return definitions.map((definition) => {
    const y = nextY[definition.kind] ?? nodeLayout.top;
    nextY[definition.kind] = y + nodeSize.height + nodeLayout.verticalGap;
    const matches = (!q || `${definition.id} ${definition.label} ${definition.description}`.toLowerCase().includes(q))
      && (filterKind === "all" || definition.kind === filterKind)
      && (lifecycle === "all" || definition.lifecycle === lifecycle)
      && (storage === "all" || definition.config.storage_mode === storage);
    return {
      id: definition.id,
      type: "studio",
      initialWidth: nodeSize.width,
      initialHeight: nodeSize.height,
      position: positions[definition.id] ?? { x: columns[definition.kind], y },
      data: { definition, dimmed: !matches, linked: linked.has(definition.id), onShowRelated, onManageBindings, onManageEndpoints },
      draggable: true,
      selected: selected.has(definition.id),
    };
  });
}

function graphEdges(nodes: StudioNode[], selectedId: string | null, dependencyEdges: typeof initialEdges): StudioEdge[] {
  const nodeMap = new Map(nodes.map((node) => [node.id, node]));
  const visibleEdges = dependencyEdges.filter((edge) => nodeMap.has(edge.source) && nodeMap.has(edge.target));
  const routed = visibleEdges.map((edge) => {
    const source = nodeMap.get(edge.source)!;
    const target = nodeMap.get(edge.target)!;
    const sourceCenter = { x: source.position.x + nodeSize.width / 2, y: source.position.y + nodeSize.height / 2 };
    const targetCenter = { x: target.position.x + nodeSize.width / 2, y: target.position.y + nodeSize.height / 2 };
    const dx = targetCenter.x - sourceCenter.x;
    const dy = targetCenter.y - sourceCenter.y;
    const horizontal = Math.abs(dx) >= Math.abs(dy) * .9;
    const sourceSide: EdgeSide = horizontal ? (dx >= 0 ? "right" : "left") : (dy >= 0 ? "bottom" : "top");
    const targetSide: EdgeSide = horizontal ? (dx >= 0 ? "left" : "right") : (dy >= 0 ? "top" : "bottom");
    return { edge, sourceSide, targetSide, sourceCenter, targetCenter };
  });
  const sourceGroups = new Map<string, typeof routed>();
  const targetGroups = new Map<string, typeof routed>();
  routed.forEach((route) => {
    const sourceKey = `${route.edge.source}:${route.sourceSide}`;
    const targetKey = `${route.edge.target}:${route.targetSide}`;
    sourceGroups.set(sourceKey, [...(sourceGroups.get(sourceKey) ?? []), route]);
    targetGroups.set(targetKey, [...(targetGroups.get(targetKey) ?? []), route]);
  });
  const portOffset = (routes: typeof routed, edgeId: string, side: EdgeSide, point: "source" | "target") => {
    const sorted = [...routes].sort((a, b) => {
      const aCenter = point === "source" ? a.targetCenter : a.sourceCenter;
      const bCenter = point === "source" ? b.targetCenter : b.sourceCenter;
      return side === "left" || side === "right" ? aCenter.y - bCenter.y : aCenter.x - bCenter.x;
    });
    const lane = sorted.findIndex((route) => route.edge.id === edgeId) - (sorted.length - 1) / 2;
    return Math.max(-34, Math.min(34, lane * 13));
  };

  return routed.map(({ edge, sourceSide, targetSide }) => {
    const active = edge.source === selectedId || edge.target === selectedId;
    const directionBias = edge.relation === "relation_source" ? -7 : edge.relation === "relation_target" ? 7 : 0;
    const sourceOffset = Math.max(-34, Math.min(34, portOffset(sourceGroups.get(`${edge.source}:${sourceSide}`) ?? [], edge.id, sourceSide, "source") + directionBias));
    const targetOffset = Math.max(-34, Math.min(34, portOffset(targetGroups.get(`${edge.target}:${targetSide}`) ?? [], edge.id, targetSide, "target") + directionBias));
    const stroke = active ? "#6557d5" : edge.relation === "governed_by" ? "#68a8ad" : edge.relation === "applies_to" || edge.relation === "reification_property" ? "#8c82cf" : edge.relation === "domain_scope" ? "#26939a" : edge.relation === "reifies_as" ? "#4f7fc5" : ["requires_model", "model_input", "model_output", "model_scope"].includes(edge.relation) ? "#dc2626" : edge.relation === "maps_to" || edge.relation === "conforms_to" ? "#0f766e" : "#d18a24";
    return {
      id: edge.id, source: edge.source, target: edge.target, type: "dependencyCurve",
      sourceHandle: `source-${sourceSide}`,
      targetHandle: `target-${targetSide}`,
      markerEnd: { type: MarkerType.ArrowClosed, width: 11, height: 11, color: stroke },
      data: {
        active,
        label: active ? ({ governed_by: "受策略治理", applies_to: "适用判断类型", domain_scope: "领域入口概念", relation_source: "关系起点", relation_target: "关系目标", reifies_as: "实体化为节点", reification_property: "实体关系判断", requires_model: "需要确定性模型", model_input: "公式输入", model_output: "公式输出", model_scope: "适用概念", uses_relation: "使用关系", prioritizes: "优先判断", maps_to: "映射为", conforms_to: "符合契约", business_scope: "适用概念", business_input: "使用判断事实" } as const)[edge.relation] : undefined,
        sourceSide,
        targetSide,
        sourceOffset,
        targetOffset,
      },
      style: {
        stroke,
        strokeWidth: active ? 2.1 : 1.25,
        strokeDasharray: edge.relation === "governed_by" ? "5 5" : undefined,
        strokeLinecap: "round",
        strokeLinejoin: "round",
        opacity: selectedId && !active ? .3 : .82,
      },
    };
  });
}

function statusLabel(status: ChangeSetStatus) {
  return ({ proposed: "已提议", review_required: "待审核", approved: "已批准", rejected: "已驳回", changes_requested: "需修改", published: "已发布" })[status];
}

function statusClass(status: ChangeSetStatus) {
  return ({
    proposed: "border-slate-200 bg-slate-50 text-slate-700",
    review_required: "border-amber-200 bg-amber-50 text-amber-700",
    approved: "border-sky-200 bg-sky-50 text-sky-700",
    rejected: "border-rose-200 bg-rose-50 text-rose-700",
    changes_requested: "border-orange-200 bg-orange-50 text-orange-700",
    published: "border-emerald-200 bg-emerald-50 text-emerald-700",
  })[status];
}

function riskLabel(risk: ChangeSet["risk"]) {
  return ({ low: "低", normal: "中", high: "高" })[risk];
}

function yamlPreview(operation: ChangeOperation) {
  const item = operation.after ?? operation.before;
  if (!item) return "";
  if (operation.type === "delete") return `# 删除 ${item.sourcePath}\n- ${item.id}`;
  if (item.kind === "model" || item.kind === "unit" || item.kind === "currency") return JSON.stringify({ ...item.config, id: item.id.replace(/^(model|unit|currency):/, ""), label: item.label, description: item.description }, null, 2);
  if (item.kind === "predicate") return [
    `id: ${item.id}`, `label: ${item.label}`, "value:", `  type: ${item.config.value_type}`,
    `  cardinality: ${item.config.cardinality}`, ...(item.config.dimension ? [`  dimension: ${item.config.dimension}`] : []),
    ...(Array.isArray(item.config.units) && item.config.units.length ? [`  units: [${item.config.units.join(", ")}]`] : []),
    ...(item.config.default_unit ? [`  default_unit: ${item.config.default_unit}`] : []),
    "storage:", `  mode: ${item.config.storage_mode}`,
    "policy:", `  write: ${item.config.write}`, `  freshness: ${item.config.freshness}`,
    `  authority: ${item.config.authority}`, `  provenance_tier: ${item.config.provenance_tier}`,
    "status:", `  lifecycle: ${item.lifecycle}`, `  equivalent_to: ${item.config.equivalent_to ?? ""}`,
  ].join("\n");
  if (item.kind === "concept") {
    const bindings = item.bindings ?? [];
    return [
      "concept_types:", `  - id: ${item.id}`, `    label: ${item.label}`, `    status: ${item.lifecycle} # 目标契约`,
      bindings.length ? "    properties:" : "    properties: []",
      ...bindings.flatMap((binding) => [
        `      - predicate: ${binding.predicateId}`,
        `        required: ${binding.required}`,
        `        cardinality: ${binding.cardinality}`,
        `        group: ${binding.group}`,
      ]),
    ].join("\n");
  }
  if (item.kind === "relation") {
    const endpoints = item.endpoints ?? [];
    return [
      "relation_types:", `  - id: ${item.id}`, `    label: ${item.label}`, `    status: ${item.lifecycle} # 目标契约`,
      `    mode: ${item.relationMode ?? "simple"}`,
      endpoints.length ? "    connections:" : "    connections: []",
      ...endpoints.flatMap((endpoint) => [
        `      - source_type: ${endpoint.sourceConceptId}`,
        `        target_type: ${endpoint.targetConceptId}`,
        `        source_cardinality: ${endpoint.sourceCardinality}`,
        `        target_cardinality: ${endpoint.targetCardinality}`,
      ]),
      ...(item.relationMode && item.relationMode !== "simple" && item.reification ? [
        "    reification:",
        `      node_type: ${item.reification.nodeType}`,
        `      identity: ${item.reification.identity}`,
        item.reification.properties.length ? "      properties:" : "      properties: []",
        ...item.reification.properties.flatMap((binding) => [
          `        - predicate: ${binding.predicateId}`,
          `          required: ${binding.required}`,
          `          cardinality: ${binding.cardinality}`,
          `          group: ${binding.group}`,
        ]),
      ] : []),
    ].join("\n");
  }
  return [`id: ${item.id}`, `label: ${item.label}`].join("\n");
}

function stageDefinitionOperation(items: ChangeOperation[], next: OntologyDefinition, baseline: OntologyDefinition[], preferredType?: ChangeOperationType) {
  const existing = items.find((item) => item.targetId === next.id);
  if (existing?.type === "create") return items.map((item) => item.id === existing.id ? { ...item, after: next } : item);
  const operation: ChangeOperation = {
    id: existing?.id ?? `op-${Date.now()}-${next.id}`,
    type: preferredType ?? (next.lifecycle === "deprecated" ? "deprecate" : "update"),
    targetId: next.id,
    targetKind: next.kind,
    before: baseline.find((item) => item.id === next.id) ?? existing?.before ?? null,
    after: next,
  };
  return existing ? items.map((item) => item.id === existing.id ? operation : item) : [...items, operation];
}

function Catalog({ definitions, selectedId, onSelect, onQuickEdit, filterKind, setFilterKind, onCreate }: {
  definitions: OntologyDefinition[]; selectedId: string | null; onSelect: (id: string) => void;
  onQuickEdit: (id: string) => void; filterKind: "all" | DefinitionKind; setFilterKind: (kind: "all" | DefinitionKind) => void; onCreate: () => void;
}) {
  return (
    <div className="flex h-full min-h-0 flex-col bg-[#f4f4f1]">
      <div className="border-b border-[#deded9] px-3.5 py-3">
        <div className="flex items-center justify-between">
          <div><p className="text-sm font-semibold text-[#292925]">定义库</p><p className="mt-0.5 text-xs text-[#85857d]">{definitions.length} 项 · ChangeSet 治理</p></div>
          <Button size="icon-sm" onClick={onCreate} aria-label="新增本体定义" className="rounded-lg bg-[#2e2e2a] text-white hover:bg-[#464641]"><Plus /></Button>
        </div>
      </div>
      <div className="flex flex-wrap gap-1 border-b border-[#deded9] p-2.5">
        <button onClick={() => setFilterKind("all")} className={cn("catalog-filter", filterKind === "all" && "catalog-filter-active")}>全部</button>
        {allKinds.map((kind) => <button key={kind} onClick={() => setFilterKind(kind)} className={cn("catalog-filter", filterKind === kind && "catalog-filter-active")}>{kindMeta[kind].label}</button>)}
      </div>
      <ScrollArea className="min-h-0 flex-1">
        <div className="space-y-4 p-2.5 pb-24">
          {allKinds.map((kind) => {
            const items = definitions.filter((item) => item.kind === kind && (filterKind === "all" || filterKind === kind));
            if (!items.length) return null;
            return <section key={kind}>
              <div className="mb-1.5 flex items-center justify-between px-2"><p className="text-xs font-semibold text-[#85857d]">{kindMeta[kind].label}</p><span className="text-xs text-[#a0a098]">{items.length}</span></div>
              <div className="space-y-0.5">{items.map((item) => <button key={item.id} onClick={() => onSelect(item.id)} className={cn("group flex w-full items-center gap-2.5 rounded-lg border border-transparent px-2 py-2 text-left transition-colors hover:bg-[#e9e9e5]", selectedId === item.id && "border-[#d8d3f4] bg-[#ebe8fb]")}>
                <span className="flex size-6 shrink-0 items-center justify-center rounded-md text-[10px] font-bold text-white" style={{ background: kindMeta[kind].color }}>{kindMeta[kind].short}</span>
                <span className="min-w-0 flex-1"><span className="block truncate text-[13px] font-medium text-[#393934]">{item.label}</span><span className="block truncate font-mono text-[10px] text-[#92928a]">{item.id}</span></span>
                {!item.readOnly && <span role="button" tabIndex={0} aria-label={`快速编辑 ${item.label}`} onClick={(event) => { event.stopPropagation(); onQuickEdit(item.id); }} onKeyDown={(event) => { if (event.key === "Enter") { event.stopPropagation(); onQuickEdit(item.id); } }} className="flex size-6 items-center justify-center rounded-md text-[#94948c] opacity-0 hover:bg-white hover:text-[#494944] focus:opacity-100 group-hover:opacity-100"><PencilLine className="size-3.5" /></span>}
              </button>)}</div>
            </section>;
          })}
        </div>
      </ScrollArea>
    </div>
  );
}

function Choice({ label, value, options, onChange, optionLabels }: { label: string; value: string; options: string[]; onChange: (value: string) => void; optionLabels?: Record<string, string> }) {
  return <div className="space-y-1.5"><Label className="text-xs text-slate-500">{label}</Label><Select value={value} onValueChange={onChange}><SelectTrigger className="w-full bg-white"><SelectValue /></SelectTrigger><SelectContent>{options.map((option) => <SelectItem key={option} value={option}>{optionLabels?.[option] ?? option}</SelectItem>)}</SelectContent></Select></div>;
}

function RegistryPicker({ label, value, options, onChange, optional = false, multiple = false, disabled = false }: {
  label: string; value: string | string[]; options: RegistryOption[]; onChange: (next: string | string[]) => void;
  optional?: boolean; multiple?: boolean; disabled?: boolean;
}) {
  const [open, setOpen] = useState(false);
  const selected = Array.isArray(value) ? value : value ? [value] : [];
  const names = selected.map((item) => options.find((option) => option.value === item)?.label ?? item);
  const choose = (item: string) => {
    if (multiple) onChange(selected.includes(item) ? selected.filter((current) => current !== item) : [...selected, item]);
    else { onChange(item); setOpen(false); }
  };
  return <div className="min-w-0 space-y-1.5"><Label className="text-xs text-slate-500">{label}</Label><Popover open={open} onOpenChange={setOpen}>
    <PopoverTrigger asChild><Button type="button" variant="outline" role="combobox" aria-label={label} aria-expanded={open} disabled={disabled} className="h-auto min-h-9 w-full justify-between bg-white px-3 text-left font-normal">
      <span className={cn("min-w-0 truncate", selected.length === 0 && "text-slate-400")}>{names.length ? names.join("、") : "搜索并选择"}</span><Search className="ml-2 size-3.5 shrink-0 text-slate-400" />
    </Button></PopoverTrigger>
    <PopoverContent align="start" className="w-[min(22rem,calc(100vw-2rem))] p-0"><Command><CommandInput placeholder={`搜索${label}…`} /><CommandList><CommandEmpty>没有可选项</CommandEmpty><CommandGroup>
      {optional && !multiple && <CommandItem value="清空选择" onSelect={() => { onChange(""); setOpen(false); }}><Check className={cn("size-4", selected.length ? "opacity-0" : "opacity-100")} />不指定</CommandItem>}
      {options.map((option) => <CommandItem key={option.value} value={`${option.value} ${option.label}`} onSelect={() => choose(option.value)}><Check className={cn("size-4", selected.includes(option.value) ? "opacity-100" : "opacity-0")} /><span className="truncate">{option.label}</span></CommandItem>)}
    </CommandGroup></CommandList></Command></PopoverContent>
  </Popover></div>;
}

function Metric({ label, value }: { label: string; value: string }) {
  return <div className="rounded-xl border border-slate-200 bg-slate-50 px-3 py-3"><p className="text-[11px] text-slate-400">{label}</p><p className="mt-1 truncate text-sm font-semibold text-slate-800">{value}</p></div>;
}

function formatConfigValue(value: unknown) {
  if (value === null || value === undefined || value === "") return "—";
  return typeof value === "object" ? JSON.stringify(value, null, 2) : String(value);
}

function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : {};
}

function asStringArray(value: unknown): string[] {
  return Array.isArray(value) ? value.filter((item): item is string => typeof item === "string") : [];
}

const schemaRequiredFields = {
  required_root: ["base", "knowledge"],
  required_base: ["schema", "node", "lifecycle", "version"],
  required_node: ["id", "kind", "type", "key", "label"],
  required_knowledge: ["attrs", "assertions", "relations", "logic_refs"],
} as const;

function SchemaRequirementsEditor({ definition, onUpdate }: { definition: OntologyDefinition; onUpdate: (next: OntologyDefinition) => void }) {
  const [draft, setDraft] = useState(() => Object.fromEntries(Object.keys(schemaRequiredFields).map((key) => [key, asStringArray(definition.config[key]).join(", ")])) as Record<keyof typeof schemaRequiredFields, string>);
  const parsed = Object.fromEntries(Object.entries(draft).map(([key, value]) => [key, value.split(",").map((part) => part.trim()).filter(Boolean)])) as Record<keyof typeof schemaRequiredFields, string[]>;
  const missing = Object.entries(schemaRequiredFields).flatMap(([key, required]) => required.filter((field) => !parsed[key as keyof typeof schemaRequiredFields].includes(field)).map((field) => `${key}: ${field}`));
  const duplicated = Object.values(parsed).some((values) => new Set(values).size !== values.length);
  const changed = Object.keys(schemaRequiredFields).some((key) => JSON.stringify(parsed[key as keyof typeof schemaRequiredFields]) !== JSON.stringify(asStringArray(definition.config[key])));
  return <section className="space-y-3 rounded-2xl border border-slate-200 bg-white p-4">
    <div><p className="text-sm font-semibold">Schema 必填字段</p><p className="mt-1 text-xs text-slate-500">用逗号分隔字段名；核心信封字段必须保留。</p></div>
    {(Object.keys(schemaRequiredFields) as (keyof typeof schemaRequiredFields)[]).map((key) => <div key={key} className="space-y-1"><Label className="font-mono text-xs">{key}</Label><Input value={draft[key]} onChange={(event) => setDraft((current) => ({ ...current, [key]: event.target.value }))} /></div>)}
    {missing.length > 0 && <p className="text-xs text-rose-600">缺少核心字段：{missing.join("、")}</p>}
    {duplicated && <p className="text-xs text-rose-600">必填字段不能重复。</p>}
    <Button size="sm" disabled={!changed || missing.length > 0 || duplicated} onClick={() => onUpdate({ ...definition, config: { ...definition.config, ...parsed } })}>暂存 Schema 字段变更</Button>
  </section>;
}

const bindingGroupLabels: Record<ConceptBinding["group"], string> = {
  identity: "身份标识",
  profile: "基本资料",
  measurement: "度量指标",
  governance: "治理判断",
  other: "其他",
};

const bindingCardinalityLabels: Record<ConceptBinding["cardinality"], string> = {
  inherit: "继承判断类型",
  one: "单值",
  many: "多值",
  temporal_many: "多值（含时态）",
};

function ConceptBindingsDialog({ open, onOpenChange, concept, definitions, onUpdateConcept, onCreateAndBind }: {
  open: boolean;
  onOpenChange: (value: boolean) => void;
  concept: OntologyDefinition | null;
  definitions: OntologyDefinition[];
  onUpdateConcept: (next: OntologyDefinition) => void;
  onCreateAndBind: (predicate: OntologyDefinition, conceptId: string) => void;
}) {
  const [search, setSearch] = useState("");
  const [createMode, setCreateMode] = useState(false);
  const [newId, setNewId] = useState("");
  const [newLabel, setNewLabel] = useState("");
  const [newDescription, setNewDescription] = useState("");
  const [newValueType, setNewValueType] = useState("string");
  const [newStorage, setNewStorage] = useState("attr");
  const predicates = definitions.filter((item) => item.kind === "predicate" && `${item.label} ${item.id} ${item.description}`.toLowerCase().includes(search.trim().toLowerCase()));
  const bindings = concept?.bindings ?? [];
  const duplicate = definitions.some((item) => item.id === newId.trim());
  const invalidId = Boolean(newId && !/^[a-z][a-z0-9_:-]*$/.test(newId));
  const createValid = Boolean(newId.trim() && newLabel.trim() && newDescription.trim() && !duplicate && !invalidId);
  const setBindings = (next: ConceptBinding[]) => {
    if (!concept) return;
    onUpdateConcept({ ...concept, bindings: next, config: { ...concept.config, schema_support: "target-contract" } });
  };
  const bind = (predicate: OntologyDefinition) => setBindings([...bindings, {
    predicateId: predicate.id,
    required: false,
    cardinality: "inherit",
    group: predicate.config.storage_mode === "attr" ? "profile" : "governance",
  }]);
  const updateBinding = (predicateId: string, patch: Partial<ConceptBinding>) => setBindings(bindings.map((item) => item.predicateId === predicateId ? { ...item, ...patch } : item));
  const unbind = (predicateId: string) => setBindings(bindings.filter((item) => item.predicateId !== predicateId));
  const createAndBind = () => {
    if (!concept || !createValid) return;
    const id = newId.trim();
    onCreateAndBind({
      id,
      kind: "predicate",
      label: newLabel.trim(),
      description: newDescription.trim(),
      lifecycle: "draft",
      sourcePath: `control/predicates/${id}.yaml`,
      refs: 0,
      files: [],
      config: {
        value_type: newValueType,
        cardinality: "one",
        storage_mode: newStorage,
        authority: "git_authored",
        freshness: newStorage === "attr" ? "stable" : "operational_30d",
        provenance_tier: newStorage === "attr" ? "C" : "B",
        write: "reviewed",
        temporal: newStorage === "attr" ? "none" : "required",
        evidence: newStorage === "attr" ? "optional" : "required",
        history: newStorage === "attr" ? "git_only" : "full",
        embedding: false,
        equivalent_to: null,
      },
    }, concept.id);
    setNewId(""); setNewLabel(""); setNewDescription(""); setNewValueType("string"); setNewStorage("attr"); setCreateMode(false);
  };
  return <Dialog open={open} onOpenChange={onOpenChange}><DialogContent className="max-h-[88vh] overflow-hidden p-0 sm:max-w-3xl">
    <DialogHeader className="border-b px-6 py-5"><DialogTitle>{concept ? `${concept.label} · 属性与判断类型` : "属性与判断类型"}</DialogTitle><DialogDescription>声明哪些全局判断类型适用于该概念。这里只维护定义与约束，不编辑实体属性值。</DialogDescription></DialogHeader>
    <div className="flex items-center gap-2 border-b bg-[#fafaf8] px-5 py-3"><div className="relative min-w-0 flex-1"><Search className="absolute left-3 top-1/2 size-4 -translate-y-1/2 text-slate-400" /><Input value={search} onChange={(event) => setSearch(event.target.value)} placeholder="搜索判断类型" className="bg-white pl-9" /></div><Button variant={createMode ? "secondary" : "outline"} onClick={() => setCreateMode((value) => !value)}><PlusCircle />新建并绑定</Button></div>
    <ScrollArea className="max-h-[60vh]"><div className="space-y-3 p-5">
      <Alert className="border-sky-200 bg-sky-50"><ShieldCheck className="size-4" /><AlertTitle>概念形状 · 目标契约</AlertTitle><AlertDescription>判断类型仍需先注册；这里声明适用范围、必填性、概念内基数与展示分组。提交时会与概念定义一起进入 ChangeSet。</AlertDescription></Alert>
      {createMode && <div className="space-y-4 rounded-2xl border border-violet-200 bg-violet-50/50 p-4"><div className="flex items-center justify-between"><p className="font-semibold text-violet-950">新建判断类型</p><Badge variant="outline" className="border-violet-200 text-violet-700">自动绑定</Badge></div><div className="grid grid-cols-2 gap-3"><div className="space-y-1.5"><Label>稳定 ID</Label><Input value={newId} onChange={(event) => setNewId(event.target.value)} placeholder="例如 model_number" aria-invalid={duplicate || invalidId} />{duplicate && <p className="text-xs text-rose-600">该 ID 已存在。</p>}{invalidId && <p className="text-xs text-rose-600">请使用小写稳定标识。</p>}</div><div className="space-y-1.5"><Label>显示名称</Label><Input value={newLabel} onChange={(event) => setNewLabel(event.target.value)} placeholder="例如 型号" /></div></div><div className="space-y-1.5"><Label>语义说明</Label><Textarea value={newDescription} onChange={(event) => setNewDescription(event.target.value)} placeholder="说明属性边界与业务含义。" /></div><div className="grid grid-cols-2 gap-3"><Choice label="值类型" value={newValueType} options={["string", "text", "number", "quantity", "currency", "enum", "boolean", "date", "node_ref", "json"]} onChange={setNewValueType} /><Choice label="存储模式" value={newStorage} options={["attr", "assertion", "external"]} onChange={setNewStorage} /></div><div className="flex justify-end"><Button disabled={!createValid} onClick={createAndBind}>创建草稿并绑定</Button></div></div>}
      {predicates.map((predicate) => {
        const binding = bindings.find((item) => item.predicateId === predicate.id);
        return <div key={predicate.id} className={cn("rounded-2xl border bg-white p-4", binding && "border-violet-200 shadow-[0_3px_12px_rgba(85,70,180,.08)]")}><div className="flex items-start gap-3"><span className="flex size-8 shrink-0 items-center justify-center rounded-lg bg-violet-600 text-xs font-bold text-white">P</span><div className="min-w-0 flex-1"><div className="flex flex-wrap items-center gap-2"><p className="font-semibold text-slate-900">{predicate.label}</p><span className="font-mono text-[11px] text-slate-400">{predicate.id}</span>{predicate.lifecycle === "draft" && <Badge variant="outline">草稿</Badge>}</div><p className="mt-1 line-clamp-2 text-xs leading-5 text-slate-500">{predicate.description}</p><div className="mt-2 flex flex-wrap gap-1.5"><Badge variant="secondary">{String(predicate.config.storage_mode)}</Badge><Badge variant="secondary">{String(predicate.config.value_type)}</Badge><Badge variant="secondary">{String(predicate.config.cardinality)}</Badge></div></div>{binding ? <Button variant="ghost" size="sm" className="text-rose-600" onClick={() => unbind(predicate.id)}><Unlink />解绑</Button> : <Button variant="outline" size="sm" onClick={() => bind(predicate)}><Link2 />绑定</Button>}</div>
          {binding && <div className="mt-4 grid grid-cols-[1fr_1fr_auto] items-end gap-3 border-t pt-4"><Choice label="概念内基数" value={binding.cardinality} options={["inherit", "one", "many", "temporal_many"]} optionLabels={bindingCardinalityLabels} onChange={(value) => updateBinding(predicate.id, { cardinality: value as ConceptBinding["cardinality"] })} /><Choice label="展示分组" value={binding.group} options={Object.keys(bindingGroupLabels)} optionLabels={bindingGroupLabels} onChange={(value) => updateBinding(predicate.id, { group: value as ConceptBinding["group"] })} /><label className="flex h-9 items-center gap-2 whitespace-nowrap rounded-lg border bg-slate-50 px-3 text-xs font-medium text-slate-700"><Switch checked={binding.required} onCheckedChange={(value) => updateBinding(predicate.id, { required: value })} />必填</label></div>}
        </div>;
      })}
      {!predicates.length && <div className="rounded-2xl border border-dashed p-10 text-center text-sm text-slate-400">没有匹配的判断类型。</div>}
    </div></ScrollArea>
    <DialogFooter className="border-t px-6 py-4"><p className="mr-auto text-xs text-slate-500">已绑定 {bindings.length} 项 · 修改已自动暂存</p><Button onClick={() => onOpenChange(false)}>完成</Button></DialogFooter>
  </DialogContent></Dialog>;
}

function RelationEndpointsDialog({ open, onOpenChange, relation, definitions, onUpdateRelation, onCreateAndBind }: {
  open: boolean;
  onOpenChange: (value: boolean) => void;
  relation: OntologyDefinition | null;
  definitions: OntologyDefinition[];
  onUpdateRelation: (next: OntologyDefinition) => void;
  onCreateAndBind: (predicate: OntologyDefinition, relationId: string) => void;
}) {
  const concepts = definitions.filter((item) => item.kind === "concept");
  const predicates = definitions.filter((item) => item.kind === "predicate");
  const [sourceConceptId, setSourceConceptId] = useState("organization");
  const [targetConceptId, setTargetConceptId] = useState("market");
  const [sourceCardinality, setSourceCardinality] = useState<RelationEndpoint["sourceCardinality"]>("many");
  const [targetCardinality, setTargetCardinality] = useState<RelationEndpoint["targetCardinality"]>("many");
  const [predicateToAdd, setPredicateToAdd] = useState("");
  const [createMode, setCreateMode] = useState(false);
  const [newId, setNewId] = useState("");
  const [newLabel, setNewLabel] = useState("");
  const [newDescription, setNewDescription] = useState("");
  const [newValueType, setNewValueType] = useState("string");
  const [newStorage, setNewStorage] = useState("attr");
  const endpoints = relation?.endpoints ?? [];
  const mode = relation?.relationMode ?? "simple";
  const reification = relation?.reification ?? { nodeType: "relation", identity: "required" as const, properties: [] };
  const reificationProperties = reification.properties;
  const availablePredicates = predicates.filter((item) => !reificationProperties.some((binding) => binding.predicateId === item.id));
  const duplicate = endpoints.some((item) => item.sourceConceptId === sourceConceptId && item.targetConceptId === targetConceptId);
  const duplicateNewId = definitions.some((item) => item.id === newId.trim());
  const invalidNewId = Boolean(newId && !/^[a-z][a-z0-9_:-]*$/.test(newId));
  const createValid = Boolean(newId.trim() && newLabel.trim() && newDescription.trim() && !duplicateNewId && !invalidNewId);
  const conceptLabel = (id: string) => concepts.find((item) => item.id === id)?.label ?? id;
  const predicateLabel = (id: string) => predicates.find((item) => item.id === id)?.label ?? id;
  const updateRelation = (patch: Partial<OntologyDefinition>) => {
    if (!relation) return;
    onUpdateRelation({ ...relation, ...patch, config: { ...relation.config, ...(patch.config ?? {}), schema_support: "target-contract" } });
  };
  const setEndpoints = (next: RelationEndpoint[]) => {
    updateRelation({ endpoints: next, config: { ...relation?.config, domain_range: next.length ? "declared" : "undeclared" } });
  };
  const setMode = (nextMode: RelationMode) => updateRelation({
    relationMode: nextMode,
    reification: nextMode === "simple" ? relation?.reification : { ...reification, identity: nextMode === "reified" ? "required" : reification.identity },
    config: { ...relation?.config, relation_mode: nextMode },
  });
  const setReification = (patch: Partial<typeof reification>) => updateRelation({ reification: { ...reification, ...patch } });
  const setReificationProperties = (properties: ConceptBinding[]) => setReification({ properties });
  const add = () => {
    if (!sourceConceptId || !targetConceptId || duplicate) return;
    setEndpoints([...endpoints, { sourceConceptId, targetConceptId, sourceCardinality, targetCardinality }]);
  };
  const addPredicate = () => {
    if (!predicateToAdd) return;
    const predicate = predicates.find((item) => item.id === predicateToAdd);
    if (!predicate) return;
    setReificationProperties([...reificationProperties, { predicateId: predicate.id, required: false, cardinality: "inherit", group: predicate.config.storage_mode === "attr" ? "profile" : "governance" }]);
    setPredicateToAdd("");
  };
  const updateProperty = (predicateId: string, patch: Partial<ConceptBinding>) => setReificationProperties(reificationProperties.map((item) => item.predicateId === predicateId ? { ...item, ...patch } : item));
  const createAndBind = () => {
    if (!relation || !createValid) return;
    const id = newId.trim();
    onCreateAndBind({
      id, kind: "predicate", label: newLabel.trim(), description: newDescription.trim(), lifecycle: "draft",
      sourcePath: `control/predicates/${id}.yaml`, refs: 0, files: [],
      config: { value_type: newValueType, cardinality: "one", storage_mode: newStorage, authority: "git_authored", freshness: newStorage === "attr" ? "stable" : "operational_30d", provenance_tier: newStorage === "attr" ? "C" : "B", write: "reviewed", temporal: newStorage === "attr" ? "none" : "required", evidence: newStorage === "attr" ? "optional" : "required", history: newStorage === "attr" ? "git_only" : "full", embedding: false, equivalent_to: null },
    }, relation.id);
    setNewId(""); setNewLabel(""); setNewDescription(""); setNewValueType("string"); setNewStorage("attr"); setCreateMode(false);
  };
  return <Dialog open={open} onOpenChange={onOpenChange}><DialogContent className="max-h-[88vh] overflow-hidden p-0 sm:max-w-3xl">
    <DialogHeader className="border-b px-6 py-5"><DialogTitle>{relation ? `${relation.label} · 关系建模` : "关系建模"}</DialogTitle><DialogDescription>统一维护有向概念连接，以及关系是否需要升级为拥有独立身份、属性、判断和证据的实体节点。</DialogDescription></DialogHeader>
    <ScrollArea className="max-h-[62vh]"><div className="space-y-5 p-5">
      <Alert className="border-amber-200 bg-amber-50"><AlertTriangle className="size-4" /><AlertTitle>关系形状与实体化模板 · 目标契约</AlertTitle><AlertDescription>当前 core.yaml 只注册关系名称。这里的 domain/range、承载方式和实体化模板都会进入 ChangeSet，并明确标记需要编译器与校验器支持。</AlertDescription></Alert>
      <section><p className="mb-2 text-sm font-semibold">承载方式</p><div className="grid grid-cols-3 gap-2">{(["simple", "reifiable", "reified"] as RelationMode[]).map((item) => <button key={item} onClick={() => setMode(item)} className={cn("rounded-2xl border p-3 text-left", mode === item ? "border-amber-400 bg-amber-50 shadow-sm" : "bg-white hover:border-amber-200")}><span className="block text-sm font-semibold">{relationModeLabel(item)}</span><span className="mt-1 block text-[11px] leading-4 text-slate-500">{item === "simple" ? "仅保存轻量有向边" : item === "reifiable" ? "需要属性时升级为节点" : "每个关系实例必须有独立节点"}</span></button>)}</div></section>
      <div className="rounded-2xl border bg-[#fafaf8] p-4"><p className="mb-4 text-sm font-semibold">新增允许的连接方向</p><div className="grid grid-cols-[1fr_auto_1fr] items-end gap-3"><div className="space-y-1.5"><Label className="text-xs text-slate-500">源概念（domain）</Label><Select value={sourceConceptId} onValueChange={setSourceConceptId}><SelectTrigger className="w-full bg-white"><SelectValue /></SelectTrigger><SelectContent>{concepts.map((item) => <SelectItem key={item.id} value={item.id}>{item.label} · {item.id}</SelectItem>)}</SelectContent></Select></div><ArrowRight className="mb-2 size-5 text-amber-600" /><div className="space-y-1.5"><Label className="text-xs text-slate-500">目标概念（range）</Label><Select value={targetConceptId} onValueChange={setTargetConceptId}><SelectTrigger className="w-full bg-white"><SelectValue /></SelectTrigger><SelectContent>{concepts.map((item) => <SelectItem key={item.id} value={item.id}>{item.label} · {item.id}</SelectItem>)}</SelectContent></Select></div></div><div className="mt-3 grid grid-cols-2 gap-3"><Choice label="源端基数" value={sourceCardinality} options={["one", "many"]} optionLabels={{ one: "一个", many: "多个" }} onChange={(value) => setSourceCardinality(value as RelationEndpoint["sourceCardinality"])} /><Choice label="目标端基数" value={targetCardinality} options={["one", "many"]} optionLabels={{ one: "一个", many: "多个" }} onChange={(value) => setTargetCardinality(value as RelationEndpoint["targetCardinality"])} /></div><div className="mt-4 flex items-center justify-between"><p className={cn("text-xs", duplicate ? "text-amber-700" : "text-slate-500")}>{duplicate ? "该方向已经声明。" : "允许同一概念指向自身，例如任务依赖任务。"}</p><Button disabled={duplicate || !sourceConceptId || !targetConceptId} onClick={add}><Link2 />添加连接</Button></div></div>
      <section><div className="mb-2 flex items-center justify-between"><p className="text-sm font-semibold">已声明方向</p><Badge variant="outline">{endpoints.length}</Badge></div><div className="space-y-2">{endpoints.length ? endpoints.map((endpoint, index) => <div key={`${endpoint.sourceConceptId}-${endpoint.targetConceptId}-${index}`} className="flex items-center gap-3 rounded-2xl border border-amber-200 bg-white p-3"><span className="flex size-8 items-center justify-center rounded-lg bg-blue-600 text-xs font-bold text-white">C</span><div className="min-w-0 flex-1"><p className="truncate text-sm font-semibold">{conceptLabel(endpoint.sourceConceptId)}</p><p className="truncate font-mono text-[10px] text-slate-400">{endpoint.sourceConceptId} · {endpoint.sourceCardinality}</p></div><div className="flex items-center gap-1 text-amber-700"><span className="h-px w-5 bg-amber-400" /><ArrowRight className="size-4" /><span className="h-px w-5 bg-amber-400" /></div><div className="min-w-0 flex-1"><p className="truncate text-sm font-semibold">{conceptLabel(endpoint.targetConceptId)}</p><p className="truncate font-mono text-[10px] text-slate-400">{endpoint.targetConceptId} · {endpoint.targetCardinality}</p></div><Button variant="ghost" size="icon-sm" className="text-rose-600" aria-label="移除连接" onClick={() => setEndpoints(endpoints.filter((_, itemIndex) => itemIndex !== index))}><Trash2 /></Button></div>) : <div className="rounded-2xl border border-dashed border-amber-200 p-8 text-center text-sm text-amber-700">尚未声明连接方向。</div>}</div></section>
      {mode !== "simple" && <section className="space-y-4 rounded-2xl border border-blue-200 bg-blue-50/40 p-4"><div><p className="text-sm font-semibold text-blue-950">实体化关系模板</p><p className="mt-1 text-xs leading-5 text-blue-800/70">关系实例将成为普通知识节点，可拥有稳定 ID、生命周期、属性、Assertion、证据与来源。</p></div><div className="grid grid-cols-2 gap-3"><div className="space-y-1.5"><Label className="text-xs text-slate-500">节点类型</Label><Select value={reification.nodeType} onValueChange={(value) => setReification({ nodeType: value })}><SelectTrigger className="w-full bg-white"><SelectValue /></SelectTrigger><SelectContent>{concepts.map((item) => <SelectItem key={item.id} value={item.id}>{item.label} · {item.id}</SelectItem>)}</SelectContent></Select></div><Choice label="身份要求" value={mode === "reified" ? "required" : reification.identity} options={["optional", "required"]} optionLabels={{ optional: "按需生成稳定 ID", required: "必须拥有稳定 ID" }} onChange={(value) => setReification({ identity: value as "optional" | "required" })} /></div><div className="border-t border-blue-100 pt-4"><div className="mb-2 flex items-center justify-between"><p className="text-sm font-semibold">节点属性与判断类型</p><button className="text-xs font-medium text-blue-700 hover:underline" onClick={() => setCreateMode((value) => !value)}>{createMode ? "取消新建" : "新建并绑定"}</button></div>{createMode && <div className="mb-3 space-y-3 rounded-xl border border-blue-200 bg-white p-3"><div className="grid grid-cols-2 gap-3"><Input value={newId} onChange={(event) => setNewId(event.target.value)} placeholder="稳定 ID，例如 contract_value" aria-invalid={duplicateNewId || invalidNewId} /><Input value={newLabel} onChange={(event) => setNewLabel(event.target.value)} placeholder="显示名称，例如 合同金额" /></div><Textarea value={newDescription} onChange={(event) => setNewDescription(event.target.value)} placeholder="语义说明" /><div className="grid grid-cols-2 gap-3"><Choice label="值类型" value={newValueType} options={["string", "text", "number", "quantity", "currency", "enum", "boolean", "date", "node_ref", "json"]} onChange={setNewValueType} /><Choice label="存储模式" value={newStorage} options={["attr", "assertion", "external"]} onChange={setNewStorage} /></div>{(duplicateNewId || invalidNewId) && <p className="text-xs text-rose-600">{duplicateNewId ? "ID 已存在。" : "请使用小写稳定标识。"}</p>}<div className="flex justify-end"><Button size="sm" disabled={!createValid} onClick={createAndBind}>创建草稿并绑定</Button></div></div>}<div className="flex gap-2"><Select value={predicateToAdd} onValueChange={setPredicateToAdd}><SelectTrigger className="min-w-0 flex-1 bg-white"><SelectValue placeholder="选择已注册判断类型" /></SelectTrigger><SelectContent>{availablePredicates.map((item) => <SelectItem key={item.id} value={item.id}>{item.label} · {item.id}</SelectItem>)}</SelectContent></Select><Button variant="outline" disabled={!predicateToAdd} onClick={addPredicate}><Plus />绑定</Button></div><div className="mt-3 space-y-2">{reificationProperties.map((binding) => <div key={binding.predicateId} className="rounded-xl border border-blue-100 bg-white p-3"><div className="flex items-center gap-3"><span className="flex size-7 items-center justify-center rounded-lg bg-violet-600 text-[11px] font-bold text-white">P</span><div className="min-w-0 flex-1"><p className="truncate text-sm font-medium">{predicateLabel(binding.predicateId)}</p><p className="truncate font-mono text-[10px] text-slate-400">{binding.predicateId}</p></div><label className="flex items-center gap-2 text-xs"><Switch checked={binding.required} onCheckedChange={(value) => updateProperty(binding.predicateId, { required: value })} />必填</label><Button variant="ghost" size="icon-sm" className="text-rose-600" onClick={() => setReificationProperties(reificationProperties.filter((item) => item.predicateId !== binding.predicateId))}><Unlink /></Button></div><div className="mt-3 grid grid-cols-2 gap-3"><Choice label="节点内基数" value={binding.cardinality} options={["inherit", "one", "many", "temporal_many"]} optionLabels={bindingCardinalityLabels} onChange={(value) => updateProperty(binding.predicateId, { cardinality: value as ConceptBinding["cardinality"] })} /><Choice label="展示分组" value={binding.group} options={Object.keys(bindingGroupLabels)} optionLabels={bindingGroupLabels} onChange={(value) => updateProperty(binding.predicateId, { group: value as ConceptBinding["group"] })} /></div></div>)}{!reificationProperties.length && <p className="rounded-xl border border-dashed border-blue-200 p-5 text-center text-xs text-blue-700">尚未为实体关系节点绑定判断类型。</p>}</div></div></section>}
    </div></ScrollArea>
    <DialogFooter className="border-t px-6 py-4"><p className="mr-auto text-xs text-slate-500">修改已自动暂存到当前 ChangeSet</p><Button onClick={() => onOpenChange(false)}>完成</Button></DialogFooter>
  </DialogContent></Dialog>;
}

function ModelEditor({ definition, definitions, onUpdate }: { definition: OntologyDefinition; definitions: OntologyDefinition[]; onUpdate: (next: OntologyDefinition) => void }) {
  const [version, setVersion] = useState(String(definition.config.version ?? "1.0.0"));
  const [appliesTo, setAppliesTo] = useState(String(definition.config.applies_to ?? ""));
  const [outputPredicate, setOutputPredicate] = useState(String(definition.config.output_predicate ?? ""));
  const [outputUnit, setOutputUnit] = useState(String(definition.config.output_unit ?? ""));
  const [inputsText, setInputsText] = useState(JSON.stringify(definition.config.inputs ?? [], null, 2));
  const [formulaText, setFormulaText] = useState(JSON.stringify(definition.config.formula ?? {}, null, 2));
  const [sampleText, setSampleText] = useState("{}");
  const [error, setError] = useState("");
  const [preview, setPreview] = useState("");
  const [busy, setBusy] = useState(false);
  const candidate = () => {
    const inputs = JSON.parse(inputsText);
    const formula = JSON.parse(formulaText);
    if (!Array.isArray(inputs) || !formula || typeof formula !== "object" || Array.isArray(formula)) throw new Error("输入须为数组，公式须为对象");
    return { ...definition.config, id: definition.id.replace(/^model:/, ""), version, description: definition.description,
      applies_to: appliesTo || null, output_predicate: outputPredicate || null,
      output_unit: outputUnit, inputs, formula };
  };
  const save = () => {
    try { const config = candidate(); onUpdate({ ...definition, config }); setError(""); toast.success("模型草稿已暂存；提交后由服务端校验和审核"); }
    catch (cause) { setError(cause instanceof Error ? cause.message : "模型配置无效"); }
  };
  const runPreview = async () => {
    try {
      const model = candidate(); const inputs = JSON.parse(sampleText);
      if (!inputs || typeof inputs !== "object" || Array.isArray(inputs)) throw new Error("试算输入须为 JSON 对象");
      setBusy(true); const result = await previewModel(model as Record<string, unknown>, inputs as Record<string, unknown>);
      setPreview(`${result.data.output.value} ${result.data.output.unit}`); setError("");
    } catch (cause) { setError(cause instanceof Error ? cause.message : "试算失败"); setPreview(""); }
    finally { setBusy(false); }
  };
  return <section className="space-y-3 rounded-2xl border border-rose-200 bg-white p-4">
    <div><p className="text-sm font-semibold text-rose-900">声明式公式</p><p className="mt-1 text-xs text-slate-500">仅支持注册的运算节点；修改公式时递增语义版本。试算不会写入结果或真源。</p></div>
    <div className="grid grid-cols-2 gap-3"><div><Label>版本</Label><Input value={version} onChange={(event) => setVersion(event.target.value)} placeholder="1.0.1" /></div><RegistryPicker label="输出单位" value={outputUnit} options={modelUnitOptions(definitions)} onChange={(next) => setOutputUnit(String(next))} /></div>
    <div className="grid grid-cols-2 gap-3"><RegistryPicker label="适用概念" value={appliesTo} options={conceptOptions(definitions)} optional onChange={(next) => { setAppliesTo(String(next)); setOutputPredicate(""); }} /><RegistryPicker label="输出判断类型" value={outputPredicate} options={outputPredicateOptions(definitions, appliesTo)} optional disabled={!appliesTo} onChange={(next) => setOutputPredicate(String(next))} /></div>
    <div><Label>输入与属性绑定 · JSON 数组</Label><Textarea className="min-h-32 font-mono text-xs" value={inputsText} onChange={(event) => setInputsText(event.target.value)} /></div>
    <div><Label>公式表达式 · JSON 对象</Label><Textarea className="min-h-40 font-mono text-xs" value={formulaText} onChange={(event) => setFormulaText(event.target.value)} /></div>
    <Button size="sm" onClick={save}>保存公式草稿</Button>
    <div className="border-t pt-3"><Label>试算输入 · JSON 对象</Label><Textarea className="min-h-24 font-mono text-xs" value={sampleText} onChange={(event) => setSampleText(event.target.value)} /><Button className="mt-2" size="sm" variant="outline" disabled={busy} onClick={() => void runPreview()}>试算当前草稿</Button>{preview && <p className="mt-2 text-sm font-semibold text-emerald-700">试算结果：{preview}</p>}</div>
    {error && <p role="alert" className="text-xs text-rose-700">{error}</p>}
  </section>;
}

function ModelHistoryPanel({ definition, onUpdate }: { definition: OntologyDefinition; onUpdate: (next: OntologyDefinition) => void }) {
  const [revisions, setRevisions] = useState<ModelRevision[]>([]);
  const [error, setError] = useState("");
  useEffect(() => {
    let active = true;
    void loadModelHistory(String(definition.config.model_id ?? definition.id.replace(/^model:/, "")))
      .then((items) => { if (active) { setRevisions(items); setError(""); } })
      .catch((cause) => { if (active) setError(cause instanceof Error ? cause.message : "无法读取模型历史"); });
    return () => { active = false; };
  }, [definition.id, definition.config.model_id]);
  const restore = (revision: ModelRevision) => {
    const [major, minor, patch] = String(definition.config.version ?? "1.0.0").split(".").map(Number);
    if (![major, minor, patch].every(Number.isInteger)) return;
    const nextVersion = `${major}.${minor}.${patch + 1}`;
    onUpdate({ ...definition, lifecycle: "active", description: String(revision.definition.description ?? definition.description),
      config: { ...revision.definition, model_id: String(revision.definition.id), version: nextVersion } });
    toast.success(`已将 ${revision.version} 的公式内容暂存为 ${nextVersion} 草稿`);
  };
  return <section className="rounded-2xl border border-rose-200 bg-white p-4"><p className="text-sm font-semibold text-rose-900">已记录模型版本</p><p className="mt-1 text-xs text-slate-500">恢复会建立更高的新版本，并进入 ChangeSet 审核。</p>
    {error ? <p className="mt-3 text-xs text-slate-500">{error}</p> : revisions.length ? <div className="mt-3 space-y-2">{revisions.map((revision) => <div key={revision.id} className="flex items-center justify-between gap-2 rounded-lg border border-rose-100 p-2"><div><p className="text-xs font-medium">v{revision.version}</p><p className="text-[10px] text-slate-500">{new Date(revision.recorded_at).toLocaleString("zh-CN")}</p></div><Button size="sm" variant="outline" onClick={() => restore(revision)}>以此内容新建版本</Button></div>)}</div> : <p className="mt-3 text-xs text-slate-500">尚无已编译版本。</p>}
  </section>;
}

function Inspector({ definition, definitions, relatedDefinitions, expert, editSignal, relatedRequest, onUpdate, onDeprecate, onDelete, onSelectRelated, onCollapse, onManageBindings, onManageEndpoints }: {
  definition: OntologyDefinition | null; definitions: OntologyDefinition[]; relatedDefinitions: OntologyDefinition[]; expert: boolean; editSignal: number; relatedRequest: { id: string } | null;
  onUpdate: (next: OntologyDefinition, type?: ChangeOperationType) => void; onDeprecate: () => void; onDelete: () => void;
  onSelectRelated: (id: string) => void; onCollapse: () => void; onManageBindings: (id: string) => void; onManageEndpoints: (id: string) => void;
}) {
  const [editing, setEditing] = useState(false);
  const [deleteOpen, setDeleteOpen] = useState(false);
  const relatedSectionRef = useRef<HTMLElement>(null);
  useEffect(() => {
    if (editSignal <= 0) return;
    const frame = requestAnimationFrame(() => setEditing(true));
    return () => cancelAnimationFrame(frame);
  }, [editSignal]);
  useEffect(() => {
    if (relatedRequest?.id !== definition?.id) return;
    const frame = requestAnimationFrame(() => relatedSectionRef.current?.scrollIntoView({ behavior: "smooth", block: "start" }));
    return () => cancelAnimationFrame(frame);
  }, [definition?.id, relatedRequest]);
  if (!definition) return <div className="flex h-full items-center justify-center p-8 text-center"><div><Network className="mx-auto size-8 text-slate-300" /><p className="mt-3 text-sm font-medium">选择一个定义</p><p className="mt-1 text-xs text-slate-500">查看配置、引用与维护操作。</p></div></div>;
  const meta = kindMeta[definition.kind];
  const editable = !definition.readOnly;
  const conceptBindingRefs = definition.kind === "predicate" ? relatedDefinitions.filter((item) => item.kind === "concept" && (item.bindings ?? []).some((binding) => binding.predicateId === definition.id)).length : 0;
  const reificationBindingRefs = definition.kind === "predicate" ? relatedDefinitions.filter((item) => item.kind === "relation" && (item.reification?.properties ?? []).some((binding) => binding.predicateId === definition.id)).length : 0;
  const relationConstraintRefs = definition.kind === "concept" ? relatedDefinitions.filter((item) => item.kind === "relation" && (item.endpoints ?? []).some((endpoint) => endpoint.sourceConceptId === definition.id || endpoint.targetConceptId === definition.id)).length : 0;
  const reificationNodeRefs = definition.kind === "concept" ? relatedDefinitions.filter((item) => item.kind === "relation" && item.reification?.nodeType === definition.id).length : 0;
  const domainScopeRefs = definition.kind === "concept" ? relatedDefinitions.filter((item) => item.kind === "domain" && (item.conceptScopes ?? []).includes(definition.id)).length : 0;
  const deleteBlocked = definition.refs > 0 || definition.files.length > 0 || conceptBindingRefs > 0 || reificationBindingRefs > 0 || relationConstraintRefs > 0 || reificationNodeRefs > 0 || domainScopeRefs > 0;
  const update = (field: "label" | "description" | "lifecycle", value: string) => onUpdate({ ...definition, [field]: value });
  const updateConfig = (field: string, value: string | null) => onUpdate({ ...definition, config: { ...definition.config, [field]: value } });
  return <div className="relative flex h-full min-h-0 flex-col bg-[#fafaf8]">
    <div className="border-b border-[#deded9] px-4 py-3"><div className="mb-3 flex items-center justify-between"><p className="text-xs font-semibold text-[#85857d]">详情面板</p><Button variant="ghost" size="icon-xs" onClick={onCollapse} aria-label="收起右侧详情"><PanelRightClose /></Button></div><div className="flex items-start justify-between gap-3">
      <div className="flex min-w-0 gap-3"><span className="flex size-10 shrink-0 items-center justify-center rounded-xl text-sm font-bold text-white" style={{ background: meta.color }}>{meta.short}</span><div className="min-w-0"><div className="flex items-center gap-2"><h2 className="truncate font-semibold">{definition.label}</h2>{definition.readOnly && <Badge variant="outline">只读</Badge>}</div><p className="mt-1 truncate font-mono text-[11px] text-slate-400">{definition.id}</p></div></div>
      {editable && <Button variant={editing ? "secondary" : "outline"} size="sm" className="rounded-lg" onClick={() => setEditing(!editing)}><PencilLine />{editing ? "完成" : "编辑"}</Button>}
    </div></div>
    <ScrollArea className="min-h-0 flex-1"><div className="space-y-5 p-5 pb-28">
      {editing ? <>
        <div className="space-y-1.5"><Label>显示名称</Label><Input value={definition.label} onChange={(event) => update("label", event.target.value)} /></div>
        <div className="space-y-1.5"><Label>语义说明</Label><Textarea value={definition.description} onChange={(event) => update("description", event.target.value)} /></div>
        {definition.kind !== "schema" && <Choice label="生命周期" value={definition.lifecycle} options={definition.kind === "business_constraint" || definition.kind === "business_rule" ? ["draft", "active", "deprecated"] : ["draft", "active", "deprecated", "merged", "retired"]} onChange={(value) => update("lifecycle", value)} />}
        {definition.kind === "schema" && <SchemaRequirementsEditor definition={definition} onUpdate={onUpdate} />}
        {definition.kind === "predicate" && <div className="space-y-4 rounded-2xl border border-violet-100 bg-violet-50/50 p-4">
          <p className="flex items-center gap-2 text-sm font-semibold text-violet-950"><Braces className="size-4" />判断类型配置</p>
          <div className="grid grid-cols-2 gap-3"><Choice label="值类型" value={String(definition.config.value_type)} options={["string", "text", "number", "quantity", "currency", "enum", "boolean", "date", "node_ref", "json"]} onChange={(v) => onUpdate({ ...definition, config: { ...definition.config, value_type: v, dimension: null, units: [], default_unit: null } })} /><Choice label="基数" value={String(definition.config.cardinality)} options={["one", "many", "temporal_many"]} onChange={(v) => updateConfig("cardinality", v)} /><Choice label="存储模式" value={String(definition.config.storage_mode)} options={["attr", "assertion", "external"]} onChange={(v) => updateConfig("storage_mode", v)} /><Choice label="写入策略" value={String(definition.config.write)} options={["reviewed", "auto", "prohibited"]} onChange={(v) => updateConfig("write", v)} /></div>
          <Choice label="权威来源" value={String(definition.config.authority)} options={authorityOptions} onChange={(v) => updateConfig("authority", v)} />
          <Choice label="新鲜度" value={String(definition.config.freshness)} options={freshnessOptions} onChange={(v) => updateConfig("freshness", v)} />
          <Choice label="溯源等级" value={String(definition.config.provenance_tier)} options={provenanceOptions} onChange={(v) => updateConfig("provenance_tier", v)} />
          <div className="space-y-1.5"><Label>替代判断类型</Label><Input placeholder="例如 risk_level_v2" value={String(definition.config.equivalent_to ?? "")} onChange={(event) => updateConfig("equivalent_to", event.target.value || null)} /></div>
          {(definition.config.value_type === "quantity" || definition.config.value_type === "currency") && <div className="space-y-2 border-t border-violet-100 pt-3">
            <p className="text-xs font-semibold text-violet-900">单位契约</p>
            {definition.config.value_type === "quantity" && <RegistryPicker label="量纲（物理量）" value={String(definition.config.dimension ?? "")} options={dimensionOptions(definitions)} onChange={(next) => onUpdate({ ...definition, config: { ...definition.config, dimension: String(next), units: [], default_unit: null } })} />}
            <RegistryPicker label={definition.config.value_type === "currency" ? "允许的货币单位" : "允许的物理单位"} value={asStringArray(definition.config.units)} options={definition.config.value_type === "currency" ? currencyOptions(definitions) : physicalUnitOptions(definitions, String(definition.config.dimension ?? ""))} multiple disabled={definition.config.value_type === "quantity" && !definition.config.dimension} onChange={(next) => {
              const units = next as string[];
              onUpdate({ ...definition, config: { ...definition.config, units, default_unit: units.includes(String(definition.config.default_unit ?? "")) ? definition.config.default_unit : null } });
            }} />
            <RegistryPicker label="默认单位（可留空）" value={String(definition.config.default_unit ?? "")} options={(definition.config.value_type === "currency" ? currencyOptions(definitions) : physicalUnitOptions(definitions, String(definition.config.dimension ?? ""))).filter((item) => asStringArray(definition.config.units).includes(item.value))} optional disabled={asStringArray(definition.config.units).length === 0} onChange={(next) => updateConfig("default_unit", String(next) || null)} />
          </div>}
        </div>}
        {definition.kind === "model" && <ModelEditor definition={definition} definitions={definitions} onUpdate={onUpdate} />}
        {(definition.kind === "business_constraint" || definition.kind === "business_rule") && <BusinessEditor definition={definition} definitions={definitions} onUpdate={onUpdate} />}
        {(definition.kind === "concept" || definition.kind === "relation") && <div className="space-y-4">
          <Alert className="border-sky-200 bg-sky-50"><ShieldCheck className="size-4" /><AlertTitle>目标契约字段</AlertTitle><AlertDescription>生命周期与替代项用于验证未来体验，当前 core.yaml 尚未正式声明。</AlertDescription></Alert>
          <div className="space-y-1.5"><Label>替代定义</Label><Input placeholder="例如 partner_of" value={String(definition.config.equivalent_to ?? "")} onChange={(event) => updateConfig("equivalent_to", event.target.value || null)} /></div>
          <div className="space-y-1.5"><Label>迁移说明</Label><Textarea placeholder="说明现有引用如何迁移或为何可以保留。" value={String(definition.config.migration_notes ?? "")} onChange={(event) => updateConfig("migration_notes", event.target.value || null)} /></div>
        </div>}
      </> : <>
        <section><p className="inspector-label">语义说明</p><p className="mt-2 text-sm leading-6 text-slate-700">{definition.description}</p></section>
        <div className="grid grid-cols-3 gap-2"><Metric label="生命周期" value={definition.lifecycle} /><Metric label="引用" value={`${definition.refs}`} /><Metric label="文件" value={`${definition.files.length}`} /></div>
        {definition.kind === "relation" && !(definition.endpoints ?? []).length && <Alert className="border-amber-200 bg-amber-50"><AlertTriangle className="size-4" /><AlertTitle>未声明类型约束</AlertTitle><AlertDescription>当前关系没有 domain/range，本图不会推断起点或终点类型。</AlertDescription></Alert>}
        <section><p className="inspector-label">关键配置</p><dl className="mt-2 overflow-hidden rounded-xl border border-slate-200 bg-slate-50/70">{Object.entries(definition.config).filter(([key]) => definition.kind !== "predicate" || expert || ["value_type", "storage_mode", "write", "authority", "freshness", "provenance_tier"].includes(key)).map(([key, value]) => <div key={key} className="border-b border-slate-200 px-3 py-2.5 last:border-0"><dt className="font-mono text-[11px] text-slate-500">{key}</dt>{typeof value === "object" && value !== null ? <dd><pre className="mt-2 max-h-48 overflow-auto whitespace-pre-wrap break-words rounded-lg bg-slate-900 p-3 text-[11px] leading-5 text-cyan-100">{formatConfigValue(value)}</pre></dd> : <dd className="mt-1 break-words text-xs font-medium">{formatConfigValue(value)}</dd>}</div>)}</dl></section>
      </>}
      {definition.kind === "concept" && <section className="overflow-hidden rounded-2xl border border-violet-200 bg-white shadow-sm"><div className="flex items-center justify-between border-b border-violet-100 bg-violet-50/60 px-4 py-3"><div><p className="flex items-center gap-2 text-sm font-semibold text-violet-950"><ListChecks className="size-4" />属性与判断类型</p><p className="mt-1 text-xs text-violet-700/70">概念形状 · 目标契约</p></div><Button size="sm" variant="outline" className="border-violet-200 bg-white text-violet-700" onClick={() => onManageBindings(definition.id)}>管理</Button></div><div className="space-y-2 p-3">{(definition.bindings ?? []).length ? (definition.bindings ?? []).map((binding) => {
        const predicate = relatedDefinitions.find((item) => item.id === binding.predicateId);
        return <button key={binding.predicateId} onClick={() => predicate && onSelectRelated(predicate.id)} className="flex w-full items-center gap-3 rounded-xl border border-slate-200 px-3 py-2.5 text-left hover:border-violet-200 hover:bg-violet-50/40"><span className="flex size-7 shrink-0 items-center justify-center rounded-lg bg-violet-600 text-[11px] font-bold text-white">P</span><span className="min-w-0 flex-1"><span className="block truncate text-sm font-medium text-slate-800">{predicate?.label ?? binding.predicateId}</span><span className="block truncate font-mono text-[10px] text-slate-400">{binding.predicateId}</span></span><span className="text-right"><span className="block text-[11px] font-medium text-slate-600">{binding.required ? "必填" : "可选"}</span><span className="block text-[10px] text-slate-400">{binding.cardinality === "inherit" ? "继承基数" : binding.cardinality}</span></span></button>;
      }) : <button onClick={() => onManageBindings(definition.id)} className="w-full rounded-xl border border-dashed border-violet-200 px-3 py-5 text-center text-sm text-violet-700 hover:bg-violet-50"><PlusCircle className="mx-auto mb-2 size-5" />绑定第一个判断类型</button>}</div></section>}
      {definition.kind === "relation" && <section className="overflow-hidden rounded-2xl border border-amber-200 bg-white shadow-sm"><div className="flex items-center justify-between border-b border-amber-100 bg-amber-50/70 px-4 py-3"><div><p className="flex items-center gap-2 text-sm font-semibold text-amber-950"><Network className="size-4" />关系建模</p><p className="mt-1 text-xs text-amber-700/70">{relationModeLabel(definition.relationMode)} · domain → relation → range</p></div><Button size="sm" variant="outline" className="border-amber-200 bg-white text-amber-700" onClick={() => onManageEndpoints(definition.id)}>管理</Button></div><div className="space-y-2 p-3">{(definition.endpoints ?? []).length ? (definition.endpoints ?? []).map((endpoint, index) => {
        const source = relatedDefinitions.find((item) => item.id === endpoint.sourceConceptId);
        const target = relatedDefinitions.find((item) => item.id === endpoint.targetConceptId);
        return <button key={`${endpoint.sourceConceptId}-${endpoint.targetConceptId}-${index}`} onClick={() => source && onSelectRelated(source.id)} className="flex w-full items-center gap-2 rounded-xl border border-slate-200 px-3 py-3 text-left hover:border-amber-200 hover:bg-amber-50/40"><span className="min-w-0 flex-1"><span className="block truncate text-sm font-medium">{source?.label ?? endpoint.sourceConceptId}</span><span className="block truncate font-mono text-[10px] text-slate-400">{endpoint.sourceConceptId}</span></span><ArrowRight className="size-4 shrink-0 text-amber-600" /><span className="min-w-0 flex-1 text-right"><span className="block truncate text-sm font-medium">{target?.label ?? endpoint.targetConceptId}</span><span className="block truncate font-mono text-[10px] text-slate-400">{endpoint.targetConceptId}</span></span></button>;
      }) : <button onClick={() => onManageEndpoints(definition.id)} className="w-full rounded-xl border border-dashed border-amber-200 px-3 py-5 text-center text-sm text-amber-700 hover:bg-amber-50"><PlusCircle className="mx-auto mb-2 size-5" />声明第一个有向连接</button>}</div>{definition.relationMode && definition.relationMode !== "simple" && definition.reification && <div className="border-t border-blue-100 bg-blue-50/50 px-4 py-3"><div className="flex items-center justify-between"><div><p className="text-xs font-semibold text-blue-950">实体化节点：{relatedDefinitions.find((item) => item.id === definition.reification?.nodeType)?.label ?? definition.reification.nodeType}</p><p className="mt-1 text-[11px] text-blue-700/70">{definition.reification.identity === "required" ? "必须拥有稳定 ID" : "按需生成稳定 ID"} · {definition.reification.properties.length} 个判断类型</p></div><Badge variant="outline" className="border-blue-200 text-blue-700">实体节点</Badge></div><div className="mt-2 flex flex-wrap gap-1">{definition.reification.properties.map((binding) => <span key={binding.predicateId} className="rounded-md border border-blue-100 bg-white px-2 py-1 text-[10px] text-blue-800">{relatedDefinitions.find((item) => item.id === binding.predicateId)?.label ?? binding.predicateId}{binding.required ? " · 必填" : ""}</span>)}</div></div>}</section>}
      {definition.kind === "domain" && <section className="overflow-hidden rounded-2xl border border-cyan-200 bg-white shadow-sm"><div className="border-b border-cyan-100 bg-cyan-50/70 px-4 py-3"><p className="flex items-center gap-2 text-sm font-semibold text-cyan-950"><Focus className="size-4" />入口概念</p><p className="mt-1 text-xs leading-5 text-cyan-800/70">先解析这些概念，再沿其有向关系遍历，并按判断类型优先级排序。</p></div><div className="space-y-2 p-3">{(definition.conceptScopes ?? []).map((conceptId) => {
        const concept = relatedDefinitions.find((item) => item.id === conceptId);
        return <button key={conceptId} onClick={() => concept && onSelectRelated(concept.id)} className="flex w-full items-center gap-3 rounded-xl border border-slate-200 px-3 py-2.5 text-left hover:border-cyan-200 hover:bg-cyan-50/50"><span className="flex size-7 items-center justify-center rounded-lg bg-blue-600 text-[11px] font-bold text-white">C</span><span className="min-w-0 flex-1"><span className="block truncate text-sm font-medium">{concept?.label ?? conceptId}</span><span className="block truncate font-mono text-[10px] text-slate-400">{conceptId}</span></span><ArrowRight className="size-4 text-cyan-600" /></button>;
      })}</div><div className="border-t border-cyan-100 px-4 py-3 text-xs leading-5 text-slate-500">Domain Pack 中现有 relation_types 与 predicate_priority 保留为概念解析后的遍历与排序策略。</div></section>}
      {definition.kind === "model" && <ModelHistoryPanel definition={definition} onUpdate={onUpdate} />}
      <section ref={relatedSectionRef}><div className="flex items-center justify-between"><p className="inspector-label">关联定义</p><span className="text-xs text-[#999991]">{relatedDefinitions.length}</span></div><div className="mt-2 flex flex-wrap gap-1.5">{relatedDefinitions.length ? relatedDefinitions.map((item) => <button key={item.id} onClick={() => onSelectRelated(item.id)} className="inline-flex items-center gap-1.5 rounded-lg border border-[#deded9] bg-white px-2.5 py-1.5 text-xs font-medium text-[#4b4b46] shadow-sm hover:border-[#bcb4e9] hover:bg-[#f0eefb]"><span className="size-1.5 rounded-full" style={{ background: kindMeta[item.kind].color }} />{item.label}</button>) : <span className="text-sm text-[#999991]">暂无已声明关联</span>}</div></section>
      <section><p className="inspector-label">影响与来源</p><div className="mt-2 space-y-2">{definition.files.length ? definition.files.map((file) => <div key={file} className="flex gap-3 rounded-xl border border-slate-200 bg-slate-50 px-3 py-3"><FileCode2 className="mt-0.5 size-4 shrink-0 text-slate-400" /><div className="min-w-0"><p className="break-all font-mono text-[11px] leading-5 text-slate-700">{file}</p><p className="text-xs text-slate-400">只读引用 · 不在本原型中修改</p></div></div>) : <div className="rounded-xl border border-dashed border-slate-200 p-5 text-center text-sm text-slate-400">暂未发现引用</div>}</div></section>
      {expert && <section><p className="inspector-label">源文件</p><p className="mt-2 rounded-xl bg-slate-950 p-4 font-mono text-xs text-cyan-200">{definition.sourcePath}</p></section>}
    </div></ScrollArea>
    {editable && definition.kind !== "schema" && <div className="absolute inset-x-0 bottom-0 flex justify-between border-t border-slate-200 bg-white/95 px-5 py-3 backdrop-blur"><Button variant="ghost" size="sm" className="text-rose-600" onClick={() => setDeleteOpen(true)}><Trash2 />删除</Button><Button variant="outline" size="sm" onClick={onDeprecate} disabled={definition.lifecycle === "deprecated"}><Undo2 />标记弃用</Button></div>}
    <AlertDialog open={deleteOpen} onOpenChange={setDeleteOpen}><AlertDialogContent><AlertDialogHeader><AlertDialogTitle>{deleteBlocked ? "不能直接删除已发布或被绑定的定义" : `删除 ${definition.label}？`}</AlertDialogTitle><AlertDialogDescription>{deleteBlocked ? `该定义有 ${definition.refs} 个知识引用、${conceptBindingRefs + reificationBindingRefs} 个属性或实体关系模板绑定、${relationConstraintRefs + reificationNodeRefs} 个关系约束和 ${domainScopeRefs} 个领域入口引用，或已经发布。请先解除关联，或改为弃用并指定迁移说明。` : "此草稿尚未被引用或绑定，可以从当前 ChangeSet 中删除。"}</AlertDialogDescription></AlertDialogHeader><AlertDialogFooter><AlertDialogCancel>取消</AlertDialogCancel>{deleteBlocked ? <AlertDialogAction onClick={onDeprecate}>改为弃用</AlertDialogAction> : <AlertDialogAction variant="destructive" onClick={onDelete}>确认删除</AlertDialogAction>}</AlertDialogFooter></AlertDialogContent></AlertDialog>
  </div>;
}

function CreateDialog({ open, onOpenChange, definitions, onCreate }: { open: boolean; onOpenChange: (value: boolean) => void; definitions: OntologyDefinition[]; onCreate: (definition: OntologyDefinition) => void }) {
  const [kind, setKind] = useState<DefinitionKind>("concept"); const [id, setId] = useState(""); const [label, setLabel] = useState(""); const [description, setDescription] = useState("");
  const normalizedId = ["model", "unit", "currency", "business_constraint", "business_rule"].includes(kind) ? `${kind}:${id.trim()}` : id.trim();
  const duplicate = definitions.some((item) => item.id === normalizedId); const invalidId = Boolean(id && !(kind === "unit" ? /^[A-Za-z][A-Za-z0-9_]*$/ : kind === "currency" ? /^[A-Z]{3}$/ : kind === "business_constraint" || kind === "business_rule" ? /^[a-z][a-z0-9_]*$/ : /^[a-z][a-z0-9_:-]*$/).test(id)); const valid = Boolean(id.trim() && label.trim() && description.trim() && !duplicate && !invalidId);
  const submit = () => {
    if (!valid) return;
    const sourcePath = kind === "predicate" ? `control/predicates/${id}.yaml` : kind === "model" ? `control/models/${id}.yaml` : kind === "business_constraint" ? `control/constraints/business/${id}.yaml` : kind === "business_rule" ? `control/rules/business/${id}.yaml` : kind === "unit" ? `control/units/${id}.yaml` : kind === "currency" ? `control/currencies/${id}.yaml` : "control/ontology/core.yaml";
    const examplePredicate = definitions.find((item) => item.kind === "predicate" && item.id === "rated_power") ?? definitions.find((item) => item.kind === "predicate");
    const exampleConcept = definitions.find((item) => item.kind === "concept" && item.id === "equipment") ?? definitions.find((item) => item.kind === "concept");
    const businessConfig: Record<string, ConfigValue> = { format: kind === "business_rule" ? "knowledgeos.business-rule.v1" : "knowledgeos.business-constraint.v1", version: "1.0.0", scope: { subject_concept: exampleConcept?.id ?? "", ...(kind === "business_rule" ? { candidate_concept: exampleConcept?.id ?? "" } : {}) }, inputs: kind === "business_rule" ? [{ id: "required", role: "subject", predicate: examplePredicate?.id ?? "" }, { id: "available", role: "candidate", predicate: examplePredicate?.id ?? "" }] : [{ id: "measured", role: "subject", predicate: examplePredicate?.id ?? "" }], checks: kind === "business_rule" ? [{ left: "available", operator: "gte", right: { input: "required" }, message: "候选实体必须满足主体要求" }] : [{ left: "measured", operator: "gt", right: { value: { value: 0, unit: "W" } }, message: "数值必须大于零" }] };
    const config: Record<string, ConfigValue> = kind === "predicate" ? { value_type: "string", cardinality: "one", storage_mode: "attr", authority: "git_authored", freshness: "stable", provenance_tier: "C", write: "reviewed", temporal: "none", evidence: "optional", history: "git_only", embedding: false, equivalent_to: null } : kind === "model" ? { id: id.trim(), model_id: id.trim(), version: "1.0.0", inputs: [], formula: {}, output_unit: "" } : kind === "business_constraint" || kind === "business_rule" ? businessConfig : kind === "unit" ? { id: id.trim(), version: "1.0.0", dimension: "", factor_to_base: "", base: false } : kind === "currency" ? { id: id.trim(), version: "1.0.0", minor_units: 2 } : kind === "relation" ? { domain_range: "undeclared", relation_mode: "simple", schema_support: "target-contract" } : { schema_support: "target-contract" };
    const definition: OntologyDefinition = { id: normalizedId, kind, label: label.trim(), description: description.trim(), lifecycle: ["model", "unit", "currency"].includes(kind) ? "active" : "draft", sourcePath, refs: 0, files: [], endpoints: kind === "relation" ? [] : undefined, relationMode: kind === "relation" ? "simple" : undefined, config };
    onCreate(definition); setId(""); setLabel(""); setDescription(""); onOpenChange(false);
  };
  return <Dialog open={open} onOpenChange={onOpenChange}><DialogContent className="sm:max-w-xl"><DialogHeader><DialogTitle>新增控制面定义</DialogTitle><DialogDescription>新定义先以草稿进入当前 ChangeSet，不会直接写入 Git 真源。</DialogDescription></DialogHeader><div className="space-y-4 py-2"><div className="grid grid-cols-3 gap-2">{editableKinds.map((item) => <button key={item} onClick={() => setKind(item)} className={cn("rounded-xl border px-3 py-3 text-left", kind === item ? "border-slate-950 bg-slate-950 text-white" : "border-slate-200")}><span className="block text-sm font-semibold">{kindMeta[item].label}</span><span className="text-[11px] opacity-60">{item}</span></button>)}</div><div className="space-y-1.5"><Label>稳定 ID</Label><Input value={id} onChange={(e) => setId(e.target.value)} placeholder={kind === "currency" ? "例如 CNY" : kind === "unit" ? "例如 kV" : "例如 equipment_rate"} aria-invalid={duplicate || invalidId} />{duplicate && <p className="text-xs text-rose-600">该 ID 已存在。</p>}{invalidId && <p className="text-xs text-rose-600">ID 格式无效。</p>}</div><div className="space-y-1.5"><Label>显示名称</Label><Input value={label} onChange={(e) => setLabel(e.target.value)} placeholder="例如 设备折旧率" /></div><div className="space-y-1.5"><Label>语义说明</Label><Textarea value={description} onChange={(e) => setDescription(e.target.value)} placeholder="说明定义边界及使用场景。" /></div></div><DialogFooter><Button variant="outline" onClick={() => onOpenChange(false)}>取消</Button><Button disabled={!valid} onClick={submit}>添加到变更</Button></DialogFooter></DialogContent></Dialog>;
}

function SubmitDialog({ open, onOpenChange, operations, warnings, onSubmit }: { open: boolean; onOpenChange: (value: boolean) => void; operations: ChangeOperation[]; warnings: string[]; onSubmit: (reason: string) => void }) {
  const [reason, setReason] = useState(""); const risk = operations.some((op) => ["schema", "model", "business_constraint", "business_rule"].includes(op.targetKind)) ? "高" : warnings.length || operations.some((op) => (op.before?.refs ?? 0) > 2) ? "中" : "低";
  return <Dialog open={open} onOpenChange={onOpenChange}><DialogContent className="max-h-[88vh] overflow-hidden p-0 sm:max-w-3xl"><DialogHeader className="border-b px-6 py-5"><DialogTitle>生成 ChangeSet</DialogTitle><DialogDescription>逐项检查结构化差异，再提交给本体管理员审核。</DialogDescription></DialogHeader><ScrollArea className="max-h-[58vh]"><div className="space-y-5 p-6"><div className="flex items-center gap-3 rounded-xl border bg-slate-50 p-4"><ShieldCheck className="size-5 text-sky-600" /><div className="flex-1"><p className="text-sm font-semibold">校验通过</p><p className="text-xs text-slate-500">{operations.length} 项变更 · {risk}风险 · {warnings.length} 个警告</p></div></div>{operations.map((op) => <div key={op.id} className="overflow-hidden rounded-2xl border"><div className="flex items-center justify-between px-4 py-3"><div><p className="text-sm font-semibold">{op.targetId}</p><p className="text-xs text-slate-400">{op.type} · {kindMeta[op.targetKind].label}</p></div><Badge variant="outline">{op.after?.sourcePath ?? op.before?.sourcePath}</Badge></div><pre className="max-h-64 overflow-auto border-t border-slate-800 bg-slate-950 p-4 text-[11px] leading-5 text-cyan-200">{yamlPreview(op)}</pre></div>)}<div className="space-y-1.5"><Label>变更原因</Label><Textarea value={reason} onChange={(e) => setReason(e.target.value)} placeholder="说明为什么需要这组变更，以及预期影响。" /></div></div></ScrollArea><DialogFooter className="border-t px-6 py-4"><Button variant="outline" onClick={() => onOpenChange(false)}>返回修改</Button><Button disabled={!reason.trim()} onClick={() => { onSubmit(reason.trim()); setReason(""); }}><Send />提交审核</Button></DialogFooter></DialogContent></Dialog>;
}

function ReviewCenter({ changeSets, busy, onReview, onApply, onPublish }: {
  changeSets: ChangeSet[];
  busy: boolean;
  onReview: (id: string, decision: "approved" | "rejected", note: string) => Promise<boolean>;
  onApply: (id: string) => Promise<string | null>;
  onPublish: (id: string, sourceRevision: string) => Promise<boolean>;
}) {
  const [selectedId, setSelectedId] = useState(changeSets[0]?.id ?? ""); const selected = changeSets.find((item) => item.id === selectedId) ?? changeSets[0];
  const [sourceRevision, setSourceRevision] = useState("");
  if (!selected) return <div className="flex h-full items-center justify-center bg-[#eef2f7] p-8"><div className="max-w-md rounded-3xl border border-dashed border-slate-300 bg-white p-10 text-center"><GitPullRequestArrow className="mx-auto size-8 text-slate-300" /><h2 className="mt-4 font-semibold text-slate-900">暂无 ChangeSet</h2><p className="mt-2 text-sm leading-6 text-slate-500">在结构白板中暂存变更并提交后，审核记录会由 KnowledgeOS API 返回到这里。</p></div></div>;
  return <div className="grid h-full min-h-0 grid-cols-[340px_minmax(0,1fr)] bg-[#eef2f7] max-md:grid-cols-1">
    <aside className="min-h-0 border-r bg-white max-md:max-h-[230px]"><div className="border-b px-5 py-4"><h2 className="text-sm font-semibold">ChangeSet 审核</h2><p className="mt-1 text-xs text-slate-500">批准、真源应用、编译和发布登记分步完成</p></div><ScrollArea className="h-[calc(100%-70px)]"><div className="space-y-2 p-3">{changeSets.map((item) => <button key={item.id} onClick={() => { setSelectedId(item.id); setSourceRevision(item.sourceRevision ?? ""); }} className={cn("w-full rounded-2xl border p-4 text-left", selected.id === item.id ? "border-slate-950 bg-slate-950 text-white shadow-lg" : "bg-white")}><div className="flex justify-between"><span className="font-mono text-[11px] text-cyan-400">{item.id}</span><span className={cn("rounded-full border px-2 py-0.5 text-[10px]", selected.id === item.id ? "border-white/20 bg-white/10" : statusClass(item.status))}>{statusLabel(item.status)}</span></div><p className="mt-3 text-sm font-semibold">{item.title}</p><p className="mt-2 text-xs opacity-50">{item.actor} · {item.createdAt}</p></button>)}</div></ScrollArea></aside>
    <main className="min-h-0 overflow-auto p-5 sm:p-8"><div className="mx-auto max-w-5xl space-y-6"><div className="rounded-3xl border bg-white p-6 shadow-sm"><div className="flex flex-col gap-4 sm:flex-row sm:justify-between"><div><div className="flex gap-2"><Badge variant="outline" className={statusClass(selected.status)}>{statusLabel(selected.status)}</Badge><Badge variant="outline">{riskLabel(selected.risk)}风险</Badge></div><h1 className="mt-4 text-2xl font-semibold tracking-tight">{selected.title}</h1><p className="mt-2 max-w-2xl text-sm leading-6 text-slate-600">{selected.reason}</p><p className="mt-4 text-xs text-slate-400">{selected.actor} · {selected.createdAt}</p><p className="mt-1 break-all font-mono text-[11px] text-slate-400">{selected.targetSource}</p></div><div className="flex max-w-sm flex-wrap items-end gap-2">{(selected.status === "review_required" || selected.status === "proposed") && <><Button disabled={busy} variant="outline" className="text-rose-600" onClick={async () => { if (await onReview(selected.id, "rejected", "缺少迁移说明，请补充后重新提交。")) toast.error("已驳回 ChangeSet"); }}><X />驳回</Button><Button disabled={busy} onClick={async () => { if (await onReview(selected.id, "approved", "治理审核通过，等待真源应用与编译。")) toast.success("审核已通过"); }}><Check />批准</Button></>}{selected.status === "approved" && <>{selected.targetSource.split(",").map((path) => path.trim()).every((path) => /^control\/(models|units|currencies|constraints\/business|rules\/business)\/[^,]+\.yaml$/.test(path)) && <Button disabled={busy || Boolean(sourceRevision)} variant="outline" onClick={async () => { const revision = await onApply(selected.id); if (revision) { setSourceRevision(revision); toast.success("已写入 Git 真源，待验证发布"); } }}>应用到真源</Button>}<div className="min-w-[220px] flex-1 space-y-1"><Label className="text-xs text-slate-500">待验证 source revision</Label><Input value={sourceRevision} onChange={(event) => setSourceRevision(event.target.value)} placeholder="Git commit 或 sha256:…" /></div><Button disabled={busy || !sourceRevision.trim()} className="bg-emerald-600 hover:bg-emerald-700" onClick={async () => { if (await onPublish(selected.id, sourceRevision.trim())) toast.success("真源已验证并发布"); }}><GitPullRequestArrow />验证发布</Button></>}</div></div></div>
      {selected.reviewNote && <Alert className={selected.status === "published" ? "border-emerald-200 bg-emerald-50" : "border-amber-200 bg-amber-50"}>{selected.status === "published" ? <CheckCircle2 className="size-4" /> : <AlertTriangle className="size-4" />}<AlertTitle>{selected.status === "published" ? "发布记录" : "审核意见"}</AlertTitle><AlertDescription>{selected.reviewNote}</AlertDescription></Alert>}
      <div className="grid gap-6 xl:grid-cols-[minmax(0,1fr)_300px]"><section className="rounded-3xl border bg-white shadow-sm"><div className="flex justify-between border-b px-5 py-4"><h2 className="flex items-center gap-2 text-sm font-semibold"><GitCompareArrows className="size-4" />字段差异</h2><span className="text-xs text-slate-400">{selected.operations.length} 项操作</span></div><div className="space-y-4 p-5">{selected.operations.length ? selected.operations.map((op) => <div key={op.id} className="overflow-hidden rounded-2xl border"><div className="flex justify-between px-4 py-3"><div><p className="text-sm font-semibold">{op.targetId}</p><p className="text-xs text-slate-400">{op.after?.sourcePath ?? op.before?.sourcePath}</p></div><Badge variant="outline">{op.type}</Badge></div><pre className="overflow-auto border-t bg-slate-950 p-4 text-[11px] leading-5 text-cyan-200">{yamlPreview(op)}</pre></div>) : <div className="rounded-2xl border border-dashed p-10 text-center text-sm text-slate-400"><Code2 className="mx-auto mb-3 size-7" />此记录未保留字段级样例。</div>}</div></section><aside className="space-y-4"><div className="rounded-3xl border bg-white p-5"><p className="inspector-label">验证摘要</p><div className="mt-4 space-y-3">{["ID 与文件路径唯一", "策略引用存在", "YAML 结构可编译", "影响范围可接受"].map((label, index) => <div key={label} className="flex items-center gap-2.5 text-sm">{index === 3 && selected.risk === "high" ? <AlertTriangle className="size-4 text-amber-500" /> : <CheckCircle2 className="size-4 text-emerald-500" />}<span>{label}</span></div>)}</div></div></aside></div>
    </div></main>
  </div>;
}

function OntologyStudio() {
  const [definitions, setDefinitions] = useState<OntologyDefinition[]>([]); const [operations, setOperations] = useState<ChangeOperation[]>([]); const [changeSets, setChangeSets] = useState<ChangeSet[]>([]);
  const [baselineDefinitions, setBaselineDefinitions] = useState<OntologyDefinition[]>([]);
  const [session, setSession] = useState<UserSession | null>(null);
  const [loginOpen, setLoginOpen] = useState(false);
  const [tokenInput, setTokenInput] = useState("");
  const [draftConflict, setDraftConflict] = useState("");
  const operationsRef = useRef(operations);
  const syncSequence = useRef(0);
  const proposalKey = useRef<{ signature: string; key: string } | null>(null);
  useEffect(() => { operationsRef.current = operations; }, [operations]);
  const [connection, setConnection] = useState<ApiConnectionState>("connecting"); const [metadata, setMetadata] = useState<StudioMetadata | null>(null); const [syncing, setSyncing] = useState(true); const [mutationBusy, setMutationBusy] = useState(false);
  const [selectedId, setSelectedId] = useState<string | null>(null); const [search, setSearch] = useState(""); const [filterKind, setFilterKind] = useState<"all" | DefinitionKind>("all"); const [lifecycle, setLifecycle] = useState<"all" | Lifecycle>("all"); const [storage, setStorage] = useState("all");
  const [selectedIds, setSelectedIds] = useState<string[]>([]);
  const selectedIdsRef = useRef(selectedIds);
  const chooseNodes = (ids: string[]) => { selectedIdsRef.current = ids; setSelectedIds(ids); setSelectedId(ids.at(-1) ?? null); };
  const [expert, setExpert] = useState(false); const [view, setView] = useState("control"); const [createOpen, setCreateOpen] = useState(false); const [submitOpen, setSubmitOpen] = useState(false); const [flow, setFlow] = useState<ReactFlowInstance<StudioNode, StudioEdge> | null>(null);
  const [leftCollapsed, setLeftCollapsed] = useState(false); const [compact, setCompact] = useState(false); const [focusMode, setFocusMode] = useState(false); const [marqueeMode, setMarqueeMode] = useState(false); const [nodePositions, setNodePositions] = useState<NodePositions>({}); const [commandOpen, setCommandOpen] = useState(false); const [editSignal, setEditSignal] = useState(0); const [relatedRequest, setRelatedRequest] = useState<{ id: string } | null>(null); const [bindingConceptId, setBindingConceptId] = useState<string | null>(null); const [endpointRelationId, setEndpointRelationId] = useState<string | null>(null);
  const [layoutRevision, setLayoutRevision] = useState(0);
  const appliedLayoutRevision = useRef(layoutRevision);
  const searchInputRef = useRef<HTMLInputElement>(null);
  const refreshFromBackend = async (announce = true, discardDraft = false) => {
    const sequence = ++syncSequence.current;
    setSyncing(true);
    try {
      const identity = await loadSession();
      const snapshot = await loadStudioSnapshot();
      if (sequence !== syncSequence.current) return false;
      setSession(identity);
      let rebased;
      try { rebased = rebaseDraft(snapshot.definitions, discardDraft ? [] : operationsRef.current); }
      catch (error) {
        setDraftConflict(error instanceof Error ? error.message : "草稿冲突");
        setConnection("connected");
        setChangeSets(snapshot.changeSets);
        return false;
      }
      setDraftConflict("");
      setDefinitions(rebased.definitions);
      setOperations(rebased.operations);
      setBaselineDefinitions(snapshot.definitions);
      setChangeSets(snapshot.changeSets);
      setMetadata(snapshot.metadata);
      setConnection("connected");
      const available = new Set(snapshot.definitions.map((item) => item.id));
      const retained = selectedIdsRef.current.filter((id) => available.has(id));
      chooseNodes(retained);
      if (announce) toast.success("已从 KnowledgeOS API 同步控制面");
      return true;
    } catch (error) {
      if (sequence !== syncSequence.current) return false;
      setConnection(error instanceof ApiError ? error.connectionState : "offline");
      if (error instanceof ApiError && error.status === 401) setSession(null);
      if (announce) toast.error(error instanceof Error ? error.message : "无法连接 KnowledgeOS API");
      return false;
    } finally {
      if (sequence === syncSequence.current) setSyncing(false);
    }
  };
  useEffect(() => {
    const timer = window.setTimeout(() => { void refreshFromBackend(false); }, 0);
    return () => window.clearTimeout(timer);
  // The initial API handshake intentionally runs once for this mounted workspace.
  }, []);
  const selectDefinition = (id: string) => { chooseNodes([id]); setFocusMode(true); setRelatedRequest(null); };
  const clearSelection = () => { chooseNodes([]); setFocusMode(false); setRelatedRequest(null); };
  const quickEditDefinition = (id: string) => { chooseNodes([id]); setFocusMode(true); setEditSignal((value) => value + 1); };
  const showRelatedDefinitions = (id: string) => { chooseNodes([id]); setFocusMode(true); setRelatedRequest({ id }); };
  const manageBindings = (id: string) => { chooseNodes([id]); setFocusMode(true); setBindingConceptId(id); };
  const manageEndpoints = (id: string) => { chooseNodes([id]); setFocusMode(true); setEndpointRelationId(id); };
  const visibleDefinitions = useMemo(() => whiteboardDefinitions(definitions), [definitions]);
  const selected = visibleDefinitions.find((item) => item.id === selectedId) ?? null;
  const bindingConcept = definitions.find((item) => item.id === bindingConceptId && item.kind === "concept") ?? null;
  const endpointRelation = definitions.find((item) => item.id === endpointRelationId && item.kind === "relation") ?? null;
  const dependencyEdges = useMemo(() => definitionEdges(definitions, true), [definitions]);
  const [allNodes, setAllNodes] = useState<StudioNode[]>([]);
  useEffect(() => {
    const projectedNodes = graphNodes(visibleDefinitions, dependencyEdges, search, filterKind, lifecycle, storage, selectedId, selectedIds, nodePositions, showRelatedDefinitions, manageBindings, manageEndpoints);
    const resetPositions = appliedLayoutRevision.current !== layoutRevision;
    setAllNodes((current) => reconcileCanvasNodes(current, projectedNodes, resetPositions));
    appliedLayoutRevision.current = layoutRevision;
  // These handlers are recreated during render but only carry the current definition actions.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [visibleDefinitions, dependencyEdges, search, filterKind, lifecycle, storage, selectedId, selectedIds, nodePositions, layoutRevision]);
  const relatedIds = useMemo(() => {
    const ids = new Set<string>();
    if (!selectedId) return ids;
    ids.add(selectedId);
    dependencyEdges.forEach((edge) => {
      if (edge.source === selectedId) ids.add(edge.target);
      if (edge.target === selectedId) ids.add(edge.source);
    });
    return ids;
  }, [dependencyEdges, selectedId]);
  const relatedDefinitions = useMemo(() => visibleDefinitions.filter((item) => item.id !== selectedId && relatedIds.has(item.id)), [visibleDefinitions, relatedIds, selectedId]);
  const nodes = useMemo(() => focusMode ? allNodes.filter((node) => relatedIds.has(node.id)) : allNodes, [allNodes, focusMode, relatedIds]);
  const edges = useMemo(() => graphEdges(nodes, selectedId, dependencyEdges), [nodes, selectedId, dependencyEdges]);
  const errors = useMemo(() => definitions.flatMap((item) => {
    const itemErrors: string[] = [];
    if (!item.label.trim()) itemErrors.push(`${item.id} 缺少显示名称`);
    if (item.lifecycle === "deprecated" && item.refs > 0 && !item.config.equivalent_to && !item.config.migration_notes) itemErrors.push(`${item.id} 被引用，弃用时需要替代项或迁移说明`);
    if (item.kind === "concept") {
      const ids = (item.bindings ?? []).map((binding) => binding.predicateId);
      if (new Set(ids).size !== ids.length) itemErrors.push(`${item.id} 存在重复的判断类型绑定`);
      (item.bindings ?? []).forEach((binding) => {
        const predicate = definitions.find((definition) => definition.id === binding.predicateId);
        if (!predicate || predicate.kind !== "predicate") itemErrors.push(`${item.id} 引用了未注册的判断类型 ${binding.predicateId}`);
      });
    }
    if (item.kind === "predicate") {
      if (!authorityOptions.includes(String(item.config.authority))) itemErrors.push(`${item.id} 引用了未知权威策略 ${String(item.config.authority)}`);
      if (!freshnessOptions.includes(String(item.config.freshness))) itemErrors.push(`${item.id} 引用了未知新鲜度策略 ${String(item.config.freshness)}`);
      if (!provenanceOptions.includes(String(item.config.provenance_tier))) itemErrors.push(`${item.id} 引用了未知溯源等级 ${String(item.config.provenance_tier)}`);
    }
    if (item.kind === "relation") {
      const keys = (item.endpoints ?? []).map((endpoint) => `${endpoint.sourceConceptId}->${endpoint.targetConceptId}`);
      if (new Set(keys).size !== keys.length) itemErrors.push(`${item.id} 存在重复的有向概念连接`);
      (item.endpoints ?? []).forEach((endpoint) => {
        const source = definitions.find((definition) => definition.id === endpoint.sourceConceptId);
        const target = definitions.find((definition) => definition.id === endpoint.targetConceptId);
        if (!source || source.kind !== "concept") itemErrors.push(`${item.id} 的源概念 ${endpoint.sourceConceptId} 不存在`);
        if (!target || target.kind !== "concept") itemErrors.push(`${item.id} 的目标概念 ${endpoint.targetConceptId} 不存在`);
      });
      const relationMode = item.relationMode ?? "simple";
      if (relationMode !== "simple") {
        if (!item.reification) itemErrors.push(`${item.id} 缺少实体化关系模板`);
        else {
          const nodeType = definitions.find((definition) => definition.id === item.reification?.nodeType);
          if (!nodeType || nodeType.kind !== "concept") itemErrors.push(`${item.id} 的实体化节点类型 ${item.reification.nodeType} 不存在`);
          if (relationMode === "reified" && item.reification.identity !== "required") itemErrors.push(`${item.id} 设置为必须实体化时必须拥有稳定 ID`);
          const propertyIds = item.reification.properties.map((binding) => binding.predicateId);
          if (new Set(propertyIds).size !== propertyIds.length) itemErrors.push(`${item.id} 的实体化模板存在重复判断类型`);
          propertyIds.forEach((predicateId) => {
            const predicate = definitions.find((definition) => definition.id === predicateId);
            if (!predicate || predicate.kind !== "predicate") itemErrors.push(`${item.id} 的实体化模板引用了未注册判断类型 ${predicateId}`);
          });
        }
      }
    }
    if (item.kind === "domain") {
      const retrieval = asRecord(item.config.retrieval);
      asStringArray(retrieval.relation_types).forEach((id) => {
        if (!definitions.some((definition) => definition.kind === "relation" && definition.id === id)) itemErrors.push(`${item.id} 引用了未知关系 ${id}`);
      });
      asStringArray(retrieval.predicate_priority).forEach((id) => {
        if (!definitions.some((definition) => definition.kind === "predicate" && definition.id === id)) itemErrors.push(`${item.id} 引用了未知判断类型 ${id}`);
      });
      asStringArray(item.config.required_models).forEach((id) => {
        if (!definitions.some((definition) => definition.kind === "model" && definition.config.model_id === id)) itemErrors.push(`${item.id} 引用了未知模型 ${id}`);
      });
    }
    if (item.kind === "connector") {
      const node = asRecord(item.config.node);
      if (!definitions.some((definition) => definition.kind === "concept" && definition.id === node.type)) itemErrors.push(`${item.id} 映射到未知概念 ${String(node.type)}`);
      const mapped = [...Object.keys(asRecord(item.config.attrs)), ...Object.keys(asRecord(item.config.assertions))];
      mapped.forEach((id) => { if (!definitions.some((definition) => definition.kind === "predicate" && definition.id === id)) itemErrors.push(`${item.id} 映射到未知判断类型 ${id}`); });
    }
    if (item.kind === "model") {
      if (!item.config.model_id || !/^\d+\.\d+\.\d+$/.test(String(item.config.version ?? "")) || !item.config.output_unit || !Array.isArray(item.config.inputs) || !item.config.inputs.length || !asRecord(item.config.formula).op) itemErrors.push(`${item.id} 缺少有效的版本、输入、公式或输出单位`);
    }
    if (item.kind === "business_constraint" || item.kind === "business_rule") {
      const scope = asRecord(item.config.scope);
      const inputRows = Array.isArray(item.config.inputs) ? item.config.inputs.map(asRecord) : [];
      const checkRows = Array.isArray(item.config.checks) ? item.config.checks.map(asRecord) : [];
      const subject = definitions.find((definition) => definition.kind === "concept" && definition.id === scope.subject_concept);
      const candidate = definitions.find((definition) => definition.kind === "concept" && definition.id === scope.candidate_concept);
      if (!/^\d+\.\d+\.\d+$/.test(String(item.config.version ?? ""))) itemErrors.push(`${item.id} 需要有效语义版本`);
      if (!subject || (item.kind === "business_rule" && !candidate)) itemErrors.push(`${item.id} 缺少已注册的适用概念`);
      if (!inputRows.length || inputRows.length > 20 || !checkRows.length || checkRows.length > 20) itemErrors.push(`${item.id} 需要 1–20 个输入和条件`);
      const names = inputRows.map((input) => String(input.id ?? ""));
      if (new Set(names).size !== names.length || names.some((name) => !/^[a-z][a-z0-9_]*$/.test(name))) itemErrors.push(`${item.id} 输入变量名重复或格式无效`);
      inputRows.forEach((input) => {
        const concept = input.role === "candidate" ? candidate : subject;
        const predicate = definitions.find((definition) => definition.kind === "predicate" && definition.id === input.predicate);
        if (!predicate || !concept || (input.role !== "subject" && (item.kind !== "business_rule" || input.role !== "candidate")) || ((concept.bindings ?? []).length > 0 && !(concept.bindings ?? []).some((binding) => binding.predicateId === input.predicate))) itemErrors.push(`${item.id} 的输入 ${String(input.id)} 未绑定到适用概念的注册判断类型`);
      });
      checkRows.forEach((check) => {
        const right = asRecord(check.right);
        if (!names.includes(String(check.left)) || !["eq", "ne", "gt", "gte", "lt", "lte"].includes(String(check.operator)) || !(Object.keys(right).length === 1 && ("value" in right || ("input" in right && names.includes(String(right.input)))))) itemErrors.push(`${item.id} 存在不完整的比较条件`);
      });
      if (scope.relation_type && !definitions.some((definition) => definition.kind === "relation" && definition.id === scope.relation_type && (definition.endpoints ?? []).some((edge) => edge.sourceConceptId === scope.subject_concept && edge.targetConceptId === scope.candidate_concept))) itemErrors.push(`${item.id} 的关系类型不允许当前概念方向`);
      const baseline = baselineDefinitions.find((definition) => definition.id === item.id);
      if (baseline && operations.some((operation) => operation.targetId === item.id) && String(baseline.config.version) === String(item.config.version)) itemErrors.push(`${item.id} 修改后必须递增语义版本`);
    }
    if (item.kind === "unit") {
      if (!/^\d+\.\d+\.\d+$/.test(String(item.config.version ?? "")) || !item.config.dimension || !Number.isFinite(Number(item.config.factor_to_base)) || Number(item.config.factor_to_base) <= 0) itemErrors.push(`${item.id} 缺少有效的版本、量纲或正数换算系数`);
    }
    if (item.kind === "currency") {
      if (!/^\d+\.\d+\.\d+$/.test(String(item.config.version ?? "")) || !Number.isInteger(item.config.minor_units) || Number(item.config.minor_units) < 0 || Number(item.config.minor_units) > 6) itemErrors.push(`${item.id} 缺少有效版本或货币小数位`);
    }
    if (item.id === "schema:canonical_node" && !asStringArray(item.config.required_knowledge).includes("logic_refs")) itemErrors.push(`${item.id} 缺少 logic_refs 契约`);
    return itemErrors;
  }), [definitions, baselineDefinitions, operations]);
  const warnings = useMemo(() => Array.from(new Set(operations.flatMap((op) => {
    const itemWarnings: string[] = [];
    if (op.targetKind === "relation" && op.after?.config.domain_range === "undeclared") itemWarnings.push(`${op.targetId} 尚未声明 domain/range`);
      if ((op.before?.refs ?? 0) > 2) itemWarnings.push(`${op.targetId} 的引用范围较大`);
      if (op.targetKind === "business_constraint" || op.targetKind === "business_rule") itemWarnings.push(`${op.targetId} 需递增版本、试算回归并由独立审核人批准`);
    if (op.targetKind === "concept" && JSON.stringify(op.before?.bindings ?? []) !== JSON.stringify(op.after?.bindings ?? [])) {
      itemWarnings.push(`${op.targetId} 的概念形状属于目标契约，发布前需要编译器支持`);
      if ((op.before?.bindings?.length ?? 0) > (op.after?.bindings?.length ?? 0) && (op.before?.refs ?? 0) > 0) itemWarnings.push(`${op.targetId} 已解绑判断类型，请核对现有实体影响`);
    }
    if (op.targetKind === "relation" && JSON.stringify(op.before?.endpoints ?? []) !== JSON.stringify(op.after?.endpoints ?? [])) itemWarnings.push(`${op.targetId} 的 domain/range 属于目标契约，发布前需要校验器支持`);
    if (op.targetKind === "relation" && (op.before?.relationMode !== op.after?.relationMode || JSON.stringify(op.before?.reification ?? null) !== JSON.stringify(op.after?.reification ?? null))) itemWarnings.push(`${op.targetId} 的实体化关系模板属于目标契约，发布前需要节点编译支持`);
    if (op.targetKind === "model" || op.targetKind === "unit" || op.targetKind === "currency") itemWarnings.push(`${op.targetId} 需递增版本、试算回归并由独立审核人批准`);
    return itemWarnings;
  }))), [operations]);

  const stageUpdate = (next: OntologyDefinition, preferredType?: ChangeOperationType) => {
    setDefinitions((items) => items.map((item) => item.id === next.id ? next : item));
    setOperations((items) => stageDefinitionOperation(items, next, baselineDefinitions, preferredType));
  };
  const handleNodeChanges = (changes: NodeChange<StudioNode>[]) => {
    setAllNodes((current) => applyNodeChanges(changes, current));
    if (changes.some((change) => change.type === "select")) {
      const next = selectionAfterChanges(selectedIdsRef.current, changes);
      selectedIdsRef.current = next;
      setSelectedIds(next);
      const latest = [...changes].reverse().find((change): change is Extract<NodeChange<StudioNode>, { type: "select" }> => change.type === "select" && change.selected);
      setSelectedId(latest?.id ?? next.at(-1) ?? null);
      if (next.length === 0) setFocusMode(false);
    }
  };
  const handleNodeDragStop = () => {
    const dragged = flow?.getNodes() ?? [];
    setNodePositions((current) => {
      const next = { ...current };
      dragged.forEach((node) => { next[node.id] = node.position; });
      return next;
    });
  };
  const fitVisibleNodes = () => requestAnimationFrame(() => flow?.fitView({ nodes, padding: .24, duration: 420, maxZoom: 1 }));
  const resetLayout = () => { setNodePositions({}); setLayoutRevision((value) => value + 1); requestAnimationFrame(() => requestAnimationFrame(() => flow?.fitView({ padding: .18, duration: 420, maxZoom: .9 }))); };
  const createDefinition = (definition: OntologyDefinition) => { setDefinitions((items) => [...items, definition]); setOperations((items) => [...items, { id: `op-${Date.now()}`, type: "create", targetId: definition.id, targetKind: definition.kind, before: null, after: definition }]); selectDefinition(definition.id); toast.success(`${definition.label} 已加入当前变更`); };
  const createAndBindPredicate = (predicate: OntologyDefinition, conceptId: string) => {
    const concept = definitions.find((item) => item.id === conceptId && item.kind === "concept");
    if (!concept) return;
    const nextConcept = { ...concept, bindings: [...(concept.bindings ?? []), { predicateId: predicate.id, required: false, cardinality: "inherit" as const, group: predicate.config.storage_mode === "attr" ? "profile" as const : "governance" as const }] };
    setDefinitions((items) => [...items.map((item) => item.id === conceptId ? nextConcept : item), predicate]);
    setOperations((items) => stageDefinitionOperation(stageDefinitionOperation(items, predicate, baselineDefinitions, "create"), nextConcept, baselineDefinitions, "update"));
    chooseNodes([conceptId]); setFocusMode(true); toast.success(`${predicate.label} 已创建并绑定到 ${concept.label}`);
  };
  const createAndBindReificationPredicate = (predicate: OntologyDefinition, relationId: string) => {
    const relation = definitions.find((item) => item.id === relationId && item.kind === "relation");
    if (!relation) return;
    const currentReification = relation.reification ?? { nodeType: "relation", identity: "required" as const, properties: [] };
    const nextRelation = { ...relation, relationMode: relation.relationMode === "simple" || !relation.relationMode ? "reifiable" as const : relation.relationMode, reification: { ...currentReification, properties: [...currentReification.properties, { predicateId: predicate.id, required: false, cardinality: "inherit" as const, group: predicate.config.storage_mode === "attr" ? "profile" as const : "governance" as const }] }, config: { ...relation.config, relation_mode: relation.relationMode === "simple" || !relation.relationMode ? "reifiable" : relation.relationMode } };
    setDefinitions((items) => [...items.map((item) => item.id === relationId ? nextRelation : item), predicate]);
    setOperations((items) => stageDefinitionOperation(stageDefinitionOperation(items, predicate, baselineDefinitions, "create"), nextRelation, baselineDefinitions, "update"));
    chooseNodes([relationId]); setFocusMode(true); toast.success(`${predicate.label} 已创建并绑定到 ${relation.label} 的实体化模板`);
  };
  const deleteSelected = () => { if (!selected) return; setDefinitions((items) => items.filter((item) => item.id !== selected.id)); setOperations((items) => { const existing = items.find((item) => item.targetId === selected.id); if (existing?.type === "create") return items.filter((item) => item.id !== existing.id); const op: ChangeOperation = { id: existing?.id ?? `op-${Date.now()}`, type: "delete", targetId: selected.id, targetKind: selected.kind, before: baselineDefinitions.find((item) => item.id === selected.id) ?? selected, after: null }; return existing ? items.map((item) => item.id === existing.id ? op : item) : [...items, op]; }); chooseNodes([]); toast.success("草稿已删除"); };
  const deprecateSelected = () => {
    if (!selected) return;
    const versioned = ["model", "unit", "currency", "business_constraint", "business_rule"].includes(selected.kind);
    const parts = String(selected.config.version ?? "1.0.0").split(".").map(Number);
    const config = versioned && parts.length === 3 && parts.every(Number.isInteger)
      ? { ...selected.config, version: `${parts[0]}.${parts[1]}.${parts[2] + 1}` } : selected.config;
    stageUpdate({ ...selected, lifecycle: "deprecated", config }, "deprecate");
    toast.message(versioned ? "已标记弃用并递增版本；请提交审核" : "已标记弃用，请补充替代项或迁移说明");
  };
  const submit = async (reason: string) => {
    const title = operations.length === 1 ? `${operations[0].targetId} 本体变更` : `${operations.length} 项本体定义变更`;
    const targetSource = Array.from(new Set(operations.map((op) => op.after?.sourcePath ?? op.before?.sourcePath).filter((path): path is string => Boolean(path)))).join(", ");
    const risk = operations.some((op) => ["schema", "model", "business_constraint", "business_rule"].includes(op.targetKind)) ? "high" as const : warnings.length || operations.some((op) => (op.before?.refs ?? 0) > 2) ? "normal" as const : "low" as const;
    if (connection === "connected") {
      if (draftConflict) { toast.error(draftConflict); return; }
      if (!session?.permissions.includes("propose")) { toast.error("当前身份没有提案权限"); return; }
      setMutationBusy(true);
      try {
        const signature = JSON.stringify({ operations, reason, baseline: metadata?.registryFingerprint });
        if (proposalKey.current?.signature !== signature) proposalKey.current = { signature, key: crypto.randomUUID() };
        await proposeChangeSet({ title, reason, risk, targetSource, operations, baseRevision: metadata?.registryFingerprint, idempotencyKey: proposalKey.current.key });
        setOperations([]); setSubmitOpen(false); setView("review");
        await refreshFromBackend(false, true);
        toast.success("ChangeSet 已写入 KnowledgeOS 审核队列");
      } catch (error) {
        toast.error(error instanceof Error ? error.message : "ChangeSet 提交失败");
      } finally {
        setMutationBusy(false);
      }
      return;
    }
    if (connection !== "demo") { toast.error("连接恢复后才能提交，草稿已保留"); return; }
    const set: ChangeSet = { id: `LOCAL-${Date.now()}`, title, reason, actor: "本地演示", targetSource, createdAt: "刚刚", risk, status: "review_required", operations };
    setChangeSets((items) => [set, ...items]); setOperations([]); setSubmitOpen(false); setView("review"); toast.warning("当前未连接后端；ChangeSet 仅保存在本页会话中");
  };
  const handleReview = async (id: string, decision: "approved" | "rejected", note: string) => {
    if (connection === "demo") {
      setChangeSets((items) => items.map((item) => item.id === id ? { ...item, status: decision, reviewNote: note, reviewedBy: "本地演示", reviewedAt: "刚刚" } : item));
      toast.warning("审核结果仅保存在本页会话中");
      return true;
    }
    if (connection !== "connected" || !session?.permissions.includes("review")) { toast.error("需要连接后端并具有审核权限"); return false; }
    setMutationBusy(true);
    try { await reviewChangeSet(id, decision, note); await refreshFromBackend(false); return true; }
    catch (error) { toast.error(error instanceof Error ? error.message : "审核操作失败"); return false; }
    finally { setMutationBusy(false); }
  };
  const handlePublish = async (id: string, sourceRevision: string) => {
    if (connection === "demo") {
      setChangeSets((items) => items.map((item) => item.id === id ? { ...item, status: "published", sourceRevision, reviewNote: `真源已更新并编译，发布修订：${sourceRevision}` } : item));
      toast.warning("发布登记仅保存在本页会话中");
      return true;
    }
    if (connection !== "connected" || !session?.permissions.includes("publish")) { toast.error("需要连接后端并具有发布权限"); return false; }
    setMutationBusy(true);
    try { await publishChangeSet(id, sourceRevision); await refreshFromBackend(false); return true; }
    catch (error) { toast.error(error instanceof Error ? error.message : "发布登记失败"); return false; }
    finally { setMutationBusy(false); }
  };
  const handleApply = async (id: string): Promise<string | null> => {
    if (connection !== "connected" || !session?.permissions.includes("publish")) { toast.error("需要连接后端并具有发布权限"); return null; }
    setMutationBusy(true);
    try { const revision = await applyChangeSet(id); await refreshFromBackend(false); return revision; }
    catch (error) { toast.error(error instanceof Error ? error.message : "应用真源失败"); return null; }
    finally { setMutationBusy(false); }
  };
  const reset = () => {
    setOperations([]); setDraftConflict(""); setSearch(""); setFilterKind("all"); setLifecycle("all");
    if (connection === "demo") { setDefinitions(initialDefinitions); setBaselineDefinitions(initialDefinitions); setChangeSets(initialChangeSets); }
    else void refreshFromBackend(true, true);
  };
  const enterDemo = () => {
    ++syncSequence.current; setSessionToken(""); setSession(null); setSyncing(false);
    setConnection("demo"); setMetadata(null); setOperations([]); setDraftConflict("");
    setDefinitions(initialDefinitions); setBaselineDefinitions(initialDefinitions); setChangeSets(initialChangeSets);
  };
  const login = async () => {
    setSessionToken(tokenInput); setTokenInput(""); setLoginOpen(false);
    await refreshFromBackend(true);
  };
  const connectionLabel = { connecting: "正在连接", connected: "已连接", demo: "演示模式", unauthorized: "请登录", forbidden: "权限不足", offline: "连接中断", unconfigured: "后端未配置" }[connection];

  useEffect(() => {
    if (focusMode) fitVisibleNodes();
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [focusMode, selectedId]);

  useEffect(() => {
    const frame = requestAnimationFrame(() => {
      if (window.innerWidth < 900) setLeftCollapsed(true);
    });
    return () => cancelAnimationFrame(frame);
  }, []);

  useEffect(() => {
    const media = window.matchMedia("(max-width: 1179px)");
    const update = () => setCompact(media.matches);
    update();
    media.addEventListener("change", update);
    return () => media.removeEventListener("change", update);
  }, []);

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      const target = event.target as HTMLElement | null;
      const typing = target?.matches("input, textarea, [contenteditable='true']");
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") { event.preventDefault(); setCommandOpen(true); return; }
      if (typing) return;
      if (event.key === "/") { event.preventDefault(); searchInputRef.current?.focus(); }
      else if (event.key.toLowerCase() === "n") { event.preventDefault(); setCreateOpen(true); }
      else if (event.key.toLowerCase() === "b") { event.preventDefault(); setMarqueeMode((value) => !value); }
      else if (event.key.toLowerCase() === "v") { event.preventDefault(); setMarqueeMode(false); }
      else if (event.key.toLowerCase() === "f" && selectedId) { event.preventDefault(); setFocusMode((value) => !value); }
      else if (event.key === "[") { event.preventDefault(); setLeftCollapsed((value) => !value); }
      else if (event.key === "]" && selectedId) { event.preventDefault(); chooseNodes([]); setFocusMode(false); setRelatedRequest(null); }
      else if (event.key === "Escape" && focusMode) { setFocusMode(false); }
      else if (event.shiftKey && event.key === "1") { event.preventDefault(); flow?.fitView({ padding: .18, duration: 320, maxZoom: .9 }); }
      else if (event.shiftKey && event.key === "2" && selectedId) { event.preventDefault(); const node = allNodes.find((item) => item.id === selectedId); if (node) flow?.fitView({ nodes: [node], padding: .7, duration: 320, maxZoom: 1.15 }); }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [allNodes, flow, focusMode, selectedId]);

  useEffect(() => {
    const modelContext = (document as Document & { modelContext?: ModelContextRegistration }).modelContext;
    if (!modelContext) return;
    const controller = new AbortController();
    void Promise.all([
      modelContext.registerTool({
        name: "search_ontology",
        description: "按 ID、中文名称或语义说明搜索 KnowledgeOS 本体定义。",
        inputSchema: {
          type: "object",
          properties: { query: { type: "string", description: "搜索词" } },
          required: ["query"],
          additionalProperties: false,
        },
        annotations: { readOnlyHint: true },
        execute: ({ query }) => {
          const q = String(query ?? "").toLowerCase();
          const matches = definitions.filter((item) => `${item.id} ${item.label} ${item.description}`.toLowerCase().includes(q)).slice(0, 20);
          return { content: [{ type: "text", text: JSON.stringify(matches.map(({ id, kind, label, lifecycle, refs }) => ({ id, kind, label, lifecycle, refs }))) }] };
        },
      }, { signal: controller.signal }),
      modelContext.registerTool({
        name: "create_ontology_draft",
        description: "在原型内创建概念类型、关系类型或判断类型草稿，并加入当前 ChangeSet。",
        inputSchema: {
          type: "object",
          properties: {
            kind: { type: "string", enum: ["concept", "relation", "predicate"] },
            id: { type: "string" },
            label: { type: "string" },
            description: { type: "string" },
          },
          required: ["kind", "id", "label", "description"],
          additionalProperties: false,
        },
        annotations: { readOnlyHint: false, destructiveHint: false },
        execute: ({ kind, id, label, description }) => {
          const normalizedKind = String(kind) as DefinitionKind;
          const normalizedId = String(id).trim();
          if (!editableKinds.includes(normalizedKind) || !/^[a-z][a-z0-9_:-]*$/.test(normalizedId)) throw new Error("定义类型或 ID 格式无效");
          if (definitions.some((item) => item.id === normalizedId)) throw new Error(`ID 已存在：${normalizedId}`);
          createDefinition({
            id: normalizedId,
            kind: normalizedKind,
            label: String(label).trim(),
            description: String(description).trim(),
            lifecycle: "draft",
            sourcePath: normalizedKind === "predicate" ? `control/predicates/${normalizedId}.yaml` : "control/ontology/core.yaml",
            refs: 0,
            files: [],
            endpoints: normalizedKind === "relation" ? [] : undefined,
            relationMode: normalizedKind === "relation" ? "simple" : undefined,
            config: normalizedKind === "predicate" ? { value_type: "string", cardinality: "one", storage_mode: "attr", authority: "git_authored", freshness: "stable", provenance_tier: "C", write: "reviewed", equivalent_to: null } : normalizedKind === "relation" ? { domain_range: "undeclared", relation_mode: "simple", schema_support: "target-contract" } : { schema_support: "target-contract" },
          });
          return { content: [{ type: "text", text: `已创建草稿 ${normalizedId}，尚未写入真实 KnowledgeOS。` }] };
        },
      }, { signal: controller.signal }),
    ]).catch(() => undefined);
    return () => controller.abort();
  // The tool registration is intentionally refreshed only when its captured catalog changes.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [definitions]);

  const catalog = <Catalog definitions={visibleDefinitions} selectedId={selectedId} onSelect={selectDefinition} onQuickEdit={quickEditDefinition} filterKind={filterKind} setFilterKind={setFilterKind} onCreate={() => setCreateOpen(true)} />;
  const inspector = <Inspector key={selected?.id ?? "empty"} definition={selected} definitions={definitions} relatedDefinitions={relatedDefinitions} expert={expert} editSignal={editSignal} relatedRequest={relatedRequest} onUpdate={stageUpdate} onDeprecate={deprecateSelected} onDelete={deleteSelected} onSelectRelated={selectDefinition} onCollapse={clearSelection} onManageBindings={manageBindings} onManageEndpoints={manageEndpoints} />;

  return <TooltipProvider><Tabs value={view} onValueChange={setView} className="h-dvh min-h-[620px] gap-0 overflow-hidden bg-[#eeeeea] text-[#292925]">
    <header className="flex h-12 shrink-0 items-center gap-2 border-b border-[#d5d5d0] bg-[#f4f4f1] px-2">
      <div className="flex min-w-0 items-center gap-2 px-1 sm:w-[258px]"><div className="flex size-7 shrink-0 items-center justify-center rounded-lg bg-[#6557d5] text-white"><Layers3 className="size-4" /></div><div className="min-w-0"><p className="truncate text-sm font-semibold">KnowledgeOS</p></div></div>
      <TabsList className="hidden h-9 gap-1 bg-transparent p-0 md:flex">
        <TabsTrigger value="control" className="h-8 rounded-lg border border-transparent px-3 text-xs text-[#777770] data-[state=active]:border-[#d8d8d3] data-[state=active]:bg-white data-[state=active]:text-[#2d2d29] data-[state=active]:shadow-sm"><ShieldCheck />控制总览</TabsTrigger>
        <TabsTrigger value="studio" className="h-8 rounded-lg border border-transparent px-3 text-xs text-[#777770] data-[state=active]:border-[#d8d8d3] data-[state=active]:bg-white data-[state=active]:text-[#2d2d29] data-[state=active]:shadow-sm"><Network />结构白板</TabsTrigger>
        <TabsTrigger value="review" className="h-8 rounded-lg border border-transparent px-3 text-xs text-[#777770] data-[state=active]:border-[#d8d8d3] data-[state=active]:bg-white data-[state=active]:text-[#2d2d29] data-[state=active]:shadow-sm"><GitPullRequestArrow />治理审核<span className="ml-1 rounded-full bg-[#e7e3fb] px-1.5 py-0.5 text-[10px] text-[#5546c4]">{changeSets.filter((item) => item.status === "review_required").length}</span></TabsTrigger>
      </TabsList>
      <Link href="/templates" className="hidden h-8 items-center gap-1.5 rounded-lg border border-indigo-200 bg-indigo-50 px-3 text-xs font-medium text-indigo-700 hover:bg-indigo-100 lg:inline-flex"><Layers3 className="size-3.5" />专家模板工作台</Link>
      <div className="ml-auto flex items-center gap-1.5"><button onClick={() => void refreshFromBackend(true)} disabled={syncing} className={cn("hidden h-8 items-center gap-2 rounded-lg border px-2.5 text-xs font-medium sm:flex", connection === "connected" ? "border-emerald-200 bg-emerald-50 text-emerald-700" : "border-amber-200 bg-amber-50 text-amber-700")} title={connection === "connected" ? "重新同步 KnowledgeOS API" : "点击重试连接，已有草稿会保留"}><span className={cn("size-2 rounded-full", connection === "connected" ? "bg-emerald-500" : "bg-amber-500")} />{connection === "connected" ? `API 已连接 · ${metadata?.contractVersion ?? ""}` : connectionLabel}<RefreshCw className={cn("size-3", syncing && "animate-spin")} /></button><Button variant="ghost" size="sm" className="hidden h-8 gap-2 rounded-lg text-xs text-[#6e6e67] lg:flex" onClick={() => setCommandOpen(true)}><Search className="size-3.5" />快速命令<kbd className="rounded border border-[#d3d3ce] bg-white px-1.5 py-0.5 font-mono text-[10px]">⌘K</kbd></Button><div className="hidden items-center gap-1.5 px-2 text-xs text-[#777770] xl:flex">{errors.length ? <XCircle className="size-3.5 text-rose-500" /> : <CheckCircle2 className="size-3.5 text-emerald-600" />}<span>{errors.length ? `${errors.length} 个错误` : "结构有效"}</span></div><div className="flex h-8 items-center gap-2 rounded-lg border border-[#d8d8d3] bg-white px-2"><Code2 className="size-3.5 text-[#777770]" /><Label className="hidden text-xs text-[#66665f] sm:block">专家</Label><Switch checked={expert} onCheckedChange={setExpert} className="scale-90 data-[state=checked]:bg-[#6557d5]" /></div><Button variant="ghost" size="icon-sm" onClick={reset} aria-label="重新载入控制面" className="text-[#777770]"><RotateCcw /></Button><Button size="sm" variant="outline" onClick={() => setLoginOpen(true)}>{session?.principal ?? "登录"}</Button><Button size="sm" variant="ghost" disabled={syncing || operations.length > 0} onClick={enterDemo}>进入演示</Button></div>
    </header>
    {draftConflict && <Alert variant="destructive"><AlertTitle>编辑冲突</AlertTitle><AlertDescription>{draftConflict}<Button variant="outline" onClick={reset}>放弃草稿并重新载入</Button></AlertDescription></Alert>}
    <Dialog open={loginOpen} onOpenChange={setLoginOpen}><DialogContent><DialogHeader><DialogTitle>登录 KnowledgeOS</DialogTitle><DialogDescription>输入管理员分配的个人访问凭证。凭证仅保留在当前页面内存中，刷新页面后需重新登录。</DialogDescription></DialogHeader><Input type="password" autoComplete="off" aria-label="个人访问凭证" value={tokenInput} onChange={(event) => setTokenInput(event.target.value)} /><Button disabled={!tokenInput.trim()} onClick={() => void login()}>登录</Button>{session && <Button variant="outline" onClick={() => { ++syncSequence.current; setSessionToken(""); setSession(null); setConnection("unauthorized"); setDefinitions([]); setBaselineDefinitions([]); setOperations([]); setChangeSets([]); setMetadata(null); setLoginOpen(false); }}>退出登录</Button>}</DialogContent></Dialog>
    <TabsContent value="control" className="min-h-0 flex-1 data-[state=inactive]:hidden"><ControlCenter definitions={definitions} errors={errors} metadata={metadata} connection={connection} onOpenDefinition={(id) => { selectDefinition(id); setView("studio"); }} onOpenStudio={() => setView("studio")} onOpenReview={() => setView("review")} /></TabsContent>
    <TabsContent value="studio" className="min-h-0 flex-1 data-[state=inactive]:hidden">
      <div
        className="relative grid h-full min-h-0 transition-[grid-template-columns] duration-200"
        style={{ gridTemplateColumns: `${leftCollapsed ? 48 : 276}px minmax(0, 1fr) ${selected && !compact ? 332 : 0}px` }}
      >
        <aside className="relative flex min-h-0 overflow-hidden border-r border-[#d5d5d0] bg-[#f4f4f1]">
          <nav className="flex w-12 shrink-0 flex-col items-center gap-1 border-r border-[#d5d5d0] bg-[#e9e9e5] py-2" aria-label="本体工具">
            <button onClick={() => { setFilterKind("all"); setLeftCollapsed(false); }} className={cn("rail-button", filterKind === "all" && !leftCollapsed && "rail-button-active")} aria-label="全部定义" title="全部定义"><LayoutGrid className="size-4" /></button>
            {allKinds.map((kind) => <button key={kind} onClick={() => { setFilterKind(kind); setLeftCollapsed(false); }} className={cn("rail-button", filterKind === kind && !leftCollapsed && "rail-button-active")} aria-label={`查看${kindMeta[kind].label}`} title={`查看${kindMeta[kind].label}`}><span className="text-[11px] font-bold" style={{ color: kindMeta[kind].color }}>{kindMeta[kind].short}</span></button>)}
            <div className="my-1 h-px w-6 bg-[#d2d2cd]" />
            <button onClick={() => setView("review")} className="rail-button relative" aria-label="变更审核" title="变更审核"><GitPullRequestArrow className="size-4" />{changeSets.some((item) => item.status === "review_required") && <span className="absolute right-1 top-1 size-1.5 rounded-full bg-[#e05a67]" />}</button>
            <div className="mt-auto" />
            <button onClick={() => setLeftCollapsed((value) => !value)} className="rail-button" aria-label={leftCollapsed ? "展开左侧目录" : "收起左侧目录"} title={`${leftCollapsed ? "展开" : "收起"}目录  [ `}>{leftCollapsed ? <PanelLeftOpen className="size-4" /> : <PanelLeftClose className="size-4" />}</button>
          </nav>
          {!leftCollapsed && <div className="min-w-0 flex-1">{catalog}</div>}
        </aside>
        <main className="relative min-h-0 overflow-hidden bg-[#f4f4f0]">
          <div className="absolute inset-x-0 top-0 z-10 flex h-[52px] items-center gap-2 border-b border-[#d8d8d3] bg-[#fafaf8]/95 px-2.5 backdrop-blur">
            <div className="flex min-w-0 items-center gap-2"><BookOpen className="size-4 text-[#777770]" /><span className="hidden truncate text-sm font-medium text-[#41413c] sm:block">本体总览</span><span className="text-xs text-[#a0a098]">/</span><span className="truncate text-xs text-[#777770]">{focusMode && selected ? selected.label : "全部定义"}</span><span className="ml-2 hidden rounded-md bg-[#eeece7] px-2 py-1 text-[10px] text-[#777770] 2xl:inline">工作领域 → 概念 → 关系 / 判断 → 策略</span></div>
            <div className="relative ml-2 hidden min-w-[190px] flex-1 sm:block sm:max-w-[300px]"><Search className="absolute left-3 top-1/2 size-3.5 -translate-y-1/2 text-[#8f8f87]" /><Input ref={searchInputRef} value={search} onChange={(e) => setSearch(e.target.value)} placeholder="搜索当前白板" className="h-8 rounded-lg border-[#d8d8d3] bg-white pl-8 pr-8 text-xs shadow-none" /><kbd className="absolute right-2 top-1/2 -translate-y-1/2 rounded border border-[#deded9] px-1.5 py-0.5 font-mono text-[10px] text-[#999991]">/</kbd></div>
            <Select value={lifecycle} onValueChange={(value) => setLifecycle(value as "all" | Lifecycle)}><SelectTrigger size="sm" className="bg-white"><SelectValue /></SelectTrigger><SelectContent><SelectItem value="all">全部状态</SelectItem><SelectItem value="draft">草稿</SelectItem><SelectItem value="active">已启用</SelectItem><SelectItem value="deprecated">已弃用</SelectItem><SelectItem value="merged">已合并</SelectItem><SelectItem value="retired">已退役</SelectItem></SelectContent></Select>
            <Select value={storage} onValueChange={setStorage}><SelectTrigger size="sm" className="hidden bg-white lg:flex"><SelectValue /></SelectTrigger><SelectContent><SelectItem value="all">全部存储</SelectItem><SelectItem value="attr">Attribute</SelectItem><SelectItem value="assertion">Assertion</SelectItem></SelectContent></Select>
            {focusMode && <Button variant="secondary" size="sm" className="rounded-lg bg-[#ece9fb] text-[#5848c6] hover:bg-[#e3dff8]" onClick={() => { setFocusMode(false); requestAnimationFrame(() => requestAnimationFrame(() => flow?.fitView({ padding: .18, duration: 320, maxZoom: .9 }))); }}><Focus />{nodes.length} 项 <X /></Button>}
            {selectedIds.length > 1 && <span className="hidden shrink-0 whitespace-nowrap rounded-md bg-[#ece9fb] px-2 py-1 text-[11px] text-[#5848c6] 2xl:inline-block">已选择 {selectedIds.length} 项</span>}
            <Button size="sm" className="ml-auto rounded-lg bg-[#30302c] text-white hover:bg-[#484843]" onClick={() => setCreateOpen(true)}><Plus />新增 <kbd className="ml-1 font-mono text-[10px] opacity-60">N</kbd></Button>
          </div>
          <div className="absolute inset-0 pt-[52px]">
            <ReactFlow<StudioNode, StudioEdge>
              nodes={nodes}
              edges={edges}
              nodeTypes={nodeTypes}
              edgeTypes={edgeTypes}
              onInit={setFlow}
              onNodesChange={handleNodeChanges}
              onNodeClick={(event, node) => {
                if (event.metaKey || event.ctrlKey || event.shiftKey) return;
                event.stopPropagation();
                // Let React Flow finish its click/selection dispatch before the
                // focused graph removes unrelated nodes and repositions cards.
                requestAnimationFrame(() => selectDefinition(node.id));
              }}
              onNodeDragStop={handleNodeDragStop}
              onPaneClick={(event) => { clearSelection(); if (event.detail === 2) setCreateOpen(true); }}
              fitView
              fitViewOptions={{ padding: .18, maxZoom: .9 }}
              minZoom={.25}
              maxZoom={1.5}
              nodesDraggable
              nodesConnectable={false}
              panOnDrag={!marqueeMode}
              selectionOnDrag={marqueeMode}
              selectionKeyCode="Shift"
              deleteKeyCode={null}
              aria-label="KnowledgeOS 本体依赖图"
            >
              <Background color="#d8d8d2" gap={22} size={1} />
              <Controls className="!overflow-hidden !rounded-lg !border !border-[#d7d7d2] !bg-white !shadow-md" />
              <Panel position="top-left" className="!left-3 !top-3"><div className="flex flex-col gap-1 rounded-lg border border-[#d7d7d2] bg-white/95 p-1 shadow-md"><button className={cn("canvas-tool", !marqueeMode && "canvas-tool-active")} title="平移与选择 V" aria-label="平移与选择" onClick={() => setMarqueeMode(false)}><MousePointer2 className="size-4" /></button><button className={cn("canvas-tool", marqueeMode && "canvas-tool-active")} title="框选工具 B" aria-label="框选工具" aria-pressed={marqueeMode} onClick={() => setMarqueeMode(true)}><Scan className="size-4" /></button><button className="canvas-tool" title="新增定义 N" onClick={() => setCreateOpen(true)}><Plus className="size-4" /></button><button className="canvas-tool" title="显示所选关联 F" disabled={!selected} onClick={() => selected && setFocusMode(true)}><Focus className="size-4" /></button><button className="canvas-tool" title="复位布局" onClick={resetLayout}><RotateCcw className="size-4" /></button></div></Panel>
              <Panel position="top-center" className="!top-3"><div className="hidden items-center gap-2 rounded-lg border border-[#d7d7d2] bg-white/90 px-2.5 py-1.5 text-[11px] text-[#777770] shadow-sm lg:flex">{allKinds.map((kind) => <span key={kind} className="flex items-center gap-1"><span className="size-1.5 rounded-full" style={{ background: kindMeta[kind].color }} />{kindMeta[kind].label}</span>)}</div></Panel>
              <Panel position="bottom-center" className="!bottom-3"><div className="hidden rounded-lg border border-[#d7d7d2] bg-white/90 px-3 py-1.5 text-[11px] text-[#7d7d75] shadow-sm md:block">点击卡片查看关联 · 空白处拖动平移 · <kbd>B</kbd> 切换框选 · <kbd>V</kbd> 返回平移 · ⌘/Ctrl + 点击多选 · 点击空白取消选择 · 双击空白新增</div></Panel>
              <Panel position="bottom-left" className="!bottom-3 !left-14"><div className="rounded-lg border border-[#d8d5e8] bg-white/95 px-2.5 py-1.5 text-[11px] text-[#655a91] shadow-sm"><b>{selected?.kind === "domain" ? "领域入口：" : selected?.kind === "relation" ? "关系建模：" : "建模路径："}</b>{selected?.kind === "domain" ? "先确认概念，再展开其关系与判断。" : selected?.kind === "relation" ? `${relationModeLabel(selected.relationMode)}；${(selected.endpoints ?? []).length ? "箭头由源概念指向目标概念。" : "尚未声明 domain/range。"}` : "概念连接判断类型，并通过有向关系连接其他概念。"}</div></Panel>
            </ReactFlow>
          </div>
          {operations.length > 0 && <div className="absolute bottom-3 left-1/2 z-20 flex min-w-[500px] -translate-x-1/2 items-center gap-3 rounded-xl border border-[#45453f] bg-[#2d2d29] px-3 py-2 text-white shadow-xl"><div className="flex size-8 shrink-0 items-center justify-center rounded-lg bg-white/10"><GitCompareArrows className="size-4 text-[#c9c3ff]" /></div><div className="min-w-0 flex-1"><p className="text-xs font-semibold">{operations.length} 项变更待提交</p><p className="truncate text-[11px] text-[#aaa9a2]">{errors.length ? `${errors.length} 个错误需要修复` : warnings.length ? `${warnings.length} 个警告` : "校验通过"}</p></div><Button size="sm" disabled={errors.length > 0} onClick={() => setSubmitOpen(true)} className="h-8 rounded-lg bg-[#7465df] text-white hover:bg-[#6557d5]">审核差异<ArrowRight /></Button></div>}
        </main>
        {selected && <aside className={cn("relative min-h-0 overflow-hidden border-l border-[#d5d5d0] bg-[#fafaf8]", compact && "absolute inset-y-0 right-0 z-30 w-[min(332px,calc(100vw-48px))] shadow-xl")} aria-label={`${selected.label} 属性详情`}>{inspector}</aside>}
      </div>
    </TabsContent>
    <TabsContent value="review" className="min-h-0 flex-1 data-[state=inactive]:hidden"><ReviewCenter changeSets={changeSets} busy={mutationBusy} onReview={handleReview} onApply={handleApply} onPublish={handlePublish} /></TabsContent>
    <div className="fixed inset-x-3 bottom-3 z-40 flex justify-center md:hidden"><div className="flex rounded-xl border border-[#44443f] bg-[#2d2d29] p-1 shadow-xl"><button onClick={() => setView("control")} className={cn("rounded-lg px-3 py-2 text-xs font-medium", view === "control" ? "bg-[#7465df] text-white" : "text-[#aaa9a2]")}><ShieldCheck className="mr-1 inline size-4" />总览</button><button onClick={() => setView("studio")} className={cn("rounded-lg px-3 py-2 text-xs font-medium", view === "studio" ? "bg-[#7465df] text-white" : "text-[#aaa9a2]")}><Network className="mr-1 inline size-4" />白板</button><button onClick={() => setView("review")} className={cn("rounded-lg px-3 py-2 text-xs font-medium", view === "review" ? "bg-[#7465df] text-white" : "text-[#aaa9a2]")}><GitPullRequestArrow className="mr-1 inline size-4" />审核</button></div></div>
    <CommandDialog open={commandOpen} onOpenChange={setCommandOpen} title="快速命令" description="搜索定义或执行当前白板操作" className="sm:max-w-xl">
      <CommandInput placeholder="搜索定义或输入命令…" />
      <CommandList>
        <CommandEmpty>没有匹配的定义或命令。</CommandEmpty>
        <CommandGroup heading="白板动作">
          <CommandItem onSelect={() => { setCreateOpen(true); setCommandOpen(false); }}><Plus />新增本体定义<CommandShortcut>N</CommandShortcut></CommandItem>
          <CommandItem onSelect={() => { setFocusMode(false); setCommandOpen(false); requestAnimationFrame(() => flow?.fitView({ padding: .18, duration: 320, maxZoom: .9 })); }}><LayoutGrid />显示全部定义<CommandShortcut>Esc</CommandShortcut></CommandItem>
          <CommandItem disabled={!selectedId} onSelect={() => { if (selectedId) setFocusMode(true); setCommandOpen(false); }}><Focus />聚焦所选关联<CommandShortcut>F</CommandShortcut></CommandItem>
          <CommandItem onSelect={() => { setLeftCollapsed((value) => !value); setCommandOpen(false); }}><PanelLeftClose />切换左侧栏<CommandShortcut>[</CommandShortcut></CommandItem>
          <CommandItem disabled={!selectedId} onSelect={() => { clearSelection(); setCommandOpen(false); }}><PanelRightClose />关闭右侧详情<CommandShortcut>]</CommandShortcut></CommandItem>
        </CommandGroup>
        <CommandGroup heading="本体定义">
          {visibleDefinitions.map((item) => <CommandItem key={item.id} value={`${item.label} ${item.id} ${kindMeta[item.kind].label}`} onSelect={() => { selectDefinition(item.id); setCommandOpen(false); }}><span className="flex size-6 items-center justify-center rounded-md text-[10px] font-bold text-white" style={{ background: kindMeta[item.kind].color }}>{kindMeta[item.kind].short}</span><span>{item.label}</span><CommandShortcut>{item.id}</CommandShortcut></CommandItem>)}
        </CommandGroup>
      </CommandList>
    </CommandDialog>
    <CreateDialog open={createOpen} onOpenChange={setCreateOpen} definitions={definitions} onCreate={createDefinition} /><ConceptBindingsDialog open={Boolean(bindingConceptId)} onOpenChange={(value) => { if (!value) setBindingConceptId(null); }} concept={bindingConcept} definitions={definitions} onUpdateConcept={stageUpdate} onCreateAndBind={createAndBindPredicate} /><RelationEndpointsDialog open={Boolean(endpointRelationId)} onOpenChange={(value) => { if (!value) setEndpointRelationId(null); }} relation={endpointRelation} definitions={definitions} onUpdateRelation={stageUpdate} onCreateAndBind={createAndBindReificationPredicate} /><SubmitDialog open={submitOpen} onOpenChange={setSubmitOpen} operations={operations} warnings={warnings} onSubmit={submit} /><Toaster richColors position="top-right" />
  </Tabs></TooltipProvider>;
}

export default function Home() { return <ReactFlowProvider><OntologyStudio /></ReactFlowProvider>; }
