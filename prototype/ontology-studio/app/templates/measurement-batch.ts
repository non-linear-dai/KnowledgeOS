/** Defer React Flow's DOM measurement writes out of ResizeObserver delivery.
 * One frame owns all pending measurements; the latest element for an ID wins.
 * The store's own update function still computes handles, parents and sizes.
 */
export function createMeasurementBatch<K, V>(
  apply: (updates: Map<K, V>) => void,
  requestFrame: (callback: () => void) => number,
  cancelFrame: (id: number) => void,
) {
  let pending = new Map<K, V>();
  let frame: number | null = null;
  let disposed = false;
  return {
    update(updates: Map<K, V>) {
      if (disposed || !updates.size) return;
      for (const [id, update] of updates) pending.set(id, update);
      if (frame !== null) return;
      frame = requestFrame(() => {
        frame = null;
        if (disposed) return;
        const batch = pending;
        pending = new Map();
        apply(batch);
      });
    },
    dispose() {
      disposed = true;
      if (frame !== null) cancelFrame(frame);
      frame = null;
      pending.clear();
    },
  };
}
