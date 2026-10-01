import type { Node, NodeChange } from "@xyflow/react";

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

export function selectionAfterChanges(current: string[], changes: NodeChange[]): string[] {
  const selected = new Set(current);
  for (const change of changes) {
    if (change.type !== "select") continue;
    if (change.selected) selected.add(change.id);
    else selected.delete(change.id);
  }
  return [...selected];
}
