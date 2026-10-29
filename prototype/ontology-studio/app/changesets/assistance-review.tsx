"use client";

import { useEffect, useState } from "react";
import { Sparkles } from "lucide-react";
import { loadAuthoringFeedback, percent, taskLabels, type AuthoringFeedback, type AuthoringTask } from "../assistant/assistant-api";
import type { DeclaredAssistance } from "./changesets-api";

/** What a reviewer needs to know about an assistant-written draft. */
export function AssistanceSection({ assistance }: { assistance: DeclaredAssistance }) {
  const modified = assistance.modified_after_suggestion;
  return <section className="change-section assistance-review"><h3><Sparkles size={15} aria-hidden="true" />助手起草</h3>
    <p className="change-section-note">{modified ? "作者在采纳助手草稿后做过修改（例如确认了回归案例或调整了规则）。" : "提交内容与助手草稿一致，请特别留意业务数值与边界的依据。"}这是作者的来源声明，不代表模型输出已被验证；确定性校验与案例结果以上方内容为准。</p>
    <dl className="property-list">
      <div><dt>任务</dt><dd>{taskLabels[assistance.task as AuthoringTask] ?? assistance.task}</dd></div>
      <div><dt>模型</dt><dd>{assistance.provider} / {assistance.model}{assistance.provider === "demo" ? "（演示样例）" : ""}</dd></div>
      {assistance.items_total != null && <div><dt>采纳项</dt><dd>{assistance.items_accepted ?? 0} / {assistance.items_total}</dd></div>}
      {Boolean(assistance.repair_rounds) && <div><dt>自动修正</dt><dd>{assistance.repair_rounds} 轮</dd></div>}
      <div><dt>标准说明</dt><dd><code>{assistance.instructions_hash.slice(0, 12)}</code>{assistance.renderer_version ? ` · 渲染器 ${assistance.renderer_version}` : ""}</dd></div>
    </dl>
  </section>;
}

/** Acceptance of assistant drafts by task, from proposals that declared assistant origin. */
export function AssistantMetrics({ active, revision }: { active: boolean; revision: number }) {
  const [feedback, setFeedback] = useState<AuthoringFeedback | null>(null);
  useEffect(() => {
    if (!active) return;
    const controller = new AbortController();
    void loadAuthoringFeedback(controller.signal).then(value => { if (!controller.signal.aborted) setFeedback(value); }).catch(() => { /* Metrics are optional. */ });
    return () => controller.abort();
  }, [active, revision]);
  if (!feedback?.tasks.length) return null;
  return <section className="assistant-metrics" aria-label="助手起草的采纳情况"><h3><Sparkles size={14} aria-hidden="true" />助手起草的采纳情况</h3>
    <table className="extraction-table"><thead><tr><th>任务</th><th>提交</th><th>审核通过率</th><th>作者修改率</th><th>采纳项比例</th></tr></thead>
      <tbody>{feedback.tasks.map(item => <tr key={item.task}><td>{taskLabels[item.task] ?? item.label}</td><td>{item.submitted}</td><td>{percent(item.approval_rate)}</td><td>{percent(item.modification_rate)}</td><td>{percent(item.item_acceptance_rate)}</td></tr>)}</tbody></table>
    <p>只统计声明了助手来源的提案；被放弃的草稿不会进入 KnowledgeOS。</p>
  </section>;
}
