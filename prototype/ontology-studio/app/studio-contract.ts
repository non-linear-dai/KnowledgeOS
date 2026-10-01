import { z } from "zod";
// Vendored from control/schemas/studio-snapshot.schema.json so the Sites
// checkout can validate the wire contract without reaching outside its root.
import contract from "./studio-snapshot.schema.json";

const definition = contract.$defs.definition.properties;
const data = contract.properties.data.properties;
export const wireDefinition = z.object({
  id: z.string().min(definition.id.minLength),
  kind: z.enum(definition.kind.enum as [string, ...string[]]),
  label: z.string(), description: z.string(),
  lifecycle: z.enum(definition.lifecycle.enum as [string, ...string[]]),
  source_path: z.string(), config: z.record(z.unknown()),
}).passthrough();
export const wireSnapshot = z.object({ data: z.object({
  contract_version: z.string(), registry_fingerprint: z.string().min(1),
  definitions: z.array(wireDefinition),
  changesets: z.array(z.object({ id: z.string(), status: z.enum(data.changesets.items.properties.status.enum as [string, ...string[]]) }).passthrough()),
  coverage: z.record(z.number()), extensions: z.record(z.number()), capabilities: z.record(z.unknown()),
}).passthrough() });
export type StudioWireSnapshot = z.infer<typeof wireSnapshot>;

export function connectionFailure(status: number, code?: string) {
  if (status === 401) return "unauthorized" as const;
  if (status === 403) return "forbidden" as const;
  if (code === "BACKEND_NOT_CONFIGURED") return "unconfigured" as const;
  return "offline" as const;
}
