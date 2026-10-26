# 抽取协议 v2：KnowledgeOS 与抽取 Agent 的对接基线

状态：草案（baseline draft），与 v1 并行，不替换现有 `/v1/extraction/*`。

- Schema：[`control/schemas/extraction-v2.schema.json`](../control/schemas/extraction-v2.schema.json)
- 参考 Skill：[`control/skills/document-instance-extraction/`](../control/skills/document-instance-extraction/)
- 示例：[`docs/examples/extraction-v2/`](examples/extraction-v2/)

## 1. 定位

KnowledgeOS 负责**契约、验收、沉淀**，Agent 负责**解析与抽取执行**。凡是迁移后必须保持一致的东西（本体、说明卡、术语、值模式、身份规则、规范化、权威策略、验收规则、证据、评测集），都留在 KnowledgeOS；凡是依赖具体模型、解析器、平台的东西（OCR、版面解析、提示词、模型选择、编排、缓存），都放在 Agent。

验收只在 KnowledgeOS 服务端执行。Agent 的自检和核验结论只作参考，不能作为放行依据。

## 2. 消息与流程

```text
Agent                                         KnowledgeOS
  │ contract_request ───────────────────────────▶ │  按领域/概念/文档类型生成契约子集
  │ ◀─────────────────────────── extraction_contract │  含 registry_fingerprint
  │ （解析材料，生成块结构）                          │
  │ source_document ────────────────────────────▶ │  校验块结构，原件与快照按哈希入证据库
  │ ◀─────────────────────────────── source_receipt │  含与上一版本的块级差异
  │ （锚点预抽取 → 提及 → 合并 → 字段 → 关系 → 规则信号）│
  │ extraction_submission(mode=precheck) ───────▶ │  全量验收，不写入
  │ ◀──────────────────────────── validation_report │  逐字段结果 + 覆盖率
  │ （按报告修复、补抽，最多数轮）                     │
  │ extraction_submission(mode=propose) ────────▶ │  同一规则验收，生成 ChangeSet
  │ ◀──────────────────────────── validation_report │  附 changesets
```

一次提交只对应一份来源文档的一个版本。

## 3. 建议端点与权限

| 端点 | 请求 → 响应 | 权限 |
|---|---|---|
| `POST /v2/extraction/contract` | `contract_request` → `extraction_contract` | read |
| `POST /v2/extraction/sources` | `source_document` → `source_receipt` | extract |
| `POST /v2/extraction/precheck` | `extraction_submission`（precheck）→ `validation_report` | extract |
| `POST /v2/extraction/submissions` | `extraction_submission`（propose）→ `validation_report` | propose |

提案人身份取自 HTTP 认证主体，不取自消息内容（沿用现有约定）。

## 4. 服务端验收规则（Schema 之外）

JSON Schema 只约束结构。以下语义规则由 KnowledgeOS 执行，并以 `Issue.code` 报告。

**契约与来源**

1. `registry_fingerprint` 必须等于当前注册表指纹，否则整份拒绝（`CONTRACT_STALE`）。
2. 提交引用的来源必须已登记，`content_hash` 一致（`SOURCE_UNKNOWN`、`SOURCE_HASH_MISMATCH`）。
3. 块 ID 唯一；`parent_id` 存在；`table_cell` 的 `table.table_id` 指向一个 `table` 块；`order` 不重复（`BLOCK_INVALID`）。超过契约 `limits` 时报 `LIMIT_EXCEEDED`。

**锚点与证据**

4. 锚点的块存在，`0 ≤ start < end ≤ len(text)`，并且 `quote == text[start:end]`，偏移按 Unicode 码点计算（`ANCHOR_BLOCK_UNKNOWN`、`ANCHOR_OUT_OF_RANGE`、`QUOTE_MISMATCH`）。契约 `acceptance.quote_match` 为 `normalized_exact` 时，NFKC 加空白折叠后能匹配的锚点也可接受，但报 `QUOTE_NORMALIZED_MATCH` 警告，并以服务端重算的偏移保存。
5. 每个字段、关系、规则信号都必须有自己的证据。`value.raw` 必须包含在某个 `role=value` 的引文中；没有标注角色的锚点视为 `value`（`VALUE_NOT_IN_EVIDENCE`）。

**结构与引用**

6. `local_id` 在一次提交内唯一（`DUPLICATE_LOCAL_ID`）；`EntityRef.local_id` 必须指向本次提交中的实例（`UNKNOWN_LOCAL_REF`）。
7. 概念存在、属性绑定到该概念（`CONCEPT_UNKNOWN`、`PREDICATE_NOT_BOUND`）。关系端点按身份解析后的实际类型校验（`RELATION_ENDPOINT_INVALID`）；两端都是新实例时，关系随实例一起进入提案。

**取值规范化（只由 KnowledgeOS 完成）**

8. 按属性定义规范化 `raw`：单位经 `unit_aliases` 映射到注册单位后换算；处理中文数量表达（万、亿、约、不低于、区间、±公差、百分比）；“约”“上限”“下限”等写入 `qualifiers`。失败时报 `VALUE_UNPARSEABLE`、`UNIT_NOT_ALLOWED`、`ENUM_NOT_ALLOWED`。`agent_hint` 与服务端结果不一致时只报 `AGENT_HINT_MISMATCH` 警告。
9. 时间原文按 `document_dates` 换算相对日期；没有时间原文时，依次用文档生效日期、发布日期、`captured_at` 补齐。

**身份、完整性与权威**

10. 身份沿用现有的证据约束解析器（`entity_resolution.py`），并增加契约中的身份键和编码模式。`node_hint` 必须出现在候选中才生效，否则报 `IDENTITY_NOT_IN_EVIDENCE` 警告。存在歧义时结果为 `needs_resolution`（`IDENTITY_AMBIGUOUS`）。任何情况下都不自动合并。
11. 缺少发布必填字段时：契约允许草稿则结果为 `draft`，报 `REQUIRED_MISSING` 警告并进入补全队列；否则拒绝。
12. `publication_route: upstream_source_system` 的字段结果为 `upstream_required`，登记到待核对队列，不写入 Git 真源（`CONNECTOR_AUTHORITY_REQUIRED`）。来源的权威等级由 `document.origin` 中可回查的系统元数据决定；只有 Agent 声明的 `document_type`、`issuer` 不会提升权威等级（`AUTHORITY_UNVERIFIED`）。
13. 单值属性与现有值不同时，结果为 `conflict`，按属性的权威冲突策略处理（`VALUE_CONFLICT`）。
14. 合并后的候选要执行已启用的业务约束（`CONSTRAINT_VIOLATION`）。
15. 低于 `acceptance.min_confidence` 的项报 `CONFIDENCE_BELOW_THRESHOLD`，不进入提案。

**整体状态**：契约过期、来源不符或没有任何可接受项时为 `rejected`；全部项无错误时为 `accepted`；其余为 `partially_accepted`。`precheck` 与 `propose` 使用完全相同的规则。

## 5. 覆盖率口径

线索包括：契约术语、值模式、单位写法、实体词典、抽取方案选择器在块文本中的命中。实例、字段、关系锚点与线索位置有重叠，即视为“已覆盖”；规则信号的锚点不计入。`title`、`heading` 块中的概念名称默认不作为线索。未覆盖的线索按块列在 `coverage.uncovered_cue_blocks`。

## 6. 规则信号比对口径

按（主体概念，属性）查找已发布逻辑，比对规范化后的算子、阈值、单位和适用范围，结果为 `matches_existing`、`differs_from_existing` 或 `no_existing_logic`。比对不会修改任何逻辑资产。存在差异时生成维护项，由规则与决策工作台按现有版本流程处理。

## 7. 来源版本与增量

新版本登记时，KnowledgeOS 按块文本哈希、`heading_path` 和顺序与上一版本对齐，得出新增、删除、修改的块。块 ID 不要求跨版本稳定。证据锚点落在被删除或修改块上的已有事实，列入 `facts_at_risk`，并进入复核队列。

## 8. 与 v1 的关系

| v1 | v2 |
|---|---|
| 按 4,000 字固定切分的 `segments` | Agent 提交的结构化 `blocks`（含页码、坐标、表格单元格、时间） |
| 证据 `{segment_id, quote}`，以子串判定 | 锚点 `{block_id, start, end, quote, role}`，精确比对 |
| 实体级证据 | 字段级证据 |
| `attrs` 与 `assertions` 由 Agent 区分 | 统一为 `fields`，存储方式由 KnowledgeOS 按属性定义决定 |
| Agent 提交规范化后的值 | Agent 只提交原文，规范化由 KnowledgeOS 完成 |
| 关系目标必须是已有实体 | 两端都可以是新实例（`local_id`） |
| `existing_id` | `node_hint`（仅为建议） |
| 无运行清单 | `run`：Agent、Skill、解析器、各阶段模型与提示词版本 |
| 无 | `rule_signals`、覆盖率报告、来源版本差异 |

v1 端点保持可用。待现有 Agent 迁移完成后，再评估是否弃用 v1。

## 9. KnowledgeOS 侧待实现清单

1. **契约生成器**：从注册表生成 `extraction_contract`。需要新增的控制资产包括抽取说明卡、属性术语、值模式、身份键和抽取方案（建议放在 `control/extraction/` 下，并在白板中提供编辑入口），全部计入注册表指纹。
2. **持久化**：在 `knowledge.state.db` 中增加来源文档（版本、哈希、块快照）、抽取运行清单、提交与验收报告、草稿实例和待核对队列；证据原件按内容哈希存储，与 state 和 ledger 一起备份。
3. **规范化器**：中文数量、单位别名、约数与区间、相对日期。
4. **验收器**：实现第 4 节的规则，复用现有的 `EntityResolver`、`validate_value`、SHACL 和业务约束执行。
5. **端点、CLI 与权限**：见第 3 节。
6. **测试**：本分支新增的 Schema 会让 Studio 定义目录多一项，`pytests/test_backend.py` 中的计数已由 91 改为 92。

## 10. 示例

`docs/examples/extraction-v2/` 中是同一份“供应商季度评审纪要”的完整往返示例：契约请求、契约、文档中间表示、登记回执、预检提交、验收报告。所有锚点偏移都按块文本计算，可直接作为两边的联调样例。示例演示了以下结果：

- 已有实例匹配；
- 字段与现有值一致（`already_present`）；
- 单值属性冲突（`conflict`）；
- 交期由 ERP 维护（`upstream_required`）；
- 规则阈值与现有约束不一致（`differs_from_existing`）；
- 未映射事实；
- 覆盖率线索。

## 11. 待定事项

- 草稿实例在什么条件下允许发布，是否需要独立的补全审核。
- 证据库的存储后端：本地内容寻址目录还是对象存储。
- 实体词典较大时的分页和增量同步方式。
- 是否允许 KnowledgeOS 基于原件独立重新解析，以抽查 Agent 提交的块结构。
