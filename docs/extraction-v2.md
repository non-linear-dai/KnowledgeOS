# 抽取协议 v2：KnowledgeOS 与抽取 Agent 的对接基线

状态：服务端已实现（契约生成、来源登记、规范化、验收、提案与待办队列），与 v1 并行，不替换现有 `/v1/extraction/*`。

- Schema：[`control/schemas/extraction-v2.schema.json`](../control/schemas/extraction-v2.schema.json)
- 参考 Skill：[`control/skills/document-instance-extraction/`](../control/skills/document-instance-extraction/)
- 示例：[`docs/examples/extraction-v2/`](examples/extraction-v2/)
- 服务端控制资产：[`control/extraction/v2/`](../control/extraction/v2/)（配置、抽取说明卡、抽取方案）
- 实现：`src/knowledge_os/extraction_v2.py`（契约、来源、验收、提案）、`src/knowledge_os/normalization.py`（规范化）

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

## 3. 端点与权限

| 端点 | 请求 → 响应 | 权限 |
|---|---|---|
| `POST /v2/extraction/contract` | `contract_request` → `extraction_contract` | read |
| `POST /v2/extraction/sources` | `source_document` → `source_receipt` | extract |
| `POST /v2/extraction/precheck` | `extraction_submission`（precheck）→ `validation_report` | extract |
| `POST /v2/extraction/submissions` | `extraction_submission`（propose）→ `validation_report` | propose |
| `GET /v2/extraction/queue?kind=&status=` | 待核对、草稿、规则差异、未映射事实队列 | read |
| `GET /v2/extraction/source?id=&version=` | 已登记的来源快照、回执与版本列表 | read |

响应沿用 KnowledgeOS 的统一信封，`data` 就是协议消息本身。`sources` 请求体上限 16 MiB（含内联原件），`precheck` 与 `submissions` 上限 8 MiB，其余 1 MiB。生成 ChangeSet 的 `submissions` 返回 201。

CLI 对应命令：`extract-v2-contract --request`、`extract-v2-register --source-document --actor`、`extract-v2-precheck --submission`、`extract-v2-submit --submission --actor`、`extract-v2-queue`。

提案人身份取自 HTTP 认证主体，不取自消息内容（沿用现有约定）。

## 4. 服务端验收规则（Schema 之外）

JSON Schema 只约束结构。以下语义规则由 KnowledgeOS 执行，并以 `Issue.code` 报告。

**契约与来源**

1. `registry_fingerprint` 必须等于当前注册表指纹，否则整份拒绝（`CONTRACT_STALE`）。
2. 提交引用的来源必须已登记，`content_hash` 一致（`SOURCE_UNKNOWN`、`SOURCE_HASH_MISMATCH`）。
3. 块 ID 唯一；`parent_id` 存在；`table_cell` 的 `table.table_id` 指向一个 `table` 块；`order` 不重复（`BLOCK_INVALID`）。超过契约 `limits` 时报 `LIMIT_EXCEEDED`。

**锚点与证据**

4. 锚点的块存在，`0 ≤ start < end ≤ len(text)`，并且 `quote == text[start:end]`，偏移按 Unicode 码点计算（`ANCHOR_BLOCK_UNKNOWN`、`ANCHOR_OUT_OF_RANGE`、`QUOTE_MISMATCH`）。契约 `acceptance.quote_match` 为 `normalized_exact` 时，NFKC 加空白折叠后能匹配、且匹配位置与声明的 `[start, end)` 有重叠的锚点也可接受，报 `QUOTE_NORMALIZED_MATCH` 警告，并以服务端重算的偏移保存；规范化只修正偏移，不会把锚点挪到块内其他位置。
5. 每个字段、关系、规则信号都必须有自己的证据。`value.raw` 必须包含在某个 `role=value` 的引文中；没有标注角色的锚点视为 `value`（`VALUE_NOT_IN_EVIDENCE`）。

**结构与引用**

6. `local_id` 在一次提交内唯一（`DUPLICATE_LOCAL_ID`）；`EntityRef.local_id` 必须指向本次提交中的实例（`UNKNOWN_LOCAL_REF`）。
7. 概念存在、属性绑定到该概念（`CONCEPT_UNKNOWN`、`PREDICATE_NOT_BOUND`）。关系端点按身份解析后的实际类型校验（`RELATION_ENDPOINT_INVALID`）；两端都是新实例时，关系随实例一起进入提案。

**取值规范化（只由 KnowledgeOS 完成）**

8. 按属性定义规范化 `raw`：单位经 `unit_aliases` 映射到注册单位后换算；处理中文数量表达（万、亿、约、不低于、区间、±公差、百分比）；“约”“上限”“下限”等写入 `qualifiers`。失败时报 `VALUE_UNPARSEABLE`、`UNIT_NOT_ALLOWED`、`ENUM_NOT_ALLOWED`。`agent_hint` 与服务端结果不一致时只报 `AGENT_HINT_MISMATCH` 警告。
9. 时间原文按 `document_dates` 换算相对日期（参考日依次取生效日期、发布日期、`captured_at`）；没有时间原文时，`observed_at` 依次用文档生效日期、发布日期、`captured_at` 补齐。只有日期的原文按配置 `default_utc_offset` 解释；`valid_to` 取所写期间的结束（不含），如“2026年三季度”到 2026-10-01。

**身份、完整性与权威**

10. 身份沿用现有的证据约束解析器（`entity_resolution.py`），并增加契约中的身份键和编码模式。`node_hint` 必须出现在候选中才生效，否则报 `IDENTITY_NOT_IN_EVIDENCE` 警告。存在歧义时结果为 `needs_resolution`（`IDENTITY_AMBIGUOUS`）。任何情况下都不自动合并。
11. 缺少发布必填字段时：契约允许草稿则结果为 `draft`，报 `REQUIRED_MISSING` 警告并进入补全队列（`extraction_queue_item` 的 `draft_instance`），不生成 ChangeSet；否则拒绝。说明卡 `identity.minimum_fields` 缺失的新实例直接拒绝。
12. `publication_route: upstream_source_system` 的字段结果为 `upstream_required`，登记到待核对队列，不写入 Git 真源（`CONNECTOR_AUTHORITY_REQUIRED`）。由连接器维护的已有实例整体为 `upstream_required`。来源的权威等级只由 `document.origin.system` 经配置 `source_authority` 映射得到；缺少 `origin` 时报 `AUTHORITY_UNVERIFIED` 警告，Tier A 属性的来源等级不在其权威策略中时报 `AUTHORITY_UNVERIFIED` 提示。Agent 声明的 `document_type`、`issuer` 不会提升权威等级。
13. 单值属性（或 `cardinality: one` 的断言）与现有值不同时，结果为 `conflict`（`VALUE_CONFLICT`）。冲突策略为 `prefer_primary` 时不进入提案；其他策略进入提案，所在 ChangeSet 升为高风险，须由独立审核人确认替换。可多值、有时态的断言与现有值不同时直接作为新断言提案；值与限定条件都相同则为 `already_present`。
14. 合并后的候选要执行已启用的业务约束（`CONSTRAINT_VIOLATION`）：先以抽取值试算约束，`invalid` 的字段被拒绝；再按发布时相同的编译校验检查整份候选文档，约束结果为 `unknown`（缺少所需事实）的新实例同样不能建立。
15. 低于 `acceptance.min_confidence` 的项报 `CONFIDENCE_BELOW_THRESHOLD`，不进入提案。

**整体状态**：契约过期、来源不符或没有任何可接受项时为 `rejected`；全部项无错误时为 `accepted`（警告与提示不影响）；其余为 `partially_accepted`。`precheck` 与 `propose` 使用完全相同的规则，包括对候选文档执行与发布时相同的编译校验；`precheck` 不写入任何状态。

## 5. 覆盖率口径

线索包括：契约术语、值模式、单位写法（紧跟数字的非 ASCII 单位）、实体词典、抽取方案选择器在块文本中的命中。实例、字段、关系锚点与线索位置有重叠，即视为“已覆盖”；概念名称线索在同一块中已有该概念实例的锚点、属性术语线索在同一块中已有该属性字段的锚点，也视为已覆盖。规则信号、未映射事实的锚点不计入覆盖，但计入 `blocks_with_evidence`。被更长术语包含的术语线索不重复列出。`title`、`heading` 块中的概念名称默认不作为线索。抽取方案选择器中，`table_header` 匹配表头单元格和首列行标签，`key_value_label` 匹配“标签：值”中冒号前的标签。未覆盖的线索按块列在 `coverage.uncovered_cue_blocks`。

## 6. 规则信号比对口径

先按 `logic_ref_hint`，否则按（主体概念，属性）查找业务约束与匹配规则（启用版本优先，其次草稿），比对规范化后的算子、阈值（按注册单位换算到同一基准比较）、单位和适用范围，结果为 `matches_existing`、`differs_from_existing` 或 `no_existing_logic`。算子来自 `operator_raw`，缺省时取取值中的上下限表达（如“不低于”）。比对不会修改任何逻辑资产。存在差异或尚无逻辑时生成维护项（`rule_signal` 队列项与复核项），由规则与决策工作台按现有版本流程处理。

## 7. 来源版本与增量

新版本登记时，KnowledgeOS 按块文本哈希、`heading_path`、块类型和顺序与上一版本（`previous_version`，省略时取最近登记的版本）对齐，得出新增、删除、修改的块；修改块先按相同 ID、再按相同标题路径、父块和表格位置配对。块 ID 不要求跨版本稳定。证据锚点落在被删除或修改块上、且经 `propose` 进入提案的事实，列入 `facts_at_risk`，并进入复核队列（`extraction_fact_at_risk`）。同一版本重复登记相同内容是幂等的；内容不同则拒绝，须登记新版本。

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

## 9. 服务端实现

**控制资产**（`control/extraction/v2/`，全部属于 Git 真源，计入注册表指纹）：

| 文件 | 格式 | 内容 |
|---|---|---|
| `profile.yaml` | `knowledgeos.extraction-v2-profile.v1` | 默认时区偏移、不开放抽取的概念、上限、验收口径、新实例 ID 前缀、来源系统 → 权威等级、全局单位与币种写法 |
| `cards/<concept>.yaml` | `knowledgeos.extraction-card.v1` | 概念说明卡、其他叫法、身份键与编码模式、建档最少字段；按属性给出术语、单位写法、枚举写法、值模式和说明卡 |
| `recipes/<id>.yaml` | `knowledgeos.extraction-recipe.v1` | 文档类型抽取方案，结构同协议中的 `Recipe` |

结构错误（格式、Schema、正则无效）会让注册表加载失败；本体、单位或币种变化导致的引用失效（属性解绑、概念删除、单位收窄）不会阻塞控制面修改，失效条目被忽略，并在 `GET /v1/control` 的 `extraction_v2.warnings` 中列出。契约中的概念术语合并自业务术语表（SKOS 词表）与说明卡。这些文件目前通过 Git 维护（也可经 `POST /v1/propose` 提交受审的 JSON 补丁），白板编辑入口尚未提供。

**持久化**（`knowledge.state.db`，durable schema 6，与 ledger 一起备份）：

- `extraction_contract`：已签发契约及其范围，提交时据此判定概念是否在契约内；
- `extraction_source`：来源版本、哈希、块快照与回执；`extraction_original`：内联提交的原件字节，按 SHA-256 存储；
- `extraction_submission`：提案提交、运行清单与验收报告，以（主体，`submission_id`）幂等；
- `extraction_fact_evidence`：提案事实到证据块的索引，用于新版本的 `facts_at_risk`；
- `extraction_queue_item`：`upstream_required`、`draft_instance`、`rule_signal`、`unmapped_fact` 四类待办，同时生成对应的复核项。

**提案**：每个目标实例一个 ChangeSet（新实例创建 `knowledge/entities/<id>.md`，已有实例增量修改），提案身份取自认证主体，幂等键为 `extraction-v2:<submission_id>:<node_id>`。发布时沿用 v1 的来源提及绑定（`source_entity_binding`），后续版本可复用身份。新实例之间的关系随起点实例一起提案；关系目标实例未通过校验时，关系一并拒绝。

## 10. 示例

`docs/examples/extraction-v2/` 中是同一份“供应商季度评审纪要”的完整往返示例：契约请求、契约、文档中间表示、登记回执、预检提交、验收报告。所有锚点偏移都按块文本计算，可直接作为两边的联调样例。契约、回执和验收报告由服务端实际生成（`registry_fingerprint`、`contract_id` 与时间戳替换为示例值），`pytests/test_extraction_v2_service.py` 校验它们与实现保持一致。报告中只有警告与提示，没有错误，因此整体状态为 `accepted`。示例演示了以下结果：

- 已有实例匹配；
- 字段与现有值一致（`already_present`）；
- 单值属性冲突（`conflict`）；
- 交期由 ERP 维护（`upstream_required`）；
- 规则阈值与现有约束不一致（`differs_from_existing`）；
- 未映射事实；
- 覆盖率线索。

## 11. 待定事项

- 草稿实例在什么条件下允许发布，是否需要独立的补全审核（当前进入补全队列，不生成 ChangeSet）。
- 证据库的存储后端：当前原件字节存于 state 库（随成对备份），大文件和对象存储尚未支持。
- 实体词典较大时的增量同步方式（当前为按 ID 排序的偏移分页）。
- 是否允许 KnowledgeOS 基于原件独立重新解析，以抽查 Agent 提交的块结构。
- 抽取说明卡、属性术语、值模式与抽取方案的白板编辑入口。
- 评测集（`evaluation.gold_set`）的存放与评分执行。
