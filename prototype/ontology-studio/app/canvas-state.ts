import type { Node, NodeChange } from "@xyflow/react";
import type { DefinitionKind, DependencyEdge, Lifecycle, OntologyDefinition } from "./studio-data";

export type DefinitionFilters = { search: string; kind: "all" | DefinitionKind; lifecycle: "all" | Lifecycle; storage: string };

export function matchesDefinitionFilters(definition: OntologyDefinition, filters: DefinitionFilters): boolean {
  const query = filters.search.trim().toLowerCase();
  return (!query || `${definition.id} ${definition.label} ${definition.description}`.toLowerCase().includes(query))
    && (filters.kind === "all" || definition.kind === filters.kind)
    && (filters.lifecycle === "all" || definition.lifecycle === filters.lifecycle)
    && (filters.storage === "all" || definition.config.storage_mode === filters.storage);
}

/** A temporary, compact result layout; the authored board positions stay intact. */
export function layoutFilteredNodes<T extends Node>(nodes: T[], area: { width: number; height: number }, positions: Record<string, { x: number; y: number }> = {}): T[] {
  let columns = 1, bestScale = 0;
  for (let count = 1; count <= nodes.length; count++) {
    const rows = Math.ceil(nodes.length / count);
    const scale = Math.min(area.width / (count * 280 - 32), area.height / (rows * 192 - 32));
    if (scale > bestScale) { bestScale = scale; columns = count; }
  }
  return nodes.map((node, index) => ({ ...node, position: positions[node.id] ?? { x: (index % columns) * 280, y: Math.floor(index / columns) * 192 } }));
}

/** Fit the projected nodes into the unobscured canvas, without a clipping zoom floor. */
export function canvasViewportForNodes(nodes: Node[], area: { x: number; y: number; width: number; height: number }, maxZoom = .9) {
  if (!nodes.length || area.width <= 0 || area.height <= 0) return null;
  const left = Math.min(...nodes.map(node => node.position.x));
  const top = Math.min(...nodes.map(node => node.position.y));
  const right = Math.max(...nodes.map(node => node.position.x + (node.measured?.width ?? node.width ?? node.initialWidth ?? 248)));
  const bottom = Math.max(...nodes.map(node => node.position.y + (node.measured?.height ?? node.height ?? node.initialHeight ?? 160)));
  const zoom = Math.min(maxZoom, area.width * .9 / Math.max(1, right - left), area.height * .9 / Math.max(1, bottom - top));
  return { x: area.x + area.width / 2 - (left + right) / 2 * zoom, y: area.y + area.height / 2 - (top + bottom) / 2 * zoom, zoom };
}

export function focusedDefinitionEdges(selectedId: string | null, definitions: OntologyDefinition[], edges: DependencyEdge[]): DependencyEdge[] {
  if (!selectedId) return [];
  const included = new Set(edges.filter((edge) => edge.source === selectedId || edge.target === selectedId).map((edge) => edge.id));
  if (definitions.find((item) => item.id === selectedId)?.kind === "concept") {
    for (const relation of definitions) {
      if (relation.kind !== "relation") continue;
      (relation.endpoints ?? []).forEach((endpoint, index) => {
        if (endpoint.sourceConceptId === selectedId) included.add(`relation-target-${relation.id}-${index}`);
        if (endpoint.targetConceptId === selectedId) included.add(`relation-source-${relation.id}-${index}`);
      });
    }
  }
  return edges.filter((edge) => included.has(edge.id));
}

export function focusedDefinitionIds(selectedId: string | null, definitions: OntologyDefinition[], edges: DependencyEdge[]): Set<string> {
  const ids = new Set<string>();
  if (!selectedId) return ids;
  ids.add(selectedId);
  for (const edge of focusedDefinitionEdges(selectedId, definitions, edges)) {
    ids.add(edge.source);
    ids.add(edge.target);
  }
  return ids;
}

export function uniqueEdgesById<T extends { id: string }>(edges: T[]): T[] {
  const seen = new Set<string>();
  return edges.filter((edge) => {
    if (seen.has(edge.id)) return false;
    seen.add(edge.id);
    return true;
  });
}

export function reconcileCanvasNodes<T extends Node>(current: T[], projected: T[], resetLayout = false): T[] {
  const previous = new Map(current.map((node) => [node.id, node]));
  return projected.map((node) => {
    const old = previous.get(node.id);
    if (!old) return node;
    return {
      ...node,
      position: resetLayout ? node.position : old.position,
      measured: old.measured ?? node.measured,
      width: old.width ?? node.width,
      height: old.height ?? node.height,
      dragging: old.dragging,
    };
  });
}

export function layoutFocusedNodes<T extends Node>(
  nodes: T[],
  kindOf: (node: T) => string,
  kindOrder: readonly string[],
  selectedId: string | null,
  positions: Record<string, { x: number; y: number }> = {},
): T[] {
  const kinds = [...new Set(nodes.map(kindOf))].sort((a, b) => {
    const aIndex = kindOrder.indexOf(a);
    const bIndex = kindOrder.indexOf(b);
    return (aIndex < 0 ? kindOrder.length : aIndex) - (bIndex < 0 ? kindOrder.length : bIndex) || a.localeCompare(b);
  });
  const columns = new Map(kinds.map((kind, index) => [kind, index]));
  const rowById = new Map<string, number>();
  for (const kind of kinds) {
    const columnNodes = nodes.filter((node) => kindOf(node) === kind);
    columnNodes.sort((a, b) => Number(b.id === selectedId) - Number(a.id === selectedId));
    columnNodes.forEach((node, index) => rowById.set(node.id, index));
  }
  return nodes.map((node) => ({
    ...node,
    position: positions[node.id] ?? {
      x: 40 + (columns.get(kindOf(node)) ?? 0) * 312,
      y: 60 + (rowById.get(node.id) ?? 0) * 192,
    },
  }));
}

export function selectionAfterChanges(current: string[], changes: NodeChange[]): string[] {
  const selected = new Set(current);
  for (const change of changes) {
    if (change.type !== "select") continue;
    if (change.selected) selected.add(change.id);
    else selected.delete(change.id);
  }
  return [...selected];
}
