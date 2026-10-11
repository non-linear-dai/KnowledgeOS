import type { Node, NodeChange } from "@xyflow/react";
import type { DependencyEdge, OntologyDefinition } from "./studio-data";

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
