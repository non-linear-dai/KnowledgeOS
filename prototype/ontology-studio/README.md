# KnowledgeOS Ontology Studio

业务规则、业务约束、计算模型、决策表和决策模型由独立的 [业务规则与决策工作台](../../docs/business-logic-workbench.md)统一维护，入口为 `/rules`；本体、实例、工艺与审核提供互相定位和版本引用。

标准工艺路线实例由独立的 [专家工艺模板工作台](../../docs/expert-template-workbench.md)维护，入口为 /templates。其作者、审核和发布权限在 Python API 分开校验；结构白板仍只维护本体与控制定义。

KnowledgeOS Studio 4.0.0 是面向 Contract 4.0 的非实例结构与治理控制面。界面从后端 `GET /v1/studio` 加载完整定义目录；结构白板显示 Schema、概念、关系、判断类型、模型、业务约束和业务规则卡片，均可查看和编辑草稿，再通过 ChangeSet API 提交、审核和登记发布。物理单位与货币仍由 Git 控制文件维护，不显示在白板。单文件模型、物理单位、货币、业务约束及业务规则经独立审核后，可由具备发布权限的用户在界面应用到 Git 真源，再验证发布；这一步不自动创建 Git commit。

Studio 不提供知识抽取页，也不承担文件、图像、音频或视频的内容抽取。外部 Agent 与适配 Skill 负责把来源材料转换为带来源引用的结构化候选；KnowledgeOS 负责验证其数据结构、治理结构化真源并提供 ChangeSet 交互。后端现有抽取协议仅作为兼容接口保留，不通过 Studio 代理暴露。

业务约束校验单个概念实例的事实，启用后也在知识编译及连接器记录接收时执行；业务规则对主体与候选实体的已确认事实作确定性比较。主体条件失败返回“未命中规则”，主体条件通过而候选条件失败返回“适用后条件失败”，通过全部条件返回“符合条件”，缺少判断所需事实时返回“信息不足”，并附事实来源。规则可以选填已注册的关系类型，结果中给出推导边，但不自动写入实体关系。白板提供适用概念、输入判断类型、比较条件、版本、样例事实试算和现有实体影响评估；试算与影响评估不写入真源。未发布的草稿可删除；已发布的定义通过递增版本修改或弃用。`control/constraints/business/` 和 `control/rules/business/` 只容纳业务定义，既有技术约束与维护规则不作为业务卡片。

4.0 的「建模助手」提供业务语言表单、Agent 草稿验证、术语维护和标准导出；审核中心提供显式批量预览与人工确认。详见 [4.0 交付与升级指南](../../docs/knowledgeos-4.0.md)。

## 工作区与界面设计

左侧导航提供四个独立工作区：`/` 本体建模白板、`/instances` 实例搜索查看平台、`/rules` 业务规则与决策工作台、`/templates` 专家流程模板工作台。知识管理区提供 `/changesets` 统一变更审核与 `/overview` 定义总览两个独立入口；不设置横向顶部栏，操作入口集中在画布浮动工具组、目录面板和页面内容中。本体白板与定义总览共享同一编辑实例，切换保留草稿和视口。其他工作区按需加载，访问过的工作区保留挂载，因此路由切换不会丢失本次页面会话中的未提交草稿、目录筛选和当前选择。刷新浏览器仍会结束内存会话；不在浏览器持久保存凭证或业务草稿。

实例平台通过只读 `GET /v1/instances` 统一浏览概念实例（包含已发布工艺模板、工序等节点）、关系边、判断实例（稳定属性与断言）以及已记录的模型运行结果。左侧紧凑目录提供类别选择和关键词搜索，具体类型、记录状态、来源与排序收在可展开的筛选面板中；列表只显示名称、类别与简要信息。搜索覆盖名称、稳定 ID、别名、关系端点、判断值及模型记录内容。服务端分页默认每页 25 条，最多 200 条；跨类型记录使用独立的投影 key，避免同名 ID、复合关系或时态版本冲突。`GET /v1/instances/get` 提供相应详情、来源与证据、关联主体，以及模型保存的输出、计算过程和定义快照；概念详情保留原有属性、历史事实与入向/出向关系浏览。查看记录不会重新运行模型或产生持久变更，尚未完成发布的模板包不进入目录。

本体白板的浮动工具栏提供“工作领域”入口，实例目录也提供常驻领域选择；默认均为“全部领域”。选择“成本分析”等领域后，白板仅渲染注册领域相关的概念、关系、判断、模型和业务规则，并沿用全局视图的类型分列规则：同类卡片纵向排列，不同类型横向错开，整体自动居中；领域可与现有搜索及类型筛选组合，组合筛选时仍保持类型分列。白板使用 Studio 返回的领域 `definition_scope`，实例接口通过 `domain` 参数使用同一注册范围，并在服务端计算分页、类别数量及筛选选项。范围由领域包的概念入口、检索配置、必需模型及其直接依赖确定，共享判断不会反向扩大到其他领域；新增领域无需修改前端选项。空白单击只取消卡片聚焦，保留当前领域选择。

三个工作区统一采用参考蓝 `#3983F7`、浅蓝底色与白色内容面板；主要按钮使用同色系较深蓝色，保证白字可读性。本体分类恢复独立配色：概念蓝、关系青绿、判断紫、模型琥珀、约束玫瑰、规则草绿、核心契约蓝灰。类别色集中在标识、卡片侧边和类型图例，名称与缩写同时保留；普通连线保持低饱和蓝灰，选中关联再以主蓝色强调。工艺阶段和条件标签沿用浅蓝中性底色。风险与失败状态保留语义色和文字。字体使用本地系统字体，不请求远程字体资源。可见键盘焦点、减少动画偏好、跳到工作区入口和配色对比度检查继续保留。

桌面导航默认收拢为 64px 图标栏；悬浮或键盘聚焦图标时，仅显示对应名称的蓝底白字提示，不展开侧边栏。收拢时不显示固定图标；先点击 KnowledgeOS 标识或导航展开入口，再使用顶部标识右侧的“固定展开”，完整导航始终展开并预留 240px；取消固定立即恢复图标栏。显式点击导航展开入口仍可临时查看完整导航，进入工作区、选择入口或按 Esc 后收拢，临时展开不改变白板宽度。固定偏好以非敏感设置保存在当前浏览器并在同源页面间同步；不会持久保存凭证或草稿。900px 以下沿用抽屉导航，关闭后完全隐藏，桌面固定偏好在返回宽屏时恢复。本体白板首次进入显示可读的局部范围，可平移、缩放或使用目录定位；返回工作区保留已有视口。实例目录在宽屏占约三分之一，右侧详情使用剩余宽度与完整工作区高度，二者独立滚动；680px 以下详情覆盖目录并可用 Esc 或关闭按钮返回。`⌘/Ctrl K` 仅作用于当前工作区；白板的 `Shift F`、`[`、`]`、`Esc` 和工艺模板的现有编辑快捷键继续可用。

左下角账户入口和各工作区共享内存中的个人凭证与身份，切换工作区无需重复登录。现有角色授权、独立审核、ChangeSet、真源和后端契约不变。

## KnowledgeOS API connection

Copy `.env.example` to `.env.local` for local development and set `KNOWLEDGEOS_API_BASE_URL` to the reachable Python API origin. The server-side `/api/knowledgeos/*` proxy exposes only the Studio routes required by this UI, so the browser never needs direct cross-origin access.

Use Studio's login button with a personal bearer token from `KNOWLEDGEOS_AUTH_TOKENS`. The proxy forwards that token to the Python API; it does not use a shared service token. Credentials stay in page memory, and `/v1/session` supplies the user's identity and permissions. Hosting-platform sign-in described below is separate and does not grant KnowledgeOS permissions.

For local development, start the backend with `knowledgeos dev-serve --port 8787`, then enter `local-admin-token` in Studio's login dialog. The fixed key is accepted only by that explicit loopback-only command; normal `serve` continues to require `KNOWLEDGEOS_AUTH_TOKENS`.
To test high-risk ChangeSet approval locally, submit as `local-admin-token`, switch Studio login to `local-reviewer-token`, approve, apply, and publish. The second development key has a distinct principal so the independent-review check remains active. Both keys work only with `dev-serve` on loopback.

Missing configuration, authentication failure, insufficient permissions, and connection loss have distinct UI states. Existing snapshots and drafts are preserved on connection failure. Demo mode is entered only through the explicit demo button. Drafts are rebased on refresh; conflicting changes remain visible and cannot be submitted until resolved. Local demo decisions cannot affect a live ChangeSet.

The built Worker preview reads `KNOWLEDGEOS_API_BASE_URL` as a Worker binding. For a local production-build check, start the Python API on port 8787 and run `npm start -- --port 15174 --var KNOWLEDGEOS_API_BASE_URL:http://127.0.0.1:8787`. Configure the same binding on the hosting platform for deployment. `.env.local` supplies the development server and does not by itself configure the built Worker preview.

## Prerequisites

- Node.js `>=22.13.0`
- pnpm `11.25.0` (install with `npm install -g pnpm@11.25.0`)
- Portable: Windows, macOS, or Linux; no Bash required
- Managed Linux: managed Linux runtime with Bash, `flock`, `curl`, `sha256sum`, and GNU `timeout`
- Git is required only for publishing

## Sites Lifecycle

The Sites initializer copies the shared starter and selects managed-linux only when `SITES_MANAGED_LINUX_CONTAINER=1`; otherwise it selects portable. It saves the selection only in ignored `.sites-runtime/execution-profile.json`. Both profiles copy/configure first, then use the plugin's separate `install-dependencies.mjs` step to measure installation independently. Edit source under `app/` and follow the Sites skill for installation, preview, builds, and publishing.

Run `node <plugin-root>/scripts/configure-execution-profile.mjs` only when the profile is unknown for the current checkout and environment. Profile changes do not alter tracked source or require reinstalling otherwise-valid dependencies; restart an existing preview to use the new selection. Do not commit or upload `.sites-runtime/`.

This starter does not use `wrangler.jsonc`.

`install:ci` runs `pnpm install --frozen-lockfile` against the checked-in `pnpm-lock.yaml`. Do not overlap installers.

- **Portable:** Preserve host HOME, pnpm store, registry, proxy, and temporary paths. Use `--prefer-offline`.
- **Managed Linux:** Use the existing project-local HOME/cache/tmp setup and Linux install lock, tarball preflight, and timeout. Restore the image-seeded pnpm store only when its lockfile hash matches; retain network fallback. Builds keep their existing timeout. These helpers are not invoked by the portable profile.

`scripts/sites-env.mjs` preserves the caller's HOME, package cache, proxy, XDG, and temporary-directory configuration while defaulting Wrangler and Miniflare state to the checkout. The `dev` and `start` scripts also keep Wrangler logs inside the checkout. Generated `.sites-runtime/` and `.wrangler/` directories are disposable and ignored by Git.

On portable, `npm run dev` uses `vinext dev` with HMR, starting at port 5173. Vinext records the running server in ignored `.vinext/` state, rejects an ordinary duplicate launch, and recovers stale state after a stopped process; exactly simultaneous starts can race. Pass `--port <port>` or `--hostname <host>` after `npm run dev --` when needed; keep portable previews on loopback.

For browser QA on managed Linux, use `sites-preview start`. The project's dev script runs Vite and accepts the supervisor's `--host 0.0.0.0 --port 4173 --strictPort` arguments. The internal browser uses `http://terminal.local:4173/`; it is not a user-facing URL. The supervisor owns the preview lifecycle. The ignored local profile survives the supervisor's cleared process environment.

The portable profile simulates ChatGPT sign-in only for loopback development requests. Visit `/signin-with-chatgpt?return_to=/` to sign in as `local_seedy` (`seedy@sites.test`, display name `Seedy`) and `/signout-with-chatgpt?return_to=/` to sign out. The development cookie preserves that identity across server restarts. Mock auth is disabled in the managed-linux profile and is not included in production builds; hosted authentication remains dispatch-owned.

The Worker uses `vinext/server/fetch-handler`, including Vinext's config-aware image handling. After building, `npm start` runs that Worker locally through Wrangler on `127.0.0.1`, sharing `.wrangler/state` with dev preview and local D1 migrations; it does not deploy the site or simulate sign-in. Use the URL printed by the server. Pass `npm start -- --port <port>` to select a different built-preview port.

Local previews use Miniflare's placeholder `Request.cf` metadata without a network lookup. Set `CLOUDFLARE_CF_FETCH_ENABLED=true` to opt into fetching preview metadata; this setting does not change hosted request metadata.

Local tool usage metrics are disabled by default. Set `WRANGLER_SEND_METRICS=true` to opt in.

## Included Shape

- edit site code under `app/`
- `app/chatgpt-auth.ts` provides optional dispatch-owned ChatGPT sign-in helpers
- `.openai/hosting.json` declares optional Sites D1 and R2 bindings
- `vite.config.ts` simulates declared bindings for local development
- `db/index.ts` reads the D1 binding from the Cloudflare Worker environment
- `db/schema.ts` starts intentionally empty
- `@cloudflare/workers-types` provides Worker types; `cloudflare-env.d.ts` declares optional `DB`/`BUCKET` bindings—update these declarations if binding names change
- `examples/d1/` contains an optional D1 example surface
- `drizzle.config.ts` supports local migration generation when needed

## Workspace Auth Headers

Signed-in visitors receive both `oai-authenticated-user-id` and `oai-authenticated-user-email`. Private Sites require every visitor to sign in; public Sites may also have anonymous visitors, for whom neither header is present.

The user ID is stable for the same user on the same Site and different across Sites. Use it as the durable user key; use email and name for display or contact purposes.

SIWC-authenticated workspace sites may also receive `oai-authenticated-user-full-name` when the user's SIWC profile has a non-empty `name` claim. The full-name value is percent-encoded UTF-8 and is accompanied by `oai-authenticated-user-full-name-encoding: percent-encoded-utf-8`.

Treat the full name as optional and fall back to email when it is absent:

```tsx
import { headers } from "next/headers";

export default async function Home() {
  const requestHeaders = await headers();
  const userId = requestHeaders.get("oai-authenticated-user-id");
  const email = requestHeaders.get("oai-authenticated-user-email");
  const encodedFullName = requestHeaders.get("oai-authenticated-user-full-name");
  const fullName =
    encodedFullName &&
    requestHeaders.get("oai-authenticated-user-full-name-encoding") ===
      "percent-encoded-utf-8"
      ? decodeURIComponent(encodedFullName)
      : null;

  const displayName = fullName ?? email;
  // ...
}
```

## Optional Dispatch-Owned ChatGPT Sign-In

Import the ready-to-use helpers from `app/chatgpt-auth.ts` when the site needs optional or required ChatGPT sign-in:

- Use `getChatGPTUser()` for optional signed-in UI.
- Use the returned `userId` as the stable user key for user-owned records; do not use email as a durable identifier.
- Use `requireChatGPTUser(returnTo)` for server-rendered pages that should send anonymous visitors through Sign in with ChatGPT.
- In a Server Component, start sign-in with `<a href={chatGPTSignInPath(returnTo)} target="_top">`. The auth helper module is server-only; do not import it into a Client Component.
- Do not use `fetch`, XHR, a client-side router, or a framework link that can prefetch the sign-in route. SIWC must start as a top-level navigation.
- Never request the AuthAPI authorization endpoint directly. The dispatch-owned `/signin-with-chatgpt` route must start the SIWC flow.
- Use `chatGPTSignOutPath(returnTo)` for browser sign-out links or actions.
- Pass a same-origin relative `returnTo` path for the destination after sign-in or sign-out. The helper validates and safely encodes it.
- Mark protected pages with `export const dynamic = "force-dynamic"` because they depend on per-request identity headers.

Dispatch owns `/signin-with-chatgpt`, `/signout-with-chatgpt`, `/callback`, the OAuth cookies, and identity header injection. Do not implement app routes for those reserved paths. Routes that do not import and call the helper remain anonymous-compatible.

SIWC establishes identity only; it does not prove workspace membership. Use the Sites hosting platform's access policy controls for workspace-wide restrictions, or enforce explicit server-side membership or allowlist checks.

Use SIWC for account pages, user-specific dashboards, saved records, and write actions tied to the current ChatGPT user. Leave public content anonymous.

## Local D1 migrations

For a D1-backed local preview, generate SQL with `npm run db:generate`. Build once through the Sites skill's build entrypoint (or `npm run build` for standalone use) to generate `dist/server/wrangler.json`, rebuilding if bindings change. From the project root, apply each pending migration in order:

```sh
node --import ./scripts/sites-env.mjs ./node_modules/wrangler/bin/wrangler.js d1 execute DB --local --config dist/server/wrangler.json --persist-to .wrangler/state --file drizzle/0000_example.sql
```

Replace the filename with the pending migration and `DB` with your D1 binding name if different. Use `.wrangler/state`, not `.wrangler/state/v3`; Wrangler adds the versioned directories. Do not replay migrations already applied locally. This updates only the preview database; publishing applies production migrations separately.

## Diagnostic Commands

- `npm run install:ci`: perform the one locked dependency install
- `npm run dev`: start the Vite/Vinext development server
- `npm run build`: build the deployable Sites artifact
- `npm run start`: preview the built Worker locally with D1/R2 support
- `npm run db:generate`: generate Drizzle migrations after schema changes

When using the Sites plugin, follow its skill instructions for installation, builds, and publishing. These npm commands remain available for standalone use.

The portable build runs Vinext directly without a host `timeout` command. The managed-linux build uses `scripts/build-verified.sh` and its existing `SITES_BUILD_TIMEOUT` setting.

## Learn More

- [vinext Documentation](https://github.com/cloudflare/vinext)
- [Drizzle D1 Guide](https://orm.drizzle.team/docs/get-started/d1-new)

变更审核统一加载最近 500 项本体、知识和专家模板 ChangeSet，可按类别、状态及关键词过滤，并保留原始提案、来源、审核意见和发布修订。单项审批按本体或模板权限分别校验，高风险提案保留独立审核限制；本体批量审核继续使用预览与确认流程。专家模板在此工作区只读查看工艺白板并验证案例，编写白板只负责编辑、试算和提交。所有审批、真源应用与发布动作仅在 `/changesets` 提供。

本体与工艺白板从工作区顶部到底部铺满，左侧竖向浮动工具组提供选择、新增、搜索、筛选和更多操作。搜索与筛选按需弹出，目录及属性面板作为画布内浮层打开，不改变画布尺寸。连接状态、同步、专家模式和快捷键说明收进更多菜单；工艺模板名称与版本在画布标签及模板设置内维护。实例刷新和审核批量操作放在各自目录面板，详情区使用完整高度。
