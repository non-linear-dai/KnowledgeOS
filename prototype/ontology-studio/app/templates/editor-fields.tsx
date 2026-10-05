"use client";

import { Plus, Trash2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import type { Condition, Comparison, OperationTemplate, ResourceRequirement, SampleCase, TimeModel } from "./template-api";

function nextKey(prefix: string, used: string[]) {
  let n = 1;
  while (used.includes(`${prefix}_${n}`)) n++;
  return `${prefix}_${n}`;
}

function parseValue(raw: string): string | number | boolean {
  const value = raw.trim();
  if (value === "true") return true;
  if (value === "false") return false;
  if (/^-?(?:0|[1-9]\d*)(?:\.\d+)?$/.test(value)) return Number(value);
  return value;
}

function displayValue(value: unknown) {
  return typeof value === "object" ? JSON.stringify(value) : String(value);
}

export function ConditionEditor({ condition, onChange, allowItem, editable }: { condition?: Condition; onChange: (next?: Condition) => void; allowItem: boolean; editable: boolean }) {
  if (condition && ("all" in condition || "any" in condition)) {
    const key = "all" in condition ? "all" : "any", children = "all" in condition ? condition.all : condition.any;
    const replace = (items:Condition[]) => onChange(items.length ? key === "all" ? {all:items} : {any:items} : undefined);
    return <section className="space-y-2 rounded-xl border p-3"><div className="flex gap-2"><button disabled={!editable} className="text-xs font-medium text-indigo-600" onClick={()=>onChange(key==="all"?{any:children}:{all:children})}>{key==="all"?"全部条件满足（AND）":"任一条件满足（OR）"}</button>{editable&&<button className="ml-auto text-xs" onClick={()=>replace([...children,{source:"facts",field:"enabled",op:"eq",value:true}])}>＋条件</button>}</div>{children.map((child,index)=><ConditionEditor key={index} condition={child} editable={editable} allowItem={allowItem} onChange={next=>replace(next?children.map((c,i)=>i===index?next:c):children.filter((_,i)=>i!==index))}/>)}</section>;
  }
  return (
    <section className="rounded-xl border border-slate-200 bg-slate-50 p-3">
      <div className="mb-2 flex items-center justify-between">
        <div><p className="text-sm font-medium">启用条件</p><p className="text-xs text-slate-500">由预览器确定性判断；缺少输入时显示错误。</p></div>
        {editable && <div className="flex flex-col gap-1"><Button variant="outline" size="sm" onClick={() => onChange(condition ? undefined : { source: "facts", field: "enabled", op: "eq", value: true })}>{condition ? "移除条件" : "添加条件"}</Button>{condition&&<button className="text-xs text-indigo-600" onClick={()=>onChange({all:[condition]})}>组合条件</button>}</div>}
      </div>
      {condition && <div className="grid grid-cols-2 gap-2 lg:grid-cols-4">
        <select className="h-9 rounded-md border border-slate-300 bg-white px-2 text-sm" disabled={!editable} value={condition.source} onChange={(event) => onChange({ ...condition, source: event.target.value as Comparison["source"] })}>
          <option value="facts">场景事实</option>{allowItem && <option value="item">当前对象</option>}
        </select>
        <Input aria-label="条件字段" placeholder="字段名" disabled={!editable} value={condition.field} onChange={(event) => onChange({ ...condition, field: event.target.value })} />
        <select className="h-9 rounded-md border border-slate-300 bg-white px-2 text-sm" disabled={!editable} value={condition.op} onChange={(event) => onChange({ ...condition, op: event.target.value as Comparison["op"] })}>
          <option value="eq">等于</option><option value="ne">不等于</option><option value="gt">大于</option><option value="gte">大于等于</option><option value="lt">小于</option><option value="lte">小于等于</option>
        </select>
        <Input aria-label="条件比较值" placeholder="比较值" disabled={!editable} value={displayValue(condition.value)} onChange={(event) => onChange({ ...condition, value: parseValue(event.target.value) })} />
      </div>}
    </section>
  );
}

export function FactRows({ facts, onChange, editable }: { facts: SampleCase["facts"]; onChange: (next: SampleCase["facts"]) => void; editable: boolean }) {
  return <div className="space-y-2">
    {Object.entries(facts).map(([key, value]) => <div key={key} className="flex items-center gap-2">
      <Input className="w-40" aria-label="字段名" disabled={!editable} value={key} onChange={(event) => {
        const renamed = event.target.value.trim();
        if (!renamed || renamed in facts && renamed !== key) return;
        const copy = { ...facts }; delete copy[key]; copy[renamed] = value; onChange(copy);
      }} />
      {typeof value === "object" ? <><Input aria-label="字段值" disabled={!editable} value={value.literal} onChange={event=>onChange({...facts,[key]:{...value,literal:event.target.value}})}/><Input aria-label="字段单位" disabled={!editable} value={value.unit} onChange={event=>onChange({...facts,[key]:{...value,unit:event.target.value}})}/></> : <Input aria-label="字段值" disabled={!editable} value={displayValue(value)} onChange={(event) => onChange({ ...facts, [key]: parseValue(event.target.value) })} />}
      {editable && <Button variant="ghost" size="icon-sm" aria-label={`删除 ${key}`} onClick={() => { const copy = { ...facts }; delete copy[key]; onChange(copy); }}><Trash2 className="size-4" /></Button>}
    </div>)}
    {editable && <Button variant="outline" size="sm" onClick={() => onChange({ ...facts, [nextKey("field", Object.keys(facts))]: "" })}><Plus className="size-3.5" /> 添加字段</Button>}
  </div>;
}

const resourceKinds: ResourceRequirement["kind"][] = ["material", "part", "equipment", "energy", "labor", "facility"];

export function OperationEditor({ operation, editable, units, models, quantityModels=[], onChange, onRemove }: {
  operation: OperationTemplate; editable: boolean; units: string[]; models: TimeModel[]; quantityModels?:TimeModel[];
  onChange: (values: Partial<OperationTemplate>) => void; onRemove: () => void;
}) {
  function changeResource(index: number, values: Partial<ResourceRequirement>) {
    const resources = structuredClone(operation.resources ?? []);
    Object.assign(resources[index], values);
    onChange({ resources });
  }
  const processing=operation.processing??{};
  const setProcessing=(values:Partial<NonNullable<OperationTemplate["processing"]>>)=>onChange({processing:{...processing,...values}});
  return <div className="space-y-3 rounded-xl border p-4">
    <div className="flex items-center justify-between"><strong className="text-sm">{operation.label}</strong>{editable && <Button variant="ghost" size="icon-sm" aria-label="删除标准工序" onClick={onRemove}><Trash2 className="size-4" /></Button>}</div>
    <div className="grid grid-cols-2 gap-2">
      <label className="text-xs text-slate-500">稳定标识<Input className="mt-1" disabled={!editable} value={operation.id} onChange={(event) => onChange({ id: event.target.value })} /></label>
      <label className="text-xs text-slate-500">版本<Input className="mt-1" disabled={!editable} value={operation.version} onChange={(event) => onChange({ version: event.target.value })} /></label>
      <label className="text-xs text-slate-500">工序名称<Input className="mt-1" disabled={!editable} value={operation.label} onChange={(event) => onChange({ label: event.target.value })} /></label>
      <label className="text-xs text-slate-500">默认计费对象（说明）<Input className="mt-1" disabled={!editable} value={operation.cost_basis} onChange={(event) => onChange({ cost_basis: event.target.value })} /></label>
      <label className="text-xs text-slate-500">工艺方法<Input className="mt-1" disabled={!editable} placeholder="如精铣、注塑、蚀刻" value={operation.method ?? ""} onChange={(event) => onChange({ method: event.target.value })} /></label>
      <label className="text-xs text-slate-500">加工时间模型
        <select className="mt-1 h-9 w-full rounded-md border border-slate-300 bg-white px-2 text-sm" disabled={!editable} value={operation.time_model_ref ?? ""} onChange={(event) => onChange({ time_model_ref: event.target.value || undefined })}>
          <option value="">{models.length ? "暂不指定" : "暂无已注册的加工时间模型"}</option>{models.map((model) => <option key={model.id} value={`${model.id}@${model.version}`}>{model.id} · v{model.version}</option>)}
        </select>
      </label>
    </div>
    <section className="space-y-3 rounded-lg bg-indigo-50 p-3"><h4 className="text-xs font-semibold">加工与批量</h4><label className="block text-xs">单次加工数量<Input aria-label="单次加工数量" disabled={!editable} value={processing.batch_size??"1"} onChange={e=>setProcessing({batch_size:e.target.value})}/></label>
      {([['duration','单次加工时长'],['setup','本批准备时长']] as const).map(([key,label])=><label key={key} className="block text-xs">{label}<div className="mt-1 flex gap-2"><Input aria-label={label} placeholder={key==='duration'&&operation.time_model_ref?'由模型计算':'未配置'} disabled={!editable||key==='duration'&&Boolean(operation.time_model_ref)} value={processing[key]?.value??''} onChange={e=>setProcessing({[key]:e.target.value?{value:e.target.value,unit:processing[key]?.unit??'minute'}:undefined})}/><button disabled={!editable} className="rounded border px-2 text-xs" onClick={()=>setProcessing({[key]:{value:processing[key]?.value??'0',unit:processing[key]?.unit==='hour'?'minute':processing[key]?.unit==='minute'?'second':'hour'}})}>{processing[key]?.unit??'minute'}</button></div></label>)}
      {operation.time_model_ref&&models.find(m=>`${m.id}@${m.version}`===operation.time_model_ref)?.inputs?.map(input=><label key={input.id} className="block text-xs">模型输入 {input.id} · {input.unit}<Input aria-label={`时间输入 ${input.id}`} placeholder="场景事实字段名" disabled={!editable} value={processing.time_inputs?.[input.id]?.field??''} onChange={e=>setProcessing({time_inputs:{...processing.time_inputs,[input.id]:{source:'facts',field:e.target.value}}})}/></label>)}
    </section>
    <label className="block text-xs text-slate-500">操作说明<Textarea className="mt-1" disabled={!editable} value={operation.summary ?? ""} onChange={(event) => onChange({ summary: event.target.value })} /></label>
    <div className="border-t pt-3"><div className="flex items-center justify-between"><h4 className="text-sm font-medium">资源需求</h4>{editable && <Button variant="outline" size="sm" onClick={() => onChange({ resources: [...(operation.resources ?? []), { kind: "labor", amount: "1", unit: "hour", basis:"time" }] })}><Plus className="size-3.5" /> 资源</Button>}</div>
      <p className="mt-1 text-xs text-slate-500">材料、部件、设备、能源、人工或场地。数量仅接受已注册单位；实际单价由测算场景提供。</p>
      <div className="mt-3 space-y-2">{(operation.resources ?? []).map((resource, index) => <div key={index} className="grid grid-cols-2 gap-2 rounded-lg bg-slate-50 p-2">
        <select aria-label="资源类型" className="h-9 rounded-md border border-slate-300 bg-white px-1 text-xs" disabled={!editable} value={resource.kind} onChange={(event) => changeResource(index, { kind: event.target.value as ResourceRequirement["kind"] })}>{resourceKinds.map((kind) => <option key={kind}>{kind}</option>)}</select>
        <Input aria-label="资源引用" placeholder="资源类别或实体引用" disabled={!editable} value={resource.ref ?? ""} onChange={(event) => changeResource(index, { ref: event.target.value || undefined })} />
        <Input aria-label="资源数量" placeholder="数量" disabled={!editable} value={resource.amount ?? ""} onChange={(event) => changeResource(index, { amount: event.target.value || undefined })} />
        <select aria-label="资源单位" className="h-9 rounded-md border border-slate-300 bg-white px-1 text-xs" disabled={!editable} value={resource.unit ?? ""} onChange={(event) => changeResource(index, { unit: event.target.value || undefined })}><option value="">单位</option>{units.map((unit) => <option key={unit} value={unit}>{unit}</option>)}</select>
        <div className="col-span-2 flex flex-wrap gap-1">{([['cycle','每次加工'],['piece','每件产出'],['time','按时长'],['batch','整批一次']]as const).map(([basis,label])=><button key={basis} disabled={!editable} className={`rounded border px-2 py-1 text-[10px] ${(resource.basis??'cycle')===basis?'border-indigo-400 bg-indigo-50 text-indigo-700':''}`} onClick={()=>changeResource(index,{basis,...(basis==='time'?{unit:'hour'}:{})})}>{label}</button>)}</div>
        <details className="col-span-2 text-xs"><summary className="cursor-pointer text-slate-500">资源数量模型（可选）</summary><select aria-label="资源数量模型" disabled={!editable} className="mt-2 h-9 w-full rounded border" value={resource.quantity_model_ref??''} onChange={e=>changeResource(index,{quantity_model_ref:e.target.value||undefined,quantity_inputs:{}})}><option value="">使用显式数量</option>{quantityModels.map(m=><option key={m.id} value={`${m.id}@${m.version}`}>{m.id} · {m.output_unit}</option>)}</select>{quantityModels.find(m=>`${m.id}@${m.version}`===resource.quantity_model_ref)?.inputs?.map(input=>{const binding=resource.quantity_inputs?.[input.id]??{source:'facts' as const,field:''};return <label key={input.id} className="mt-2 block">{input.id} · {input.unit}<div className="flex gap-1"><button disabled={!editable} className="text-indigo-600" onClick={()=>changeResource(index,{quantity_inputs:{...resource.quantity_inputs,[input.id]:{...binding,source:binding.source==='facts'?'process':binding.source==='process'?'item':'facts'}}})}>{binding.source==='process'?'加工结果':binding.source==='item'?'对象':'场景'}</button><Input aria-label={`数量输入 ${input.id}`} disabled={!editable} value={binding.field} placeholder={binding.source==='process'?'duration_seconds / cycles':'字段名'} onChange={e=>changeResource(index,{quantity_inputs:{...resource.quantity_inputs,[input.id]:{...binding,field:e.target.value}}})}/></div></label>;})}</details>
        {editable && <Button variant="ghost" size="icon-sm" aria-label="删除资源" onClick={() => onChange({ resources: (operation.resources ?? []).filter((_, item) => item !== index) })}><Trash2 className="size-4" /></Button>}
      </div>)}</div>
    </div>
  </div>;
}
