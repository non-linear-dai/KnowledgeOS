# KnowledgeOS 4.5：发布说明与升级指南

4.5 在 4.0 的语义底座上增加三组能力：**抽取协议 v2**（外部 Agent 入库的契约、验收与沉淀）、**编写助手**（模型辅助起草，内核不调用模型），以及**多 Agent 接入第一期**（参与者与凭证、问责链审批、本机 MCP 服务）。Python 包、MCP 适配层与 Studio 版本均为 **4.5.0**，控制/API 契约为 **4.5**，持久化状态 schema 为 **7**。4.0 的实体源文件、谓词 ID、连接器记录、ChangeSet、模型历史和账本保持兼容。

## 新增能力

| 方向 | 内容 | 文档 |
|---|---|---|
| 抽取协议 v2 | 契约生成、来源登记与块快照、规范化（中文数字、区间、单位/币种、日期）、服务端验收（锚点、字段级证据、身份解析、权威冲突）、每实例一个 ChangeSet、待办队列；Studio「抽取配置」工作台（说明卡、术语、抽取方案、反馈闭环）。v1 接口保持不变。 | [extraction-v2.md](extraction-v2.md) |
| 编写助手 | `/v1/authoring/*` 确定性说明与试运行（决策表覆盖与边界案例、矛盾条件、近似属性/概念检测）；可选 `knowledgeos-assistant` 网关（Anthropic / OpenAI 兼容 / 演示）；Studio 四个工作台的「助手起草」；提案记录助手来源声明与采纳度量。 | [authoring-assistant.md](authoring-assistant.md) |
| 多 Agent 接入（第一期） | 本地账号与本机所有者；Agent 登记负责人、`kos_` 范围化凭证；问责链独立性、Agent 不能批准或发布、个人模式下负责人审核本人名下 Agent 的提案、可选自审；治理事件游标；「我的待审」；`knowledgeos-mcp`（本机 HTTP + stdio，19 个工具、角色提示词、服务端句柄）；Claude Code、Claude 桌面版、Cursor、Kimi Code、Codex 接入配置。 | [agent-collaboration-mcp.md](agent-collaboration-mcp.md) |

## 升级

1. 停止 API/CLI 写入方，按 4.0 指南做一次离线备份（`knowledgeos backup <目录>`）。
2. 更新代码后 `pip install -e .`（新增 `knowledgeos-assistant`、`knowledgeos-mcp` 两个命令）。
3. 首次启动时自动建表：state schema 6（抽取 v2）与 7（参与者、凭证、审批策略）。无需手工迁移。
4. 环境变量令牌（`KNOWLEDGEOS_AUTH_TOKENS`）与 `dev-serve` 开发令牌继续有效；本地账号与 Agent 凭证是新增方式。首次在 Studio「账户与连接」创建本机所有者（仅能从本机创建一次）。
5. 可选：启动 `knowledgeos-mcp --port 8791`（只绑定本机地址）与 `knowledgeos-assistant --port 8790`；Studio 通过 `KNOWLEDGEOS_MCP_BASE_URL`、`KNOWLEDGEOS_ASSISTANT_BASE_URL` 找到它们。本地预览脚本会全部自动启动。

## 回滚

- 4.5 的备份（schema 7）不能由 4.0 恢复，4.0 会明确拒绝；回滚请使用升级前的 4.0 备份，在独立工作区恢复后重建索引并校验账本。
- 新增表（抽取 v2、参与者与凭证）对 4.0 不可见；回滚后 Agent 凭证与本地账号失效，环境变量令牌照常可用。

## 已知限制

- 多 Agent 第一期只在本机运行；ChatGPT 桌面版（开发者模式只接受公网 HTTPS）与 Grok Bot（云端运行）计划在第二期随公网 HTTPS 与 OAuth 接入。
- 协作空间（主张、证据、共识、晋升包）、任务认领与租约、到期自动复核尚未实现。
- 编写助手的说明模板仍在代码中维护（渲染器 1.1.0），尚未作为可审核的控制资产。
