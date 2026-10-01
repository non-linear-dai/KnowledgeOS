import type { DefinitionKind, OntologyDefinition } from "./studio-data";

// The whiteboard is for ontology and schema authoring. Fixed registries and
// operational configuration remain in the control plane, outside this canvas.
export const whiteboardKinds: DefinitionKind[] = ["schema", "concept", "relation", "predicate", "model", "business_constraint", "business_rule"];

export function whiteboardDefinitions(definitions: OntologyDefinition[]): OntologyDefinition[] {
  return definitions.filter((definition) => whiteboardKinds.includes(definition.kind));
}
