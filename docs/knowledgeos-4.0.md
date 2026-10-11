# KnowledgeOS 4.0：方案 B 交付与升级指南

4.0 在既有架构上增加标准语义投影、共享校验、业务术语、建模助手和人工批量审核。Python 包和 Studio 的版本均为 **4.0.0**，控制/API 契约为 **4.0**。实体源文件继续使用 `ckm: '3.0'`，既有实体 ID、谓词 ID、连接器记录、ChangeSet、模型历史和账本保持兼容。

## 日常使用

1. 登录 Studio，进入结构白板，打开「建模助手」。
2. 在「辅助建模」填写对象名称、含义，勾选已有属性。「检查草稿」会在隔离工作区编译候选定义、重放已保存的连接器记录，并运行配置的核心问题检查。查看变更内容后提交审核。
3. 新草稿为 draft；提交不会直接修改控制文件。由另一位审核人批准，再由发布人「应用到真源」「验证发布」。已有白板中的属性、关系与约束编辑仍可使用。
4. 在「业务术语」维护常用名称、其他叫法和定义。其他语言标签与未编辑的术语保留。术语变更递增版本，走同一审核流程。
5. 在「检查与导出」检查核心问题或下载 JSON-LD/Turtle。常规用户无需学习标准语法。
6. 审核中心提供「批量审核」：选择记录、填写意见、预览、明确确认决定。最多 50 项，每项保留独立审计记录。高风险提案仍须独立审核；专家工艺模板继续使用其单独权限和流程。批准不会自动应用或发布。

建模助手的普通表单使用确定性模板组装草稿，没有在后台调用未配置的大模型。「更多选项与 Agent 草稿」可导出带现有定义、语义契约、证据材料、输出格式的请求，并导入外部 Agent 的返回结果。API 允许 Agent 自动构建概念、属性和关系；服务器校验来源引文、注册关系、现有数据及请求版本后才接受提案。模型引用了材料，不等于材料逻辑已经被证明；独立审核仍负责业务判断。

## 范围与架构映射

| 项目 | 4.0 实现 | 原有架构与边界 |
|---|---|---|
| A00 / A01 | `knowledgeos.semantic-profile.v1`、语义 IR、按类型隔离的稳定 IRI及无损 ID 还原 | `control/semantics/profile.yaml` 是 Git 真源；IRI 是现有 ID 的投影。标签修改不改变 ID。生产命名空间应保持稳定，改命名空间须单独规划迁移。 |
| A03 | 本体/谓词/关系、术语及生成的 Shapes 导出 JSON-LD/Turtle | RDF 1.1 / RDFS 与 JSON-LD 1.1；只读导出，不增加 RDF 数据库或外部导入。多种允许端点组合使用 KnowledgeOS 扩展表达，避免多个 RDFS domain/range 导致交集误读。 |
| A04 | SKOS 概念方案、首选名称、别名、定义、范围说明、上位概念与 closeMatch | `control/vocabularies/*.yaml`；同语种首选名称唯一、引用有效、应用约定的层级无环。SKOS 术语与本体类使用不同 IRI；链接为 `kos:describesClass`，不会推导类或实例相等。普通 UI 编辑概念关联的中文名称/别名/定义，完整词表结构可由 Git 维护。 |
| A06 / A07 | 从概念属性绑定生成 SHACL Core 子集并执行校验 | 必填、单值上限、标量类型、枚举、数值边界、引用 IRI；原生编译/连接器/抽取入口共享校验。时态、多版本事实、单位、权威来源、Tier A证据、关系组合/具象化和业务规则继续由原生确定性代码校验。不是任意 SHACL 或 OWL 解释器。 |
| A09 | PROV-O来源导出 | 源与断言为 `prov:Entity`，ChangeSet 为 `prov:Activity`、提案人为 `prov:Agent`；仅导出记录中存在的派生/修订关系，保留原生时态、证据、来源地址/哈希/版本、等级和状态。不会虚构生成活动或把 proposed 转成 confirmed。 |
| A20 | Skill成功标准 | 可配置 `requires_claims`、`requires_citations`、`requires_model_trace`；服务验证声明/引用及确定性模型记录。返回 acceptance；引用有效性不是语义蕴涵证明。未配置标准的旧 Skill 继续可用。 |
| A21 | 核心问题回归套件 | `control/semantics/evaluation.yaml` 支持属性绑定、关系端点、术语名称及 IRI还原四种确定性检查。当前核心案例是回归底线，扩展业务案例仍须配置；不是对所有业务知识正确性的认证。 |
| A22 | 抽取契约增强 | 加入标准 IRI、Shape IRI、术语及校验契约，纳入请求指纹。已有抽取/解析/ChangeSet机制保持；未知事实进入 unmapped_facts，企业运营事实继续经连接器。 |
| A23 | 人与 Agent 共用建模契约及 Studio助手 | 引文精确匹配、来源材料与证据保存于 durable proposal metadata；改变注册定义须重新请求。可以一批创建属性和绑定概念，不执行自由文本代码。 |
| A25 | 语义版本快照与候选影响评估 | `durable.semantic_revision` 保存 IR、注册指纹、契约版本，追加审计事件；隔离编译检测断裂，写源前再次检查。破坏性变化要求修正数据/迁移后重新提案，首期不自动迁移。 |
| A26（首期） | 来源/风险分组、人工批量预览和决定 | 预览指纹包含当前版本及 lock_version，改变后拒绝旧确认；事务内全部成功或回滚。没有自动审批或替代独立审核。 |
| A27 | 带版本和校验和的 state/ledger成对备份与恢复 | 查询索引可重建；版本 4 的耐久布局为加表、加字段，既有数据不删除。恢复拒绝覆盖非空运行目录、源码指纹不匹配或高于当前可处理布局的备份。 |

OWL/SWRL、SPARQL写入口、BPMN/OWL-S执行、ODRL策略解释及自动批准没有加入本期。现有确定性模型、业务规则、权限与 Skill架构继续负责这些相应能力。

## API 与 Agent

所有新端点复用现有 Bearer 身份。`actor`、`reviewer` 由 HTTP身份决定，客户端无法冒名。

| 端点 | 作用 / 权限 |
|---|---|
| GET `/v1/semantics` | Profile、IR、词表、能力范围；read |
| GET `/v1/semantics/export?format=turtle` | 控制投影；read。也支持 json-ld。 |
| GET `/v1/semantics/export?scope=provenance&id=org:acme` | 指定实体来源；省略 id 则导出全局来源和 ChangeSet活动。read |
| GET `/v1/semantics/history` | 最近 50 个编译后的语义版本；read |
| GET `/v1/semantics/evaluate` | 核心问题检查；read |
| POST `/v1/semantics/validate` | `{document: canonicalObject}`，原生+生成的 SHACL校验；read |
| POST `/v1/semantics/impact` | `{target_source,patch,operations}`，控制文件候选影响检查；propose |
| POST `/v1/ontology/request` | `{goal,material}`，准备便携建模请求；propose |
| POST `/v1/ontology/preview` | `{request,model_output}`，证据/格式/影响检查；propose |
| POST `/v1/ontology/propose` | 增加 reason、可选 idempotency_key；propose，高风险独立审核 |
| POST `/v1/semantics/vocabulary/propose` | `{vocabulary,base_revision,reason,idempotency_key?}`；propose，高风险独立审核 |
| POST `/v1/changesets/review-batch/preview` | `{ids,decision}`；review |
| POST `/v1/changesets/review-batch` | 增加 preview_hash、note，显式确认；review |

建模请求限制：目标 4,000字符、依据材料 30,000字符、每份输出最多20项操作。Agent输出仅支持概念/谓词/关系的 create/update/deprecate。证据须引用请求中的 segment_id 和原文，禁止自由导入、删除及运营实体事实写入。

词表的 id 和术语 id 是小写稳定标识。词表最多500项，更新须提高三段语义版本。Profile 不接受外部上下文、远程 ontology imports、JS或推理选项；JSON-LD导出无需远程 context。服务只执行自身生成的 Shapes，未提供任意 SPARQL/SHACL执行接口。

## 命令行

安装 4.0依赖后，可以继续使用现有启动和治理命令。

```bash
python -m pip install -e '.[test]'
knowledgeos --version
knowledgeos semantics
knowledgeos semantic-evaluate
knowledgeos semantic-export --format turtle > control-export.ttl
knowledgeos semantic-export --format json-ld > control-export.jsonld
knowledgeos semantic-export --scope provenance --id org:acme > provenance.ttl
knowledgeos semantic-history
knowledgeos ontology-request --goal '建立检验报告概念' --material-file modeling-notes.txt > modeling-request.json
knowledgeos ontology-preview --request modeling-request.json --model-response agent-output.json
knowledgeos ontology-propose --request modeling-request.json --model-response agent-output.json --actor agent:modeler --reason '统一检验建模' --idempotency-key inspection-v1
```

导出放在控制真源以外。`semantic-evaluate` 返回非零状态表示配置案例失败。CLI文件路径支持现有 envelope 或建模请求本体；CLI提案身份由本地执行者提供，远程调用应使用受认证 API。

## 升级和回退

先保存包含未提交工作的一份完整源码快照，停止访问运行目录的所有 API/CLI进程，使用旧版匹配源码的 `knowledgeos backup <new-directory>` 成对备份 state和ledger。保留对应源码指纹与备份清单，备份不提交 Git。

安装新依赖，再运行 `knowledgeos doctor`、`knowledgeos rebuild`、`knowledgeos semantic-evaluate`、`knowledgeos verify-ledger`，确认 `/health`报告4.0.0后启动 Studio。首次打开4.0服务添加语义版本表和提案元数据列；编译成功后记录标准契约快照。后端和界面须同时切换，浏览器凭证保留规则不变。

需要回退时，在**另一个干净目录**解开升级前源码快照，按旧版安装其依赖，保持 runtime为空；使用匹配备份恢复 state与ledger，重建索引并验证账本，再切换服务端口。不要把升级前数据库覆盖到仍在运行的4.0目录，也不要把4.0期间的新耐久状态直接交给旧版。回退点之后产生的提案、模型运行和连接器增量，应另行保存后安排合并。

本次开发前的源码快照位于忽略目录 `.release-backups/pre-4.0-20261012/`。其中 tar.gz包含当时所有受版本控制或未忽略的源文件，包括原有未提交修改；manifest和patch可用于核对。数据库备份与恢复验证结果见该目录的 `release.json`。本次首次4.0重建在备份成功前执行；`durable/`记录4.0初始化后的成对状态，尚无4.0用户提案或连接器写入。`durable-compatible-3.6/`使用同一成对数据及升级前源码指纹，已在隔离目录用3.6源码恢复、重建并验证账本；额外的编译快照和审计记录保留。这是已验证的兼容回退备份，不是初始化前的数据库快照。不要使用 `git reset --hard`回退：它会丢弃开发前已有的未提交工作。

## 验证范围

后端回归覆盖原有编译、连接器、抽取、模型、账本、治理与 API行为，新增标准图等价、稳定IRI、SKOS身份隔离、SHACL门禁、来源保存、辅助建模审核/应用/发布、连接器影响预览、批量审核原子性及版本备份测试。前端覆盖中文稳定标识、草稿契约、已注册属性限制、术语版本/语言保留与显式批量预览请求；另运行 TypeScript、lint和生产构建。UI人工检查包含桌面和窄屏助手操作。

2026-10-12 最终验证：后端 85 项、前端 49 项全部通过；TypeScript 与生产构建通过，lint 无错误（保留页面原有 6 条警告）。当前工作区重建成功，3 个核心问题检查通过，账本链校验通过。升级前源码与兼容回退备份位于上述目录；4.0 交付源码及对应成对数据库另存于 `.release-backups/knowledgeos-4.0.0-20261012/`。
