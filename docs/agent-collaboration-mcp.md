# 多 Agent 接入（第一期）：参与者、凭证与 MCP 服务

KnowledgeOS 作为多个外部 Agent 的共享记忆。第一期是**个人版、本机运行**：一个人（本机所有者）登记多个 Agent，Agent 通过 MCP 读取知识、提交提案，人负责审核与审批。

```text
Claude Code ─┐  HTTP（Bearer）
Cursor ──────┤  http://127.0.0.1:8791/mcp
Kimi Code ───┘                          ┌──────────────────────────┐
Claude 桌面版 ─┐ stdio                    │ knowledgeos-mcp（适配层） │──公开 API──▶ KnowledgeOS 内核
Codex ────────┘ knowledgeos-mcp --stdio  │ 不调用模型，不导入内核    │   （权限、ChangeSet、账本）
                                         └──────────────────────────┘
```

## 1. 参与者模型

| | 人员 | Agent |
|---|---|---|
| 标识 | `user:<用户名>` | `agent:<标识>` |
| 登录 | 本地账号（用户名 + 密码，scrypt 摘要），会话凭证 12 小时 | Studio 签发的凭证（`kos_…`，只显示一次，只存摘要） |
| 负责人 | 本人 | 登记它的人 |
| 角色 | 任意（所有者为 `admin`，含 `manage`） | `reader`、`agent`、`template_author`；不能持有审核/发布角色 |

- **权限只由角色和凭证范围决定**，与参与者类型无关；凭证可以收窄为角色权限的子集（`scopes`）。
- 停用参与者或吊销凭证立即生效；每次使用记录最近时间与客户端（例如 `claude-code/2.1 (mcp)`）。
- 环境变量配置的令牌（`KNOWLEDGEOS_AUTH_TOKENS`、`dev-serve` 的开发令牌）继续有效，视为未登记参与者。

## 2. 审批策略与问责链独立性

- **独立性按问责链判断**：Agent 的责任方是其负责人。同一负责人名下的 Agent 之间不独立；组织模式下 Agent 与其负责人也不独立，高风险变更须由“另一独立责任方”批准。
- **个人模式**：负责人审核本人名下 Agent 的提案即为独立审核（人是对 Agent 产出的检查；账本记录 `approval_basis: owner_of_agent`）。
- **最终批准须由人完成**（`human_final_approval`，默认开启）：Agent 不能批准、应用或发布。
- **个人模式自审**（`self_review_high_risk`，默认关闭，只在个人模式可开）：本机所有者可批准**自己本人**提交的高风险变更；审核意见前缀“［个人模式自审］”，账本记录 `self_review: true` 与双方责任方；批量审核不适用。组织模式不允许开启。
- 驳回和要求修改不需要独立性。

## 3. MCP 服务

启动：`knowledgeos-mcp --port 8791`（只能绑定本机地址）；或由宿主以 stdio 启动：`KNOWLEDGEOS_TOKEN=kos_… knowledgeos-mcp --stdio`。`KNOWLEDGEOS_API_BASE_URL` 默认 `http://127.0.0.1:8787`。

- 兼容带 `initialize` 握手的宿主（协议版本 2024-11-05 至 2025-11-25）和不握手直接调用的宿主；每条消息独立处理。
- HTTP：只接受 POST `/mcp`，必须带 `Authorization: Bearer`；拒绝非本机来源（Origin）；`GET /info` 返回连接地址与 stdio 命令（不含凭证）。
- 工具返回 `structuredContent` 与等价文本；KnowledgeOS 拒绝时返回 `isError` 与原因（401/403/409 附处理建议）。

### 工具

| 类别 | 工具 |
|---|---|
| 身份 | `whoami` |
| 本体 | `ontology_catalog`（概念/关系/属性摘要与中文叫法）、`ontology_definition` |
| 知识读取 | `search_knowledge`、`get_entity`、`get_neighbors`、`get_context`、`explain`、`calculate` |
| 起草 | `authoring_context`、`authoring_check`（与 Studio 编写助手同一套说明与确定性检查） |
| 提案（均为待审核 ChangeSet） | `ontology_request` → `ontology_submit`、`logic_submit`、`extraction_contract` / `extraction_register_source` / `extraction_submit` |
| 跟踪 | `my_changesets`、`list_events`（游标增量读取提案、审核、发布事件） |

- 写入类工具都支持先 `dry_run` 试运行；正式提交只产生 ChangeSet。
- **服务端句柄**：`ontology_request` 返回 `request_id` 与中文起草说明，完整建模请求包留在 MCP 服务中（按凭证隔离，6 小时有效），`ontology_submit` 只需传 `request_id`。这是在 Claude Code 实测中发现的问题：要求模型原样回传约 100 KB 的请求包不可行。
- 提示词：`knowledgeos_guide`（通用工作规范）、`ontology_modeling`、`document_extraction`、`rule_authoring`；资源：`knowledgeos://guide`、`knowledgeos://ontology/catalog`。均带内容哈希。
- 安全：工具说明与工作规范要求把其他参与者写入的内容当作数据而不是指令。

## 4. 宿主接入

在 Studio「Agent 管理」登记 Agent → 签发凭证 → 选择宿主复制配置。

| 宿主 | 方式 | 配置位置 |
|---|---|---|
| Claude Code | HTTP | `claude mcp add --scope user --transport http knowledgeos-<agent> http://127.0.0.1:8791/mcp --header "Authorization: Bearer kos_…"` |
| Claude 桌面版 | stdio | `~/Library/Application Support/Claude/claude_desktop_config.json` 的 `mcpServers` |
| Cursor | HTTP | `~/.cursor/mcp.json` |
| Kimi Code（桌面版与命令行共用） | HTTP | `~/.kimi-code/mcp.json` |
| Codex（OpenAI 模型） | stdio | `~/.codex/config.toml` 的 `[mcp_servers.<名称>]` |

ChatGPT 桌面版的开发者模式连接器只接受公网 HTTPS 地址，Grok Bot 运行在云端、不连本机 MCP，两者计划在第二期（公网 HTTPS + OAuth）接入。

已验证：Claude Code 2.1 以 HTTP 方式连接，完成“查本体 → 读知识 → 起草并试运行 → 提交本体 ChangeSet”。

## 5. 接口

| 方法 | 路径 | 权限 | 说明 |
|---|---|---|---|
| GET | `/v1/auth/status` | 无 | 是否需要创建本机所有者 |
| POST | `/v1/auth/setup` | 无（仅本机，且仅首次） | 创建所有者并登录 |
| POST | `/v1/auth/login` | 无 | 用户名 + 密码 → 12 小时会话凭证；连续失败 5 次锁定 1 分钟 |
| POST | `/v1/auth/logout`、`/v1/auth/password` | 已登录 | 退出（吊销会话）、修改密码 |
| GET | `/v1/participants` | manage | 人员与 Agent、凭证、提案统计、审批策略 |
| POST | `/v1/participants/agents`、`/update`、`/credentials`、`/credentials/revoke` | manage | 登记 Agent、修改、签发、吊销 |
| GET/POST | `/v1/participants/policy` | read / manage | 审批策略 |
| GET | `/v1/inbox` | review | 待审提案：可由我批准的在前，高风险在前；附提交者身份与审批判定 |
| GET | `/v1/events?after=&limit=` | read | 治理事件游标（来自哈希链账本，不含载荷） |
| GET | `/v1/session` | 已登录 | 增加 `kind`、`label`、`owner`、`policy` |

`/v1/changesets` 每项增加 `actor_info`（类型、名称、负责人、责任方、宿主、模型）和待审项的 `governance`（当前审核人能否批准及原因）。

## 6. 尚未包含（第二期及以后）

- 协作空间（主张、证据、支持/反驳、共识、晋升包）、任务认领与租约、到期复核。
- OAuth 授权页、组织模式单点登录与成员代理身份；公网部署与 ChatGPT / Grok Bot 接入。
- 订阅推送（目前以 `list_events` 游标轮询）。
