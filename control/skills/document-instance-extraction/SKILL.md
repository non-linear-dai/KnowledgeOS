---
name: document-instance-extraction
description: 按 KnowledgeOS 抽取契约，把合同、规格书、会议纪要、邮件、表格、扫描件、音视频转写等异构材料解析为带字段级证据的实例、关系和规则信号候选，并提交 KnowledgeOS 验收。用于从材料中发现或更新符合本体定义的实例；不用于问答、计算，也不用于直接修改知识库。
metadata:
  short-description: 契约驱动、证据可回溯的文档实例抽取
  protocol: knowledgeos.extraction.v2
---

# 异构文档实例抽取

把一份来源材料转换成符合 KnowledgeOS 本体定义的实例候选。目标是**尽可能多、尽可能准**：召回靠分阶段抽取和锚点补漏，精度靠逐字段证据和服务端验收。复制本 Skill 到其他 Agent 平台时，`contract.yaml` 必须随本文件一起带走。

## 分工与边界

- KnowledgeOS 负责定义“什么是对的”（本体、说明卡、术语、值模式、识别键、权威策略），也负责判定“是否接受”（锚点、证据、规范化、身份匹配、约束校验）。
- 本 Skill 只负责“从材料中做出来”：解析、定位、抽取、补漏、按验收报告修复。
- 不直接写知识库。所有结果都通过 `extraction_submission` 提交；`propose` 模式生成的也只是待审核的 ChangeSet。

## 必需能力

由适配层绑定以下抽象能力和 KnowledgeOS 操作（见 `contract.yaml`）：

- 能力：`document_parsing`、`structured_output_llm`、`deterministic_text_matching`；视材料需要 `ocr`、`speech_to_text`；推荐 `verification_llm`。
- KnowledgeOS 操作：`get_extraction_contract`、`register_source`、`precheck_submission`、`propose_submission`、`resolve`、`get`。

缺少某项必需能力时，停止并说明缺什么，不要用模型记忆或猜测代替。

## 工作流

### 1. 获取契约（contract）

用 `contract_request` 获取契约，`include` 中带上 `instructions`。已知文档类型时带上 `document_type`，否则按调用方给出的领域取子集。记住 `contract_id` 和 `registry_fingerprint`；后续任何一步收到 `CONTRACT_STALE`，都要重新获取契约并从第 4 步重来。

契约中的 `instructions` 是 KnowledgeOS 按本 Skill 的阶段渲染好的标准说明（Markdown），内容来自已发布的本体与抽取说明卡：

- 每个阶段的提示词使用 `instructions.sections` 中 `stage` 对应的段落：`all`（总要求，所有阶段都带上）、`route`、`mentions`、`consolidate`、`fill_fields`、`relations`、`rule_signals`、`precheck_and_repair`。
- 分批处理时，用 `instructions.concepts.<概念>.mentions / consolidate / fill_fields` 只放入本批涉及的概念。
- 不要自行改写这些段落或把结构化契约重新转写成提示词；需要确定性匹配（锚点、值模式、单位写法）时直接读结构化字段。
- 把 `instructions.content_hash` 和 `renderer_version` 记入运行清单（`run.stages[].parameters_hash` 或适配层自己的记录），便于复现。

### 2. 解析为文档中间表示（parse）

- 输出 `source_document`。每个块的 `text` 必须是解析器得到的原文，**不得改写、摘要、翻译或纠错**；OCR 结果按识别原样保留，并填写 `recognition_confidence`。
- 保留阅读顺序（`order`）和标题路径（`heading_path`）。表格拆成 `table` 容器块加 `table_cell` 块，填写行、列和是否表头；有页码、坐标就填写 `page`、`bbox`；音视频填写 `speaker` 和 `time`。
- `document.content_hash` 等于原件字节的 SHA-256。同一份材料的新版本沿用 `document.id`，更新 `version`，并填写 `previous_version`。
- 能从材料中读到发布日期、生效日期时，填写 `document_dates` 并附证据。发布方、审批状态等来源信息只取自来源系统元数据，不要由模型推断。

### 3. 登记来源（register）

用 `register_source` 提交 `source_document`。回执中如果带有 `diff`，后续只抽取新增或修改的块（加上必要的上下文块）；`facts_at_risk` 中列出的事实要重新核对，并在提交中给出现行证据，或在说明中注明已失效。

### 4. 判断文档类型（route）

先用契约中 `recipes[].match` 做确定性匹配；匹配不上再由 LLM 判断文档类型。判断结果与当前契约范围不一致时，按新的文档类型重新获取契约子集。

### 5. 锚点预抽取（anchor，纯确定性）

不调用 LLM，用以下内容在块文本中做匹配，得到“线索锚点”：

- `lexicon` 中已有实例的名称和编码；
- 概念和属性的 `terms`（首选名称和其他叫法）；
- `value_patterns` 和身份 `patterns`；
- `unit_aliases` 中的单位写法；
- `recipes` 的选择器（标题、表头、键值标签）。

线索锚点是后续阶段必须逐一处理的清单：每一条要么被某个实例或字段覆盖，要么在复核时给出“不构成实例”的理由。

### 6. 识别提及（mentions）

按章节（相同 `heading_path`）或单张表格分批，让 LLM 只做一件事：找出属于契约中概念的提及，输出概念 ID、原文片段和所在块。提示词中放入 `instructions` 的总要求与 `mentions` 段落（或本批概念的 `concepts.<概念>.mentions`），其中已包含说明卡的识别线索、正例、反例和边界说明。批次要小，不要把全文和全部概念一次交给模型。

### 7. 合并实例（consolidate）

- 把指向同一对象的提及合并为一个实例，包括“该公司”“上述设备”这类指代，全部记入 `mentions`。
- 为每个实例分配本次提交内唯一的 `local_id`（形如 `tmp:org-1`）。
- 身份线索只记录原文中出现的：业务编码填 `identity.key`；需要的话用 `resolve` 查询已有实例，把结果作为 `node_hint`。最终是否为同一实例由 KnowledgeOS 判定，不要自行断言。
- 属于默认命名空间时省略 `key_namespace`，不要填空字符串。

### 8. 填写字段（fill_fields）

对每个实例，逐个属性检索最相关的块（该实例的提及附近、抽取方案映射到的表格、属性术语命中的位置），再让 LLM 提取取值。提示词中放入该概念的 `concepts.<概念>.fill_fields` 段落。规则如下：

- `value.raw` 必须**逐字复制**自原文，并且包含在某个 `role=value` 的锚点引文中；单位原文放 `unit_raw`。
- **不做任何换算或规范化**：不换单位，不把“约 5 万元”改写成 50000，不推算相对日期。规范化由 KnowledgeOS 完成。可以用 `agent_hint` 给出自己的理解，但它不参与验收。
- 表格取值同时给出表头或字段名锚点（`role=label` 或 `header`）和取值锚点（`role=value`）。
- 时间只记录原文表达（如 `observed_at_raw: "2026年10月15日"`）并附证据；原文没有时间就省略，由 KnowledgeOS 按文档日期补齐。
- 原文没写的不填，不用常识或其他文档补全。发布必填字段缺失时，把实例标记为 `completeness: draft`，KnowledgeOS 会建草稿实例并进入补全队列。
- 属性必须是契约中绑定到该概念的属性。契约外的事实写入 `unmapped_facts`，并选择合适的 `reason`。
- `publication_route: upstream_source_system` 的属性照常抽取提交，KnowledgeOS 会把它登记为待核对事项，不会写入 Git 真源。

### 9. 抽取关系（relations）

只使用契约中登记的关系，并符合其端点类型。两端可以是本次的新实例（`local_id`），也可以是已有实例（`node_id`）。证据要能看出关系本身，例如“由 Acme 供应”，并用 `subject`、`object`、`context` 角色标注锚点；注意关系方向（“X 采购 Y”和“X 供应 Y”方向相反）。

### 10. 规则信号（rule_signals）

遇到规范性条款（“不得低于”“必须”“应当”“超过……则……”），或涉及契约 `rule_slots` 中属性的阈值、条件时，记录为 `rule_signals`：条款原文、主体概念、属性、算子原文、取值原文、适用范围原文；能对应到已有逻辑时填 `logic_ref_hint`。不要起草或修改逻辑资产本身。

### 11. 逐条核验（verify，可选）

用另一个模型或另一套提示词，对每个字段判断“证据是否支持该取值”，结论写入 `verification`。判为 `unsupported` 的字段在预检前删除，`uncertain` 的保留。核验结论只是参考，不能替代服务端验收。

### 12. 预检与修复（precheck_and_repair）

以 `mode: precheck` 提交，按验收报告修复，最多 3 轮：

| 问题代码 | 处理方式 |
|---|---|
| `CONTRACT_STALE` | 重新获取契约，从第 4 步重来 |
| `QUOTE_MISMATCH`、`ANCHOR_OUT_OF_RANGE`、`ANCHOR_BLOCK_UNKNOWN` | 用确定性代码按块文本重算偏移；不要让模型修偏移 |
| `QUOTE_NORMALIZED_MATCH` | 改为精确引文后重算偏移 |
| `VALUE_NOT_IN_EVIDENCE` | 重新提取该字段，`raw` 必须复制自引文 |
| `CONCEPT_UNKNOWN`、`PREDICATE_NOT_BOUND` | 改写为 `unmapped_facts` |
| `RELATION_ENDPOINT_INVALID` | 核对端点类型；无法满足就删除该关系 |
| `UNIT_NOT_ALLOWED`、`ENUM_NOT_ALLOWED`、`VALUE_UNPARSEABLE` | 核对 `raw` 和 `unit_raw` 是否原样复制；原文确实用了契约外的写法时，改写为 `unmapped_facts`（reason 选 `other`） |
| `REQUIRED_MISSING` | 在全文中定向查找该属性；仍然没有就标记为 `draft` |
| `IDENTITY_AMBIGUOUS`、结果为 `needs_resolution` | 原文中有编码或上下文实体时补充 `identity.key` 或 `identity.context`；否则保持原样，留给人工确认 |
| `CONNECTOR_AUTHORITY_REQUIRED`、`VALUE_CONFLICT` | 不处理，属于预期结果，由业务系统或审核人决定 |

报告中 `coverage.uncovered_cue_blocks` 列出的块，对相关概念和属性重新执行第 6 至第 8 步。确认确实不构成实例的（例如泛指的“测试台”，不是某台具体设备），可以不处理。不要为了消除警告而删除有证据的结果。

### 13. 提交提案（propose，可选）

只有在调用方要求、并且具备提案权限时执行。提交内容与最后一次通过预检的内容相同，`mode` 改为 `propose`，填写 `reason`。`submission_id` 是幂等键：重试时不要更换；内容有变化时必须更换。

## 偏移量

`start` 和 `end` 是块文本中的 Unicode 码点偏移，左闭右开，`quote` 必须等于 `text[start:end]`。JavaScript 等以 UTF-16 计长度的环境要注意：扩展区汉字、表情符号等会占两个 UTF-16 单元，需要换算。所有偏移都用代码计算，不要由模型生成。

## 运行清单

每次提交都填写 `run`：Agent 平台与版本、本 Skill 的 ID 与版本，以及解析器和每个阶段使用的模型、提示词哈希。这是结果可复现、可评测的前提。

## 禁止事项

- 不编造实例 ID；`node_hint` 只能来自契约 `lexicon` 或 `resolve` 的结果。
- 不把候选标记为已确认，不绕过 KnowledgeOS 直接写文件或数据库。
- 不改写原文，不用模型知识或其他材料补值，不跨文档引用证据。
- 不自行换算单位、币种、日期，也不自行判定来源的权威等级。
- 不因为模型“很确定”就省略证据；没有证据的内容不提交。

## 迁移到新的 Agent 平台

1. 本 Skill 随 KnowledgeOS 一起迁移，无需修改。
2. 在新平台实现适配层：把 `required_capabilities` 绑定到具体的解析器、OCR、模型和匹配工具；把 `knowledgeos_operations` 绑定到 KnowledgeOS 端点，使用 `agent` 角色的凭证。
3. 实现确定性的偏移计算和 SHA-256 计算工具。
4. 用 `contract.yaml` 中 `evaluation.gold_set` 指向的标注集跑评测，各项指标达标后再开启 `propose`。
