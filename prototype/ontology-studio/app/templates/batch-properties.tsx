"use client";

import { useState } from 'react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { ConditionEditor } from './editor-fields';
import { DecisionBindingEditor } from './decision-editor';
import type { Condition, DecisionBinding, DecisionTable, RouteStep } from './template-api';

type BatchPatch = Partial<Pick<RouteStep, 'cost_basis' | 'when' | 'decision_binding'>>;

export function BatchProperties({ steps, editable, allowItem, decisions, onApply, onLibrary, onDecisions }: {
  steps: RouteStep[]; editable: boolean; allowItem: boolean; decisions: DecisionTable[];
  onApply: (patch: BatchPatch) => boolean; onLibrary: () => void; onDecisions: () => void;
}) {
  const [basis, setBasis] = useState(steps.every(s => s.cost_basis === steps[0].cost_basis) ? steps[0].cost_basis : '');
  const [condition, setCondition] = useState<Condition | undefined>();
  const [binding, setBinding] = useState<DecisionBinding | undefined>();
  const [fields, setFields] = useState({ basis: false, condition: false, decision: false });
  const changed = Object.values(fields).some(Boolean);
  return <section className="space-y-4">
    <p className="rounded-xl bg-indigo-50 p-3 text-xs leading-5 text-indigo-700">批量配置 {steps.length} 道工序。勾选需要统一的字段后，一次应用；工序名称和前置关系保留。</p>
    <Button className="w-full" variant="outline" disabled={!editable} onClick={onLibrary}>统一选择标准工序</Button>
    <label className="flex items-center gap-2 text-xs"><input type="checkbox" checked={fields.basis} disabled={!editable} onChange={e => setFields(f => ({ ...f, basis: e.target.checked }))} />统一计费对象</label>
    {fields.basis && <Input aria-label="批量计费对象" value={basis} disabled={!editable} onChange={e => setBasis(e.target.value)} placeholder="如 panel、wafer、piece" />}
    <label className="flex items-center gap-2 text-xs"><input type="checkbox" checked={fields.condition} disabled={!editable} onChange={e => setFields(f => ({ ...f, condition: e.target.checked }))} />统一加工条件</label>
    {fields.condition && <ConditionEditor condition={condition} editable={editable} allowItem={allowItem} onChange={setCondition} />}
    <label className="flex items-center gap-2 text-xs"><input type="checkbox" checked={fields.decision} disabled={!editable} onChange={e => setFields(f => ({ ...f, decision: e.target.checked }))} />统一决策引用</label>
    {fields.decision && <DecisionBindingEditor tables={decisions} binding={binding} editable={editable} operation onChange={setBinding} onOpen={onDecisions} />}
    {(fields.condition && !condition || fields.decision && !binding) && <p className="text-xs text-amber-700">未设置的已勾选条件／决策将被清除。</p>}
    <Button className="w-full" disabled={!editable || !changed || fields.basis && !basis.trim()} onClick={() => {
      const applied = onApply({ ...(fields.basis ? { cost_basis: basis.trim() } : {}), ...(fields.condition ? { when: condition } : {}), ...(fields.decision ? { decision_binding: binding } : {}) });
      if (applied) setFields({ basis: false, condition: false, decision: false });
    }}>应用到 {steps.length} 道工序</Button>
    <div className="max-h-48 space-y-1 overflow-auto border-t pt-3">{steps.map(step => <p key={step.id} className="truncate text-xs text-slate-500">{step.label}</p>)}</div>
  </section>;
}
