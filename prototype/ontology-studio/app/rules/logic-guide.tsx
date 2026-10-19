"use client";

import { BookOpen, ArrowRight, FlaskConical, Plus } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { logicKindLabels, pretty, type LogicAsset, type LogicKind } from "./logic-api";
import { businessLogicGuides } from "./logic-business-guide";
import { BusinessData } from "./business-values";
import { guideExample, logicGuides, type GuideRegistry } from "./logic-guides";

const kinds = Object.keys(logicKindLabels) as LogicKind[];

export function LogicTypeSummary({ kind, onRead }: { kind: LogicKind; onRead: () => void }) {
  const guide = logicGuides[kind];
  return <aside className="logic-type-help" aria-label={`${logicKindLabels[kind]}定义说明`}><div><span><BookOpen size={15} />{guide.question}</span><p>{guide.definition}</p><small>{guide.difference}</small></div><Button size="sm" variant="ghost" onClick={onRead}>维护说明与样例<ArrowRight size={14} /></Button></aside>;
}

export function LogicGuideContent({ kind, assets, definitions, canCreate, onCreate, expert = false }: { expert?: boolean; kind: LogicKind; assets: LogicAsset[]; definitions: GuideRegistry; canCreate: boolean; onCreate: () => void }) {
  const guide = (expert ? logicGuides : businessLogicGuides)[kind], example = guideExample(kind, assets, definitions);
  return <article className="logic-guide-content" aria-label={`${logicKindLabels[kind]}维护说明与样例`}>
    <div className="logic-guide-intro"><span className="neutral-badge">{logicKindLabels[kind]}</span><h3>{guide.question}</h3><p>{guide.definition}</p></div>
    <dl className="logic-guide-io"><div><dt>什么时候使用</dt><dd>{guide.choose}</dd></div><div><dt>和其他类型的区别</dt><dd>{guide.difference}</dd></div><div><dt>输入什么</dt><dd>{guide.input}</dd></div><div><dt>得到什么</dt><dd>{guide.output}</dd></div></dl>
    <section><h4>定义字段怎么填</h4><div className="logic-guide-table"><table><thead><tr><th>配置项</th><th>含义</th><th>{expert ? "配置示例" : "业务示例"}</th></tr></thead><tbody>{guide.fields.map(field => <tr key={field.name}><th scope="row">{field.name}</th><td>{field.meaning}</td><td>{expert ? <code>{field.example}</code> : field.example}</td></tr>)}</tbody></table></div></section>
    <section><h4>维护步骤</h4><ol className="logic-guide-steps">{guide.maintenance.map((step, index) => <li key={step}><span>{index + 1}</span><p>{step}</p></li>)}</ol></section>
    <section className="logic-guide-story"><span><FlaskConical size={17} />业务示例</span><h4>{example.definition.label}</h4><p>{guide.scenario}</p><p>{guide.result}</p><div className="logic-guide-example-actions"><Button size="sm" variant="outline" disabled={!canCreate || example.missing.length > 0} onClick={onCreate}><Plus />用此示例新建草稿</Button><small>另建会话草稿，保留当前内容；不会自动提交或发布。</small></div>{example.missing.length > 0 && <p className="logic-guide-prerequisites">创建本例前需要：{example.missing.join("、")}。说明和示意配置仍可查看。</p>}{!canCreate && <p className="logic-note">登录并取得对应作者权限后，可创建教学草稿。</p>}</section>
    <section><h4>输入与预期结果</h4><p className="logic-note">以下是本例第一个案例。先填写业务条件，再独立填写需要核对的预期结果。</p><div className="logic-guide-code-grid"><div><strong>试算输入</strong>{expert ? <pre>{pretty(example.input)}</pre> : <BusinessData value={example.input} />}</div><div><strong>案例预期</strong>{expert ? <pre>{pretty(example.expected)}</pre> : <BusinessData value={example.expected} />}</div></div>{expert && <details className="logic-guide-cases"><summary>查看本例全部 {example.definition.test_cases!.length} 个案例及完整配置</summary><pre>{pretty(example.definition)}</pre></details>}</section>
    <section><h4>维护时重点检查</h4><ul className="logic-guide-checks">{guide.checks.map(check => <li key={check}>{check}</li>)}</ul></section>
    <section className="logic-guide-common"><h4>提交、发布与关联</h4><p>维护定义和案例 → 验证全部 → 提交审核 → 独立身份审核 → 应用真源 → 验证发布。修改已有资产时先创建新版本，在“关联与使用”检查引用方并明确升级引用。</p><p>案例通过表示“实际结果符合预期”。例如预期不合法、实际也不合法的约束案例仍应通过；发布前还需评估现有实例能否满足启用后的约束。</p><p>匹配规则、约束和公式维护当前真源定义并递增版本；决策表和决策模型另建不可覆盖的版本。记录运行保存快照，回放使用原输入与原定义。实际企业事实通过其权威来源维护。</p></section>
  </article>;
}

export function LogicGuideDialog({ open, onOpenChange, kind, onKindChange, assets, definitions, canCreate, onCreate, expert = false }: { expert?: boolean; open: boolean; onOpenChange: (open: boolean) => void; kind: LogicKind; onKindChange: (kind: LogicKind) => void; assets: LogicAsset[]; definitions: GuideRegistry; canCreate: boolean; onCreate: () => void }) {
  return <Dialog open={open} onOpenChange={onOpenChange}><DialogContent className="logic-guide-dialog sm:max-w-4xl"><DialogHeader><DialogTitle>规则与决策工作台说明</DialogTitle><DialogDescription>先判断要解决的问题，再选择逻辑类型；每类均有维护步骤和可试算样例。</DialogDescription></DialogHeader><div className="logic-guide-scroll"><section className="logic-guide-comparison"><h3>五类业务逻辑怎么选</h3><div className="logic-guide-table"><table><thead><tr><th>类型</th><th>要回答的问题</th><th>主要输出</th></tr></thead><tbody>{kinds.map(value => <tr key={value} data-current={kind === value}><th scope="row"><button aria-pressed={kind === value} onClick={() => onKindChange(value)}>{logicKindLabels[value]}</button></th><td>{logicGuides[value].question}</td><td>{(expert ? logicGuides : businessLogicGuides)[value].output}</td></tr>)}</tbody></table></div></section><nav className="logic-guide-type-tabs" aria-label="选择说明类型">{kinds.map(value => <button key={value} aria-pressed={kind === value} onClick={() => onKindChange(value)}>{logicKindLabels[value]}</button>)}</nav><LogicGuideContent expert={expert} key={kind} kind={kind} assets={assets} definitions={definitions} canCreate={canCreate} onCreate={onCreate} /></div></DialogContent></Dialog>;
}
