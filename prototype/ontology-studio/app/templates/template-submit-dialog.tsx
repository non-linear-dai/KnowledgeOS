"use client";

import { useEffect, useState } from "react";
import { Check, CircleAlert, Loader2, RefreshCw, Save } from "lucide-react";
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription, DialogFooter } from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { previewTemplate, type ExpertTemplate } from "./template-api";
import { checkTemplateCases, templateCasesPass, type TemplateCaseCheck } from "./template-case-checks";

type Props = {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  template: ExpertTemplate;
  editable: boolean;
  busy: boolean;
  error: string;
  reason: string;
  onReasonChange: (reason: string) => void;
  semanticChanges: string[];
  onExpectedChange: (id: string, expected: number) => void;
  onEditCase: (id: string) => void;
  onSubmit: (validatedTemplate: ExpertTemplate) => Promise<void>;
};

export function TemplateSubmitDialog(props: Props) {
  const { open, template, editable, busy } = props;
  const [validation, setValidation] = useState<{ template: ExpertTemplate; checks: TemplateCaseCheck[] } | null>(null);
  const [retry, setRetry] = useState(0);
  useEffect(() => {
    if (!open) return;
    const controller = new AbortController();
    // Clear obsolete feedback before starting a new abortable case check.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setValidation(null);
    void checkTemplateCases(template, previewTemplate, controller.signal).then(checks => {
      if (!controller.signal.aborted) setValidation({ template, checks });
    }).catch(() => { /* Closing or editing cancels the obsolete check. */ });
    return () => controller.abort();
  }, [open, template, retry]);
  const checks = validation?.template === template ? validation.checks : null;
  const passed = checks !== null && templateCasesPass(checks);
  const canSubmit = editable && !busy && passed && Boolean(props.reason.trim()) && template.steps.length > 0;

  return <Dialog open={open} onOpenChange={props.onOpenChange}>
    <DialogContent className="max-h-[85dvh] overflow-y-auto">
      <DialogHeader><DialogTitle>提交模板变更</DialogTitle><DialogDescription>先核验所有案例，提交后进入统一变更审核工作区。</DialogDescription></DialogHeader>
      <p className="text-sm">{template.label} · v{template.version} · {template.steps.length} 道工序 · {template.cases.length} 个案例</p>
      {props.semanticChanges.length > 0 && <ul className="list-inside list-disc text-sm text-muted-foreground">{props.semanticChanges.map(item => <li key={item}>{item}</li>)}</ul>}
      <section aria-label="提交前案例核验" className="space-y-3 rounded-xl border border-border p-4">
        <div className="flex items-center gap-2 text-sm font-medium">
          {checks === null ? <Loader2 className="size-4 animate-spin text-primary" /> : passed ? <Check className="size-4 text-primary" /> : <CircleAlert className="size-4 text-muted-foreground" />}
          <span role="status">{checks === null ? "正在核验所有案例…" : passed ? `全部 ${checks.length} 个案例核验通过` : "案例尚未通过核验"}</span>
          <Button size="icon-sm" variant="ghost" className="ml-auto" aria-label="重新核验案例" disabled={busy || checks === null} onClick={() => setRetry(value => value + 1)}><RefreshCw /></Button>
        </div>
        <p className="text-xs leading-5 text-muted-foreground">工序数按案例的条件与对象集合展开计算，包含重复执行的次数。数量不一致时，请核对编排，或确认新的预期。</p>
        {checks?.map(check => <div key={check.id} className="space-y-2 border-t border-border pt-3">
          <div className="flex items-center justify-between gap-3 text-sm"><span className="min-w-0 break-all font-medium">{check.id}</span><span className={check.passed ? "shrink-0 text-primary" : "shrink-0 text-muted-foreground"}>{check.passed ? "通过" : check.error ? "展开失败" : "数量不一致"}</span></div>
          <p className="text-sm">预期 {check.expected} 道 · {check.actual === null ? "未能完成展开" : `实际展开 ${check.actual} 道`}</p>
          {check.error && <p role="alert" className="break-words text-xs text-destructive">{check.error}</p>}
          {!check.passed && <div className="flex flex-wrap gap-2">
            {check.actual !== null && <Button size="sm" variant="outline" aria-label={`确认 ${check.id} 预期为 ${check.actual} 道工序`} disabled={!editable || busy} onClick={() => props.onExpectedChange(check.id, check.actual!)}>以展开数更新预期</Button>}
            <Button size="sm" variant="ghost" disabled={busy} onClick={() => props.onEditCase(check.id)}>编辑案例</Button>
          </div>}
        </div>)}
        {checks?.length === 0 && <p className="text-sm text-muted-foreground">请先添加至少一个样本案例。</p>}
      </section>
      {template.steps.length === 0 && <p role="alert" className="text-sm text-destructive">请先在白板中添加工序。</p>}
      <label className="change-field">提交说明<Textarea aria-label="模板提交说明" value={props.reason} disabled={busy} onChange={event => props.onReasonChange(event.target.value)} /></label>
      {props.error && <p role="alert" className="text-sm text-destructive">{props.error}</p>}
      <DialogFooter><Button variant="outline" disabled={busy} onClick={() => props.onOpenChange(false)}>返回编辑</Button><Button disabled={!canSubmit} onClick={() => { if (canSubmit) void props.onSubmit(template); }}>{busy ? <Loader2 className="animate-spin" /> : <Save />}提交变更</Button></DialogFooter>
    </DialogContent>
  </Dialog>;
}
