# 编写助手（Authoring Assistant）

本体白板、规则与决策工作台、抽取配置和专家工艺模板工作台可以调用模型起草定义，协助建模人员和专家。设计目标是：模型只负责理解业务语言、起草和追问；所有判断、校验和发布仍由确定性代码与人完成。

## 1. 架构与边界

```text
Studio 助手面板 ──/api/assistant──▶ 助手网关（knowledgeos-assistant，可选进程）──▶ 模型服务
      │                                   │  ① POST /v1/authoring/context（用户本人凭证）
      │                                   │  ② 模型起草 → JSON Schema 校验
      │                                   │  ③ POST /v1/authoring/check（确定性试运行，失败则带错误重试，最多 2 轮）
      │◀──────────── 建议（不写入任何地方）──┘
      └──/api/knowledgeos──▶ 原有提案接口（/v1/logic/propose 等）＋ assistance 来源声明 ──▶ ChangeSet → 独立审核 → 应用真源 → 验证发布
```

- **内核不调用模型。** `knowledge_os` 只提供确定性的上下文、标准说明和试运行；测试 `test_kernel_never_calls_models` 保证内核代码不出现模型调用。
- **网关独立。** `knowledgeos_assistant` 只通过公开 HTTP API 访问 KnowledgeOS，并转发用户本人的 Bearer 凭证，因此上下文读取和试运行都按该用户授权（需要 `propose` 或 `template_edit` 权限）。测试保证网关不导入内核模块。
- **网关不写入。** 建议返回 Studio，由人采纳、修改，再通过原有提案接口提交；高风险变更仍需另一身份审核。
- **不配置网关也能用确定性部分**：近似属性复用提示、决策表覆盖检查、边界案例确认等都来自 `/v1/authoring/check`。

## 2. 四类任务

| 任务 | 入口 | 助手做什么 | KnowledgeOS 确定性检查 |
|---|---|---|---|
| `logic.draft` | `/rules` 工具栏「助手起草」 | 把业务说明写成决策表、匹配规则或业务约束；独立写出“意图案例” | 定义校验；决策表覆盖缺口、首条命中下永不生效的规则；规则中互相矛盾的字面条件（数量按单位换算后比较）；执行意图案例并标出与说明不一致处；生成边界案例 |
| `ontology.model` | 本体白板「建模助手 › 助手起草」 | 依据粘贴的材料起草概念、属性、关系；每项引用原文 | 原有建模协议校验与隔离编译；与已有属性/概念的近似名称检测（标签、标识、抽取说明卡叫法、业务术语），量纲不同时单独提示 |
| `extraction.card` | `/extraction` 反馈页「助手起草」「让助手处理」 | 根据抽取反馈改写说明卡：单位写法、枚举说法、属性叫法、边界说明 | 说明卡严格校验与写法试算；计算草稿能解决哪些待处理建议 |
| `template.interview` | `/templates` 工具栏「工艺访谈助手」 | 访谈式追问计价基准、重复条件、样例工序数，整理路线草稿 | 模板缺口清单、模板校验、逐个样例展开并与专家给出的预期工序数比较 |

### 意图案例与边界案例

- **意图案例**由模型根据业务说明独立写出预期结果，再由引擎执行。一致的才可勾选带入；不一致的说明“定义或预期至少有一个理解错了”，交给专家判断。
- **边界案例**由引擎在每个边界值和越界值上计算结果，专家勾选“确认结果符合业务”后才成为回归案例。系统不会把引擎自己的输出自动当作正确答案。

决策表覆盖检查是精确的：每个输入单元格在由边界值划分出的区间上取值恒定，因此对每个区间组合取一个代表值即可覆盖全部情况。组合超过 4096 个时退化为只检查各规则自身的区间，并在结果中注明。

## 3. 来源声明与度量

采纳的草稿在提交时附带 `assistance`：

```json
{"suggestion_id": "…", "task": "logic.draft", "provider": "anthropic", "model": "claude-sonnet-5-5",
 "renderer_version": "1.0.0", "instructions_hash": "<sha256>", "context_hash": "<sha256>",
 "output_hash": "<试运行时的草稿摘要>", "items_total": 5, "items_accepted": 4, "repair_rounds": 1}
```

KnowledgeOS 校验格式后写入 ChangeSet 的 `proposal_metadata.assistance`，并计算 `modified_after_suggestion`（提交内容摘要是否不同于助手草稿）。这是作者的声明，用于审核提示和统计，**不授予任何权限，也不证明模型输出正确**。审核页会显示“助手起草”说明；变更审核首页显示各任务的审核通过率、作者修改率和采纳项比例（`GET /v1/authoring/feedback`）。被放弃的草稿不会进入 KnowledgeOS。

支持声明的接口：`/v1/logic/propose`、`/v1/ontology/propose`、`/v2/extraction/assets/propose`、`/v1/templates/propose`。

## 4. 标准说明

`/v1/authoring/context` 返回按任务渲染的中文说明（Markdown），包含规则、可用定义目录、示例和输出 JSON Schema，并附 `content_hash` 与 `renderer_version`。同样的注册表和输入总是得到相同字节，审核时可以追溯模型当时收到的说明。说明中的示例由测试保证能通过确定性引擎。

命令行：

```bash
knowledgeos authoring-context --task logic.draft --input input.json --instructions-only
knowledgeos authoring-check   --task logic.draft --draft draft.json --input input.json
```

其他 Agent 平台可以直接使用这两个接口：取说明 → 自行调用模型 → 试运行 → 通过原有提案接口提交。

## 5. 配置网关

```bash
pip install -e .            # 同时安装 knowledgeos 与 knowledgeos-assistant
export KNOWLEDGEOS_API_BASE_URL=http://127.0.0.1:8787
export KNOWLEDGEOS_ASSISTANT_PROVIDER=anthropic          # anthropic | openai_compatible | demo
export KNOWLEDGEOS_ASSISTANT_MODEL=claude-sonnet-5-5
export KNOWLEDGEOS_ASSISTANT_API_KEY=...
knowledgeos-assistant --port 8790
```

| 变量 | 默认 | 说明 |
|---|---|---|
| `KNOWLEDGEOS_ASSISTANT_PROVIDER` | 未设置 | `anthropic`（Messages API）、`openai_compatible`（任意 chat completions 兼容端点，含私有化部署）、`demo`（固定样例，不调用模型） |
| `KNOWLEDGEOS_ASSISTANT_MODEL` | anthropic 为 `claude-sonnet-5-5` | 模型名 |
| `KNOWLEDGEOS_ASSISTANT_API_KEY` | — | 只在网关进程内使用，不出现在状态接口或日志 |
| `KNOWLEDGEOS_ASSISTANT_BASE_URL` | anthropic 为 `https://api.anthropic.com` | `openai_compatible` 必填，例如 `https://llm.example.com/v1` |
| `KNOWLEDGEOS_ASSISTANT_JSON_MODE` | `true` | 兼容端点不支持 `response_format` 时设为 `false` |
| `KNOWLEDGEOS_ASSISTANT_SEND_DOCUMENT_SAMPLES` | `true` | 设为 `false` 时，抽取说明卡任务不向模型发送原文样例片段 |
| `KNOWLEDGEOS_ASSISTANT_MAX_REPAIRS` | `2` | 校验失败后的自动修正轮数（0–4） |
| `KNOWLEDGEOS_ASSISTANT_MAX_TOKENS` / `TIMEOUT` | `8000` / `120` 秒 | 单次模型调用上限 |
| `KNOWLEDGEOS_ASSISTANT_REQUESTS_PER_MINUTE` | `20` | 按用户凭证限流 |

Studio 通过 `KNOWLEDGEOS_ASSISTANT_BASE_URL`（Worker 绑定或 `.env.local`）找到网关；未设置时，助手面板提示未配置，确定性检查照常可用。

### 发送给模型的数据

| 任务 | 发送内容 |
|---|---|
| 业务逻辑 | 控制定义目录（概念、属性、单位、已登记逻辑名称）、用户填写的说明、可选的当前草稿 |
| 本体建模 | 控制定义目录与中文叫法、用户粘贴的建模依据 |
| 抽取说明卡 | 该概念的属性定义、当前说明卡、待处理建议；默认包含建议附带的原文样例片段（可关闭） |
| 工艺访谈 | 已发布工序与时间模型目录、对话内容、可选的当前模板草稿 |

不发送实例断言、连接器记录、账本或其他业务运营数据。网关日志只记录任务、耗时、修正轮数和用量，不记录提示词、草稿或凭证。

## 6. 本地预览

双击 `启动KnowledgeOS预览.command`（或 `bash scripts/preview.sh`）会同时启动后端（8787）、助手网关（8790）与 Studio（5173）。未配置模型时网关以**演示模式**运行，所有草稿都标注“演示模式：固定样例，未调用模型”。要使用真实模型，创建 `runtime/assistant.env`（已忽略，不会提交）：

```bash
KNOWLEDGEOS_ASSISTANT_PROVIDER=anthropic
KNOWLEDGEOS_ASSISTANT_MODEL=claude-sonnet-5-5
KNOWLEDGEOS_ASSISTANT_API_KEY=...
```

然后关闭预览窗口重新双击启动。

## 7. 接口

| 方法 | 路径 | 权限 | 说明 |
|---|---|---|---|
| GET | `/v1/authoring/tasks` | read | 任务目录与渲染器版本 |
| POST | `/v1/authoring/context` | propose 或 template_edit | `{task, input, include:["instructions"]}` → 上下文、输出 Schema、标准说明 |
| POST | `/v1/authoring/check` | propose 或 template_edit | `{task, draft, input?}` → 问题列表、分析结果、`draft_hash`；不写入 |
| GET | `/v1/authoring/feedback` | read | 声明助手来源的提案统计 |
| GET | 网关 `/v1/assistant/status` | — | 是否已配置、提供方与模型（无密钥） |
| POST | 网关 `/v1/assistant/suggest` | 用户凭证 | `{task, input}` → 建议、检查结果、来源声明 |

## 8. 尚未包含

- 网关不保存会话；访谈记录只在页面内存中，刷新即清空。
- 说明模板写在代码中（`authoring.py`），尚未作为可审核的控制资产维护。
- 尚未提供英文说明、MCP 工具封装与流式输出。
