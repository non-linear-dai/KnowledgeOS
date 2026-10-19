import type { ExpertTemplate, Preview, SampleCase } from "./template-api";

export type TemplateCaseCheck = {
  id: string;
  expected: number;
  actual: number | null;
  passed: boolean;
  error?: string;
};

// Use the same expansion engine as submission: repeat groups and conditions can
// make the expanded count different from the number of cards on the whiteboard.
export async function checkTemplateCases(
  template: ExpertTemplate,
  preview: (template: ExpertTemplate, sample: SampleCase) => Promise<Preview>,
  signal?: AbortSignal,
): Promise<TemplateCaseCheck[]> {
  const checks: TemplateCaseCheck[] = [];
  for (const sample of template.cases) {
    signal?.throwIfAborted();
    try {
      const result = await preview(template, sample);
      checks.push({ id: sample.id, expected: sample.expected_operations,
        actual: result.operation_count, passed: result.matches_expected });
    } catch (cause) {
      checks.push({ id: sample.id, expected: sample.expected_operations,
        actual: null, passed: false, error: cause instanceof Error ? cause.message : "案例展开失败" });
    }
    signal?.throwIfAborted();
  }
  return checks;
}

export function templateCasesPass(checks: TemplateCaseCheck[]): boolean {
  return checks.length > 0 && checks.every(check => check.passed);
}
