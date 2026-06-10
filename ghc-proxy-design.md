# GHC Proxy 设计文档

> 将 GitHub Copilot（以下简称 GHC）的模型 API 包装为统一端点，兼容 Claude Code / Codex / OpenClaw。
> 本文档描述其原理、架构、服务调用关系与关键时序。所有示例代码、配置均使用占位符，不含任何真实租户 ID、密钥、token 或客户敏感信息。

---

## 1. 背景与目标

### 1.1 要解决的问题

企业希望集中管理一批 GHC 账号，对内提供一个统一的、与主流 AI 编码客户端兼容的模型端点，达到：

- 前端用户拿一个**代理分发的 Key** 认证，经代理访问 GHC 的全部模型；
- 每个前端用户的流量**只对应一个后端 GHC 账号**（1:1），避免 GHC 官方将代理识别为「多人共用一个账号」而触发封号；
- 集中实现 **token 流量统计、prompt 日志留存（审计 / 数据挖掘）、用户行为分析**；
- 用 GHC 账号的模型端点支撑 Claude Code / Codex / OpenClaw 等软件运行。

### 1.2 合规性说明

GitHub 官方文档明确支持将 Copilot 作为 OpenClaw 等第三方工具的模型 Provider（见 OpenClaw「Three ways to use Copilot」）。本服务的用法属于此类受支持范畴。**真正的封号风险来自「单账号被多人并发使用」的模式识别**，因此本设计的核心约束是严格的「**前端用户 ↔ 后端 GHC 账号 1:1 绑定**」。

### 1.3 设计目标

| 目标 | 说明 |
|---|---|
| 高可用 | 无单点；代理层无状态、可水平扩展；凭证刷新有主备/抢锁机制。 |
| 水平扩展 | 部署于 K8s，代理层按 QPS / 并发用 HPA 扩缩容。 |
| 凭证长期保活 | 把 GHC 登录凭证存数据库，定时主动刷新，免去 N 个容器隔离 N 个账号。 |
| 可观测 | 统一 token 计量、prompt 留存、用户分析，基于 Kafka。 |
| 协议兼容 | 同时兼容 OpenAI 风格（Codex/OpenClaw）与 Anthropic Messages（Claude Code）。 |

---

## 2. 技术可行性：GHC 凭证机制（已在本机验证）

> 本节结论已在本工作区所在服务器上验证（Copilot CLI `1.0.61`，账号为企业版），并与开源代理实现（如 `ericc-ch/copilot-api`）交叉核对一致。

### 2.1 双 Token 模型（设计的技术基石）

GHC 的认证是**两段式**的：

| Token | 来源 | 形态 | 生命周期 | 用途 |
|---|---|---|---|---|
| **OAuth Token（长效）** | GitHub Device Flow | `gho_` 前缀，40 字符 | 长期有效（直到撤销/登出） | 持久身份凭证，用于换取下方短效 token |
| **Copilot Token（短效）** | 用 OAuth token 调用换取接口 | 形如 `tid=...;exp=...;refresh_in=...;...` 的不透明串 | 约 30 分钟 | 实际请求模型 API 时的 Bearer |

**关键推论**：每个账号需要**持久化的只有那个 `gho_` OAuth token（一行数据库记录）**。短效 Copilot token 是一次无状态 HTTP 调用换来的、可随时重建的缓存值。
因此**管理 N 个账号无需 N 个容器 / 文件系统隔离**——只要在数据库里存 N 行加密的 `gho_` token 即可。这是整个方案成立的根基。

### 2.2 本机凭证落盘位置与结构

GHC CLI 把 OAuth token 落盘在 `~/.copilot/config.json`（JSONC 格式，含 `//` 注释），结构（**值已脱敏**）：

```jsonc
// User settings
{
  "firstLaunchAt": "<ISO8601 时间戳>",
  "copilotTokens": {
    // key 为 "<host>:<login>"，value 为 gho_ 开头的 40 字符 OAuth token
    "https://github.com:<LOGIN>": "<GHO_OAUTH_TOKEN, 40 chars>"
  },
  "lastLoggedInUser": { "host": "https://github.com", "login": "<LOGIN>" },
  "loggedInUsers": [ { "host": "https://github.com", "login": "<LOGIN>" } ],
  "staff": false
}
```

> 提取方式：解析该 JSONC（去除注释后按 JSON 解析），读取 `copilotTokens` 下的值即为 `gho_` token。GHC Proxy **不依赖**继续使用本地 CLI——拿到 `gho_` token 后，后续全部走 HTTP，由代理自己完成 token 交换与请求。

### 2.3 Device Flow 登录（操作人员授权）

标准 GitHub OAuth Device Flow，三个端点：

1. `POST https://github.com/login/device/code`（携带 `client_id`、`scope`）→ 返回 `device_code`、`user_code`、`verification_uri`、`interval`、`expires_in`；
2. 把 `verification_uri`（`https://github.com/login/device`）与 `user_code`（形如 `XXXX-XXXX`）**吐给操作人员/前端**，由真人在浏览器完成登录与设备授权；
3. 后台按 `interval` 轮询 `POST https://github.com/login/oauth/access_token`，授权完成后即返回 `gho_` 形态的 `access_token`。

> `client_id` 使用 GHC 客户端公开的 OAuth App ID（示例占位：`Iv1.<CLIENT_ID>`；公开实现中常见 VS Code Copilot 的 `Iv1.b507a08c87ecfe98`）。该值是公开客户端标识，非密钥。

### 2.4 Token 交换与刷新

用 `gho_` token 换短效 Copilot token：

```
GET https://api.github.com/copilot_internal/v2/token
Authorization: token <GHO_OAUTH_TOKEN>
```

返回（节选关键字段）：

```jsonc
{
  "token": "tid=...;exp=...;refresh_in=...;...",  // 实际用于模型 API 的 Bearer
  "expires_at": 1781096400,                          // 过期时间（epoch 秒）
  "refresh_in": 1500,                                // 建议在此秒数后刷新（早于 expires_at）
  "endpoints": { "api": "https://api.<PLAN>.githubcopilot.com" },
  "chat_enabled": true
}
```

**刷新策略**：在 `now + refresh_in` 时刻（即过期前的安全窗口）主动刷新，而非等到 `expires_at`。`endpoints.api` 给出该账号对应的模型 API base（个人版 `api.individual.…`，商业版 `api.business.…`，企业版 `api.enterprise.…`，本机为企业版）。

### 2.5 模型 API 调用

```
POST {endpoints.api}/chat/completions      # OpenAI 风格对话补全
POST {endpoints.api}/responses             # Responses API（Codex 等）
GET  {endpoints.api}/models                # 模型列表
Authorization: Bearer <COPILOT_TOKEN>
Editor-Version: <e.g. vscode/1.95.0>
Editor-Plugin-Version: <e.g. copilot-chat/0.26.7>
Copilot-Integration-Id: vscode-chat
X-GitHub-Api-Version: 2025-04-01
Copilot-Vision-Request: true|false
```

> 上述 header 是 GHC 侧校验「请求来自合法客户端」的关键，缺失会被拒。具体取值随客户端版本演进，需在配置中可调。

### 2.6 需加入防火墙/代理白名单的域名

依据 GitHub 官方 *Copilot allowlist reference*（HTTPS / 443）：

- **认证 / 设备流**：`https://github.com/login/*`
- **用户与凭证交换**：`https://api.github.com/user`、`https://api.github.com/copilot_internal/*`
- **模型 API**：`https://*.githubcopilot.com/*`（含 `*.individual.` / `*.business.` / `*.enterprise.githubcopilot.com`）、`https://copilot-proxy.githubusercontent.com`、`https://origin-tracker.githubusercontent.com`
- **遥测（可选）**：`https://copilot-telemetry.githubusercontent.com/telemetry`、`https://collector.github.com/*`
- **实验配置（可选）**：`https://default.exp-tas.com`

---

## 3. 总体架构

### 3.1 运行形态

采用「**模块化单体 + 两类 K8s 工作负载**」：同一镜像，按角色启动不同进程。既满足高可用与水平扩展，又避免过早微服务化的运维成本。

```
                          ┌──────────────────────────────────────────────────────┐
 Claude Code ─┐           │                     GHC Proxy 镜像                     │
 Codex        ┼─ proxy ──▶│  [proxy 角色 · 无状态 · HPA 扩缩]                       │
 OpenClaw     ┘  key      │   Ingress Adapters → Router/Binding → Credential Svc    │──▶ api.github.com
                          │   (anthropic ⇄ openai)  (1:1 粘性)   (token 交换/缓存)  │   /copilot_internal/v2/token
                          │                                                        │──▶ api.<plan>.githubcopilot.com
                          │  [refresher 角色 · 抢锁单例]                            │   /chat/completions /responses /models
                          │   定时刷新 · Device Flow 引导 · 健康检查/隔离          │
                          └───────┬───────────────┬────────────────┬──────────────┘
                                  │               │                │
                            PostgreSQL          Redis            Kafka
                       (accounts/users/     (copilot_token     (prompt 日志,
                        bindings/creds/      缓存, binding      usage 计量,
                        usage 聚合)          缓存, device       audit)
                                             session, 账号锁)
```

- **proxy 角色**：处理前端流量，完全无状态（状态在 PG/Redis），可任意水平扩展；
- **refresher 角色**：负责「主动刷新 token、Device Flow 登录引导、账号健康检查与隔离」等周期/异步任务，靠 Redis 分布式锁做到**每账号单写者**，避免多副本竞争。

### 3.2 模块职责

| 模块 | 职责 | 依赖 |
|---|---|---|
| **Ingress Adapters** | 终结前端协议；将 Anthropic Messages 与 OpenAI 风格请求归一化为内部 canonical 请求；流式响应回译。提供 `/v1/messages`（Anthropic）与 `/v1/chat/completions`、`/v1/responses`、`/v1/models`（OpenAI 直通）。 | — |
| **Auth & Key** | 校验前端 `proxy key`，解析出 `user_id`、配额、限流策略。 | PG/Redis |
| **Router / Binding** | 维护「user ↔ account」1:1 粘性绑定；绑定缺失或账号不健康时分配空闲账号；并发上限（每账号至多 1 个活跃用户）。 | PG/Redis |
| **Credential Svc** | 用账号 `gho_` token 换/取短效 Copilot token（Redis 缓存，按 `expires_at` 设 TTL）；附加客户端 header；调用模型 API；处理上游 401/限流。 | PG/Redis |
| **Refresher Worker** | 周期扫描即将到期的 token 并提前刷新；驱动 Device Flow；账号健康探测与隔离/恢复。 | PG/Redis |
| **Admin API** | 账号导入、发起登录、查看账号/绑定/配额、隔离/解隔离、用量报表。 | PG |
| **Observability** | 把 prompt、用量、审计事件投递 Kafka；指标暴露 Prometheus。 | Kafka |

---

## 4. 数据流与关键时序

### 4.1 在线推理请求（命中已绑定且健康的账号）

```
Client                Proxy(Ingress→Auth→Router→Cred)         Redis        GHC
  │  POST /v1/messages (Bearer proxy_key)  │                    │            │
  ├───────────────────────────────────────▶                    │            │
  │                       校验 key → user_id│                    │            │
  │                       查 binding(user)  ├──读缓存───────────▶│            │
  │                          ◀── account_id ┤◀───────────────────┤            │
  │                       取 copilot_token  ├──读缓存───────────▶│            │
  │                                         │  (miss → 见 4.2)   │            │
  │      Anthropic→canonical→OpenAI 请求    │                    │            │
  │                                         ├── POST /chat/completions (Bearer copilot_token) ─▶
  │                                         │            (SSE 流式)            ◀──────────────────
  │   ◀── OpenAI→Anthropic 流式回译 (SSE) ──┤                    │            │
  │                                         ├──▶ Kafka: prompt 日志 / usage    │
```

要点：热路径**只读 Redis**（binding + copilot_token 均命中缓存），无同步 DB 写；prompt/usage **异步**投 Kafka，不阻塞响应。

### 4.2 Token 缓存未命中 / 即将过期（Credential Svc + Refresher）

```
                Credential Svc                  Redis                 GHC(api.github.com)
 cache miss ───────────────────────────────────▶ GET copilot_token:{account}
                                                  (nil)
            尝试获取每账号锁 ─────────────────────▶ SET lock:{account} NX EX
            GET /copilot_internal/v2/token (token <gho_>) ───────────────────────────▶
            ◀── {token, expires_at, refresh_in, endpoints} ─────────────────────────
            写缓存 SET copilot_token:{account} EX=(expires_at-now-skew) ─▶
            记录 refresh_at=now+refresh_in 到 PG ─▶
            释放锁
```

Refresher Worker 独立地周期扫描 `refresh_at <= now` 的账号并提前刷新，保证「用户长期不访问也不掉线」。

### 4.3 账号登录失效 → 自动改路由（核心容错）

```
Cred Svc ── /chat/completions ─▶ GHC
        ◀── 401 / token_expired / login_required ──┐
        │                                           │
        │ 1) 标记 account 为 quarantined（PG）+ 发告警(Kafka)
        │ 2) Router: 解绑 user，从空闲池挑一个 healthy account 重新 1:1 绑定
        │ 3) 用新账号重试本次请求（至多 1 次）
        │ 4) 旧账号进入「待操作人员重新登录」队列；Refresher 持续探测
        ▼
   返回成功响应 / 或（无空闲账号时）503 + Retry-After
```

> 「主动刷新」让登出尽量不发生；「自动改路由」是兜底。两者叠加实现前端无感的长期保活。

### 4.4 Device Flow 账号上线（操作人员）

```
Operator/Admin UI        Admin API / Refresher           GHC
  │ 发起登录(account)  ──▶ POST /login/device/code ──────▶
  │                    ◀── user_code, verification_uri ──
  │ ◀── 展示「访问 github.com/login/device 输入 XXXX-XXXX」┤
  │ (真人浏览器完成授权)                                   │
  │                       轮询 /login/oauth/access_token ─▶
  │                    ◀── gho_ access_token ─────────────
  │                       加密存 PG(accounts.oauth_token)  │
  │                       account 置为 healthy/idle        │
```

---

## 5. 数据模型（PostgreSQL）

```sql
-- 后端 GHC 账号池
accounts(
  id, login, host,
  oauth_token_enc BYTEA,         -- gho_ token，应用层 AEAD 加密（KMS/信封加密）
  plan TEXT,                     -- individual|business|enterprise
  api_base TEXT,                 -- endpoints.api，登录/刷新时写入
  status TEXT,                   -- idle|bound|quarantined|logging_in|disabled
  refresh_at TIMESTAMPTZ,        -- 下次主动刷新时间
  last_error TEXT, last_seen_at TIMESTAMPTZ,
  created_at, updated_at
)
-- 前端用户
users(id, external_id, display_name, status, created_at)
-- 前端 Key（哈希存储）
api_keys(id, user_id, key_hash, name, scopes, rate_limit, status, created_at, last_used_at)
-- 1:1 粘性绑定（唯一约束保证双向唯一）
bindings(
  user_id UNIQUE, account_id UNIQUE,   -- 双 UNIQUE = 严格 1:1
  bound_at, last_active_at, status
)
-- Device Flow 进行中的会话
device_sessions(id, account_id, device_code_enc, user_code, verification_uri,
                interval_s, expires_at, status, created_at)
-- 用量聚合（明细走 Kafka→数仓）
usage_rollup(user_id, account_id, day, model, prompt_tokens, completion_tokens,
             requests, PRIMARY KEY(user_id, day, model))
```

> Redis 键：`copilot_token:{account_id}`（短效 token，TTL=过期前）、`binding:{user_id}`（绑定缓存）、`lock:account:{account_id}`（刷新/分配互斥）、`ratelimit:{key_id}`（限流计数）。
> 加密：`oauth_token_enc` 用应用层 AEAD（如 AES-GCM），数据密钥经 KMS 信封加密；明文 token 绝不入库、不进日志。

---

## 6. 1:1 绑定与路由策略

- **绑定唯一性**：`bindings` 表对 `user_id` 与 `account_id` 双 `UNIQUE`，从存储层面杜绝「一个账号绑定多个用户」。
- **粘性**：同一 user 后续请求始终命中同一 account，行为特征稳定，降低风控触发概率。
- **分配**：新 user 或原账号失效时，从 `status=idle` 池中**原子地**（`SELECT … FOR UPDATE SKIP LOCKED` + 唯一约束）挑选一个账号绑定。
- **容量约束**：活跃用户数 ≤ 健康账号数。无空闲账号时返回 `503 + Retry-After`，并触发告警提示操作人员补充/恢复账号。
- **回收**：长期不活跃的 user 可释放绑定，把账号还回 idle 池（可配置 TTL）。

---

## 7. 可观测性、计量与审计（Kafka）

- **Topics**：`ghcproxy.prompts`（完整请求/响应，供审计与数据挖掘）、`ghcproxy.usage`（每请求 token 计量）、`ghcproxy.audit`（绑定变更、账号隔离、登录事件）。
- **消费侧**：流式落数仓（用户行为分析）、聚合写回 `usage_rollup`、告警规则（隔离账号数、503 率、刷新失败率）。
- **隐私**：prompt 留存需符合企业合规；建议对 topic 做加密与访问控制，必要时对敏感字段脱敏。
- **指标（Prometheus）**：QPS、上游时延、token 命中率、刷新成功率、隔离账号数、空闲账号数、绑定饱和度。

---

## 8. 高可用与水平扩展

| 维度 | 措施 |
|---|---|
| proxy 层 | 无状态，多副本 + HPA（按 CPU/并发）；K8s Service 负载均衡。 |
| refresher | 多副本但靠 Redis 锁选主/每账号单写者；避免重复刷新与竞争。 |
| 状态层 | PG 主从 + 连接池；Redis 哨兵/集群；Kafka 多分区多副本。 |
| 优雅退出 | proxy 收到 SIGTERM 后 drain 在途流式请求再退出。 |
| 限流/熔断 | 每 key 限流；对 GHC 上游 5xx/429 退避重试 + 熔断。 |
| 密钥安全 | `gho_` 信封加密；Secret 经 K8s Secret/External Secrets 注入。 |

---

## 9. 风险与缓解

| 风险 | 缓解 |
|---|---|
| GHC 内部接口变更（非官方稳定 API） | header / 端点 / client_id 全部走配置；集成测试探测；版本随客户端升级。 |
| 风控封号 | 严格 1:1 绑定 + 粘性；主动刷新减少异常登录；监控隔离率。 |
| token 泄露 | 信封加密、最小权限、不落明文日志、定期轮换。 |
| 空闲账号耗尽 | 容量告警；账号池水位监控；操作人员补号 SOP。 |
| prompt 合规 | topic 加密 + 访问控制 + 可配置脱敏 + 留存期策略。 |

---

## 10. 部署与示例代码索引

示例代码与配置位于 `examples/`（**均为占位符，无真实密钥**）：

```
examples/
├── src/
│   ├── common/config.ts            # 配置/环境变量（端点、header、client_id 占位）
│   ├── common/crypto.ts            # gho_ token 的 AEAD 信封加密示例
│   ├── credential/deviceFlow.ts    # Device Flow 登录（吐 user_code 给前端）
│   ├── credential/tokenExchange.ts # gho_ → copilot token 交换 + Redis 缓存
│   ├── credential/refresher.ts     # 主动刷新 worker（抢锁单例）
│   ├── credential/refresherMain.ts # refresher 角色进程入口（装配 PG/Redis + AccountRepo）
│   ├── router/binding.ts           # 1:1 粘性绑定与空闲账号分配
│   ├── proxy/server.ts             # buildServer 工厂：Auth + 路由 + 转发骨架
│   └── proxy/anthropicAdapter.ts   # Anthropic Messages ⇄ OpenAI 转换骨架
├── db/schema.sql                   # PostgreSQL DDL
├── config/ghc-proxy.example.yaml   # 运行配置示例
├── docker/docker-compose.yaml      # 本地 PG/Redis/Kafka 依赖
└── k8s/                            # proxy Deployment + refresher Deployment + HPA + Secret 占位
```

> 示例以骨架/片段为主，展示关键机制（token 交换、刷新、绑定、协议转换、部署），非完整可运行产品。落地时需补全错误处理、重试、测试与安全加固。
