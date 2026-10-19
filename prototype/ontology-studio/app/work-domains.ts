import type { OntologyDefinition } from "./studio-data";

export type WorkDomain = { id: string; label: string };
const labels: Record<string, string> = { "Cost analysis": "成本分析", "Industry research": "行业研究", "Project management": "项目管理" };
export function workDomainLabel(domain: WorkDomain): string { return labels[domain.label] ?? domain.label; }
export function workDomains(definitions: OntologyDefinition[]): WorkDomain[] {
  return definitions.filter(definition => definition.kind === "domain").map(definition => ({ id: definition.id.replace(/^domain:/, ""), label: definition.label }));
}
const strings = (value: unknown): string[] => Array.isArray(value) ? value.filter((item): item is string => typeof item === "string") : [];
const record = (value: unknown): Record<string, unknown> => value && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : {};

export function domainDefinitionIds(definitions: OntologyDefinition[], domainId: string): Set<string> {
  const domain = definitions.find(definition => definition.kind === "domain" && definition.id === `domain:${domainId}`);
  if (!domain) return new Set();
  // Live snapshots carry the server's registered, deterministic scope.
  if (Array.isArray(domain.config.definition_scope)) return new Set(strings(domain.config.definition_scope));
  // Offline demonstrations use the same declared roots and outward dependencies.
  const retrieval = record(domain.config.retrieval);
  const ids = new Set([...(domain.conceptScopes ?? []), ...strings(retrieval.relation_types), ...strings(retrieval.predicate_priority), ...strings(domain.config.required_models).map(id => `model:${id}`)]);
  for (const definition of definitions.filter(item => item.kind === "model" && ids.has(item.id))) {
    if (typeof definition.config.applies_to === "string") ids.add(definition.config.applies_to);
    if (typeof definition.config.output_predicate === "string") ids.add(definition.config.output_predicate);
    for (const input of Array.isArray(definition.config.inputs) ? definition.config.inputs : []) {
      const predicate = record(input).predicate;
      if (typeof predicate === "string") ids.add(predicate);
    }
  }
  for (const definition of definitions) {
    if (ids.has(definition.id)) {
      for (const binding of [...(definition.bindings ?? []), ...(definition.reification?.properties ?? [])]) ids.add(binding.predicateId);
    }
    if (definition.kind === "business_constraint" || definition.kind === "business_rule") {
      const scope = record(definition.config.scope);
      const concepts = [scope.subject_concept, scope.candidate_concept, ...Object.values(record(scope.context_concepts))].filter((id): id is string => typeof id === "string");
      if (concepts.length && concepts.every(id => ids.has(id)) && (!scope.relation_type || ids.has(String(scope.relation_type)))) {
        ids.add(definition.id);
        for (const input of Array.isArray(definition.config.inputs) ? definition.config.inputs : []) {
          const predicate = record(input).predicate;
          if (typeof predicate === "string") ids.add(predicate);
        }
      }
    }
  }
  return new Set(definitions.filter(definition => ids.has(definition.id)).map(definition => definition.id));
}
