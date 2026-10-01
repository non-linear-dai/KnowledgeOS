import type { OntologyDefinition } from "./studio-data";

export interface RegistryOption { value: string; label: string }

// Keep this list aligned with LEGACY_UNITS in src/knowledge_os/model_contract.py.
const modelBuiltInUnits = [
  "one", "currency", "currency_per_unit", "currency_per_hour", "hour_per_unit",
  "minute_per_unit", "calendar_days", "count", "risk_points", "iso8601_date",
  "risk_points_per_calendar_day", "risk_points_per_count",
];

const ordered = (options: RegistryOption[]) => options.sort((a, b) => a.value.localeCompare(b.value));
const registryId = (definition: OntologyDefinition) => String(definition.config.id ?? definition.id.replace(/^(unit|currency):/, ""));

export function dimensionOptions(definitions: OntologyDefinition[]): RegistryOption[] {
  return ordered([...new Set(definitions.filter((item) => item.kind === "unit").map((item) => String(item.config.dimension ?? "")).filter(Boolean))]
    .map((value) => ({ value, label: value })));
}

export function physicalUnitOptions(definitions: OntologyDefinition[], dimension: string): RegistryOption[] {
  return ordered(definitions.filter((item) => item.kind === "unit" && item.config.dimension === dimension)
    .map((item) => ({ value: registryId(item), label: `${registryId(item)} · ${item.label}` })));
}

export function currencyOptions(definitions: OntologyDefinition[]): RegistryOption[] {
  return ordered(definitions.filter((item) => item.kind === "currency")
    .map((item) => ({ value: registryId(item), label: `${registryId(item)} · ${item.label}` })));
}

export function modelUnitOptions(definitions: OntologyDefinition[]): RegistryOption[] {
  const physical = definitions.filter((item) => item.kind === "unit").map((item) => ({ value: registryId(item), label: `${registryId(item)} · ${item.label}` }));
  return ordered([...new Map([...modelBuiltInUnits.map((value) => ({ value, label: value })), ...physical]
    .map((option) => [option.value, option])).values()]);
}

export function conceptOptions(definitions: OntologyDefinition[]): RegistryOption[] {
  return ordered(definitions.filter((item) => item.kind === "concept").map((item) => ({ value: item.id, label: `${item.label} · ${item.id}` })));
}

export function outputPredicateOptions(definitions: OntologyDefinition[], conceptId: string): RegistryOption[] {
  const concept = definitions.find((item) => item.kind === "concept" && item.id === conceptId);
  const bound = new Set((concept?.bindings ?? []).map((binding) => binding.predicateId));
  return ordered(definitions.filter((item) => item.kind === "predicate" && bound.has(item.id)
    && (item.config.storage_mode === "assertion" || item.config.storage_mode === "external"))
    .map((item) => ({ value: item.id, label: `${item.label} · ${item.id}` })));
}
