# GHC Proxy

> 将 **GitHub Copilot（GHC）** 的模型 API 包装成统一端点，兼容 **Claude Code / Codex / OpenClaw**。
> 前端用户拿一个代理分发的 Key 即可经代理访问 GHC 的全部模型，每个用户流量严格对应一个后端 GHC 账号，
> 实现集中化的 token 计量、prompt 留存与用户行为分析。

本仓库包含完整的 Python 实现（`src/ghcproxy/`）、运维管理面板（`frontend/`，React SPA）、测试（`tests/`）
与部署清单（`deploy/`）。已通过本地与 **Azure VNet 内**的全栈部署测试，用真实 `gho_` 凭证跑通 GPT 与 Claude 调用。
所有脚本与配置均使用**占位符**，不含任何真实租户 ID、密钥、token 或客户敏感信息。

---

## 主要设计

### 解决的问题

企业希望集中管理一批 GHC 账号，对内提供一个统一、与主流 AI 编码客户端兼容的模型端点：

- 前端用户拿**代理分发的 Key** 认证，经代理访问 GHC 全部模型；
- 每个前端用户流量**只对应一个后端 GHC 账号（1:1）**，避免 GHC 风控将代理识别为「多人共用一个账号」而封号；
- 集中实现 **token 流量统计、prompt 日志留存（审计 / 数据挖掘）、用户行为分析**；
- 用 GHC 账号端点支撑 Claude Code / Codex / OpenClaw 等软件运行。

> 合规性：GitHub 官方支持将 Copilot 作为 OpenClaw 等第三方工具的模型 Provider，本用法属此范畴。
> 真正的封号风险来自「单账号被多人并发使用」，故核心约束是严格的 **用户 ↔ 账号 1:1 绑定**。

### 技术基石：两级 token 认证（按 OAuth 客户端自适应）

GHC 认证有**两种形态**，取决于账号长效凭证由哪个 OAuth 客户端签发；本服务两条路径都支持（先尝试换取，404 则回退直连）：

| 客户端类型 | 长效凭证 | 访问模型 API 的方式 |
|---|---|---|
| **编辑器 GitHub App**（如 `Iv1.b507a08c87ecfe98`） | `ghu_`（user-to-server） | **必须换取**：`GET api.github.com/copilot_internal/v2/token`（以 `Authorization: token <ghu_>` 鉴权）→ 短效 **token B**（约 25–30 分钟，带 `expires_at`），再以 token B 作 Bearer |
| **CLI OAuth App**（`Ov23...`） | `gho_` | **直接**作 Bearer（换取端点对该 token 返回 404，scope 不含 copilot） |

> 本机 Copilot CLI `1.0.61`（`gho_`/CLI 客户端）实测：换取端点返回 404，`gho_` 直连即可 200。litellm / OpenCode / copilot-api 等公开实现走的是编辑器客户端的换取路径——故本服务统一支持两者。

token 解析与缓存由 `CopilotTokenService` 负责：换取所得 token B 在过期前（默认预留 `liveness_skew_s` 秒）自动刷新；直连模式缓存判定结果避免每请求重探 404。**每账号需持久化的只有一行长效凭证**（数据库加密存储），因此**管理 N 个账号无需 N 个容器 / 文件系统隔离**；refresher 定时刷新 token B 并校验存活性，长效凭证失效则隔离待重登。

上游同时提供：`/chat/completions`（OpenAI 风格，GPT 与 Claude 均可）、`/v1/messages`（Anthropic 原生）、`/models`、`/responses`。

### 请求头构造（影响 cache 命中与异常检测）

向模型 API 发请求时，按真实编辑器客户端构造完整身份头（对照 litellm `get_copilot_default_headers` 与实测 CLI 流量），错误构造会降低 prompt cache 命中率、或被判为异常客户端：

- 静态身份：`Copilot-Integration-Id`、`Editor-Version`、`Editor-Plugin-Version`、`X-GitHub-Api-Version`、`OpenAI-Intent`、`User-Agent`（全部可配，见 `UpstreamConfig`）；
- 每请求唯一 `X-Request-Id`（实测不影响 cache key）；
- **动态 `X-Initiator`**：消息含 `assistant`/`tool` 角色判为 `agent`，否则 `user`——GHC 据此区分人工与 agent 跟进调用，硬编码 `user` 会放大 premium request 计费；
- **`Copilot-Vision-Request: true`**：消息含图片（OpenAI `image_url` / Anthropic `image` 块）时附加。

### 总体架构

采用「**模块化单体 + 两类 K8s 工作负载**」：同一镜像，按 `ROLE` 启动不同进程。

```
 Claude Code ─┐            ┌──────────── GHC Proxy 镜像 ─────────────┐
 Codex        ┼─ proxy ───▶│ [ROLE=proxy · 无状态 · HPA]            │──▶ api.<plan>.githubcopilot.com
 OpenClaw     ┘   key      │  auth→binding(1:1)→forward→usage/log   │   /chat/completions /v1/messages
                           │ [ROLE=refresher · 抢锁单例]            │   /models /responses
                           │  存活性校验 · Device Flow · 健康隔离    │
                           └──────┬─────────────┬─────────────┬─────┘
                              PostgreSQL       Redis         Kafka
                          (账号/用户/绑定/  (Key 短缓存/      (prompt 日志/
                               Key/用量聚合)     账号锁)          用量计量/审计)
```

- **proxy 角色**：处理前端流量，完全无状态（状态在 PG/Redis），可任意水平扩展；
- **refresher 角色**：存活性校验、引导 Device Flow 登录、账号健康检查与隔离，靠 Redis 分布式锁做到每账号单写者。

### 关键能力

- **1:1 粘性绑定**：`bindings` 表对 `user_id` 与 `account_id` 双 `UNIQUE`，从存储层杜绝一账号绑定多用户；空闲账号用 `FOR UPDATE SKIP LOCKED` 原子分配。
- **两级 token 自适应**：编辑器客户端（`ghu_`）经 `copilot_internal/v2/token` 换取短效 token B 并按 `expires_at` 自动刷新；CLI 客户端（`gho_`）直连。refresher 定时刷新/校验，失效自动隔离待重登；换取端点 5xx/429/网络抖动视为暂态，不隔离。
- **自动改路由容错**：账号登录失效（401/403）时隔离该账号，从空闲池重新绑定 healthy 账号并重试本次请求（至多一次，含流式请求）。
- **协议兼容**：同时兼容 Anthropic Messages（Claude Code）与 OpenAI 风格（Codex/OpenClaw），含 SSE 流式透传。
- **可观测性**：prompt 请求、用量、审计事件投递 Kafka；buffered 响应同步记录响应体，流式响应不缓存完整输出；指标暴露 Prometheus（`/metrics`）。
- **运维管理面板**：独立 React SPA，经扩展后的 admin JSON API 提供账号绑定可视化、token 用量四维分析、用户与 Key 全生命周期管理、后端账号状态管理（详见下节）。

---

## 运维管理面板（Admin Console）

面向**运维操作人员**的独立单页应用（React + Vite + TypeScript + Recharts），与后端**前后端分离、独立构建部署**。
面板通过 nginx 把 `/admin/*` 反代到 proxy 的 admin API，浏览器同源访问；操作人员登录时输入
`GHCPROXY_ADMIN_TOKEN`，前端持该 token 以 `X-Admin-Token` 头调用 admin API（沿用现有静态 admin token 机制，
不新增操作员账号体系）。

四类能力：

| 页面 | 能力 | 后端端点 |
|---|---|---|
| **Usage** | token 用量四维可视化：按时间趋势（折线）、按模型占比（饼图）、按后端账号分布（柱状）、按前端用户排行（表格）。读取 `usage_rollup`，**不读 prompt 明细**。 | `GET /admin/usage/{timeseries,by-user,by-account,by-model}?from&to` |
| **Bindings** | 查看「用户 ↔ 账号」1:1 粘性绑定（状态/绑定时间/最近活跃），支持手动解绑使账号回 idle。 | `GET /admin/bindings`、`POST /admin/bindings/{user_id}/release` |
| **Users & Keys** | 列出用户与名下 Key（**仅元数据，不回显明文/哈希**）；创建用户并签发默认 Key；新增 / 轮换 / 吊销 Key；停用 / 启用用户。明文 Key **仅签发瞬间一次性返回**。 | `GET /admin/users`、`PATCH /admin/users/{id}`、`POST /admin/users/{id}/keys`、`POST /admin/keys/{id}/{rotate,revoke}` |
| **Accounts** | 查看账号池（login/plan/api_base/status/last_error/last_seen/下次刷新）；变更状态（disable / 解除 quarantine 回 idle）；对失效账号发起 Device Flow 重登并展示 `user_code`/`verification_uri`。 | `GET /admin/accounts`、`PATCH /admin/accounts/{id}/status`、`POST /admin/accounts/{login}/login/{start,poll}` |

> 所有新增端点继续走 `require_admin`（静态 admin token）鉴权。Key 一律只存哈希（`api_keys.key_hash`），
> 面板任何场景都不回显明文或哈希，明文仅在签发瞬间返回一次。

### 本地开发面板

```bash
cd frontend
npm install
npm run dev          # Vite dev server (http://localhost:5173)，自动把 /admin 反代到 localhost:8080
# 或构建生产产物：
npm run build        # 产物在 frontend/dist/（已 gitignore）
```

docker-compose 已内置 `console` 服务（见下方快速开始），起栈后浏览 `http://localhost:8081` 即可。

---

## 技术栈

- **语言**：Python 3.11+（FastAPI + httpx + asyncpg）
- **数据库**：PostgreSQL（持久状态）、Redis（缓存、会话、分布式锁）
- **消息队列**：Kafka（prompt 日志、用量计量、审计；本项目仅生产，不消费）
- **部署**：Kubernetes（无状态 proxy 层 + HPA，refresher 抢锁单例）；本地/单机用 docker-compose

---

## 仓库结构

```
.
├── README.md                 # 本文件
├── ghc-proxy-design.md       # 详细设计文档（原理 / 架构 / 时序 / 数据模型）
├── todo.md                   # 原始需求背景与任务说明 + 交付状态
├── CLAUDE.md                 # 面向 AI 助手的项目概览
├── pyproject.toml            # 包定义与依赖
├── Dockerfile                # 后端单镜像（ROLE=proxy | refresher）
├── src/ghcproxy/             # 后端实现（见 ghc-proxy-design.md §10 索引）
├── frontend/                 # 运维管理面板（React + Vite + TS + Recharts）SPA
│   ├── Dockerfile            # 构建 SPA -> nginx 托管（独立镜像）
│   ├── nginx.conf            # SPA history fallback + /admin 反向代理
│   └── src/                  # api.ts + pages/{Login,Usage,Bindings,Users,Accounts}
├── tests/                    # pytest 单元 + 接口测试
├── deploy/
│   ├── docker/docker-compose.yaml   # 全栈：PG/Redis/Kafka/proxy/refresher/console
│   └── k8s/                         # namespace / config+secret / proxy / refresher / console
└── examples/                 # 早期 TypeScript 说明性骨架（仅供对照）
```

---

## 快速开始（本地全栈）

```bash
# 1) 起全栈（PostgreSQL + Redis + Kafka + proxy + refresher + console）
cd deploy/docker
DATA_KEY_B64=$(python3 -c 'import base64,os;print(base64.b64encode(os.urandom(32)).decode())') \
ADMIN_TOKEN=my_admin_token \
docker compose up -d --build
# 管理面板：浏览 http://localhost:8081，用 my_admin_token 登录

# 2) 导入一个 GHC 账号（长效凭证由操作人员经 Device Flow 取得；这里直接导入）
#    oauth_token 可为 gho_（CLI 客户端，直连）或 ghu_（编辑器客户端，自动换取 token B）
curl -X POST localhost:8080/admin/accounts -H "x-admin-token: my_admin_token" \
  -H 'content-type: application/json' \
  -d '{"login":"<github-login>","oauth_token":"<gho_或ghu_token>","plan":"enterprise"}'

# 3) 创建用户并签发代理 Key（明文 Key 仅此一次返回）
curl -X POST localhost:8080/admin/users -H "x-admin-token: my_admin_token" \
  -H 'content-type: application/json' -d '{"external_id":"alice"}'

# 4) 像用 OpenAI / Anthropic 一样调用
curl localhost:8080/v1/chat/completions -H "authorization: Bearer ghcp_<key>" \
  -H 'content-type: application/json' \
  -d '{"model":"gpt-4o-2024-11-20","messages":[{"role":"user","content":"hi"}]}'

curl localhost:8080/v1/messages -H "x-api-key: ghcp_<key>" \
  -H 'content-type: application/json' \
  -d '{"model":"claude-sonnet-4.5","max_tokens":50,"messages":[{"role":"user","content":"hi"}]}'
```

### 客户端接入

| 客户端 | 配置 |
|---|---|
| **Claude Code** | `ANTHROPIC_BASE_URL=http://<proxy>/  ANTHROPIC_API_KEY=ghcp_<key>`（走 `/v1/messages`） |
| **Codex / OpenAI 兼容** | `base_url=http://<proxy>/v1  api_key=ghcp_<key>`（走 `/chat/completions`、`/responses`） |
| **OpenClaw** | OpenAI 兼容 provider，指向 `http://<proxy>/v1` |

### 运行测试

```bash
pip install -e ".[dev]"
pytest -q          # 140 项单元/接口测试，无需外部依赖（含 token service 两级换取 / 请求头派生 / 面板 admin 端点）

# 面板前端构建（需 Node 18+）
cd frontend && npm install && npm run build
```

---

## 测试与部署状态

| 项 | 状态 | 说明 |
|---|---|---|
| 可行性（凭证机制） | ✅ | 两级 token 自适应：实测 `gho_`（CLI 客户端）换取端点 404→直连作 Bearer，GPT/Claude/models 均 200；编辑器客户端 `ghu_` 换取 token B 路径由 token service 实现并单测覆盖。 |
| 单元 + 接口测试 | ✅ | `pytest` 140 项全绿（crypto / 绑定 / 转发改路由 / 流式错误处理 / Device Flow / 用量解析 / 鉴权 / 配置 / 接口 / **token service 两级换取** / **请求头动态派生** / **upstream 接线** / 面板 admin 端点）。 |
| 面板前端构建 | ✅ | `npm run build` 通过（tsc 严格模式 + Vite 产物）。 |
| 本地全栈集成 | ✅ | docker-compose 全栈，真实长效凭证经 token service 解析后跑通 GPT + Claude（OpenAI 与 Anthropic 两种协议）+ 流式 + 用量入库 + Kafka 事件；prompt cache 命中实测（`cached_tokens`>0）。 |
| Azure 云端部署 | ✅ | `rg-dev2`（japaneast）VM 全栈（含 console 面板）；从 `vnet-dev-jpe` 经 VNet Peering 内网调用 GPT/Claude 通过；面板经 admin token 调通各端点。 |

> Kafka 仅做日志/用量/审计的**生产**端；消费与数据分析不属于本项目范围（见 `todo.md`）。

---

## 延伸阅读

- 设计文档：[`ghc-proxy-design.md`](./ghc-proxy-design.md)
- 需求背景与交付状态：[`todo.md`](./todo.md)

## License

[MIT](./LICENSE)
