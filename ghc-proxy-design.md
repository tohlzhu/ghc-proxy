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

## 2. 技术可行性：GHC 凭证机制（已在本机 + 云端实测验证）

> 本节结论已通过 **mitmproxy 抓取本机 Copilot CLI `1.0.61`（企业版账号）真实流量**验证，并在本地与 Azure 云端部署中用真实 `gho_` 凭证完整跑通 GPT / Claude 调用。

### 2.1 凭证模型（设计的技术基石 —— 实测修正）

> ⚠️ **重要修正**：早期设想 GHC 用「`gho_` 长效 → 短效 copilot token」两段式换取。抓包实测表明：**当前独立 Copilot CLI 直接用 `gho_` token 作为模型 API 的 Bearer**，并不调用 `copilot_internal/v2/token` 换取短效 token（该接口对 CLI 颁发的 `gho_` token 返回 404，因其 scope 为 `gist, read:org, read:user, repo`，不含 `copilot`）。当前实现采用**直接 Bearer**路径。

| Token | 来源 | 形态 | 生命周期 | 用途 |
|---|---|---|---|---|
| **OAuth Token（`gho_`）** | GitHub Device Flow | `gho_` 前缀，40 字符 | 长期有效（直到撤销/登出） | **直接**作为模型 API 的 Bearer；持久身份凭证 |

**关键推论**：每个账号需要**持久化的只有那一行 `gho_` token**。
因此**管理 N 个账号无需 N 个容器 / 文件系统隔离**——只要在数据库里存 N 行加密的 `gho_` token 即可。这是整个方案成立的根基，已实测成立。

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

> 提取方式：解析该 JSONC（去除注释后按 JSON 解析），读取 `copilotTokens` 下的值即为 `gho_` token。GHC Proxy **不依赖**继续使用本地 CLI——拿到 `gho_` token 后，后续全部走 HTTP，由代理自己直接携带 Bearer 调用模型 API。

### 2.3 Device Flow 登录（操作人员授权）

标准 GitHub OAuth Device Flow，三个端点：

1. `POST https://github.com/login/device/code`（携带 `client_id`、`scope`）→ 返回 `device_code`、`user_code`、`verification_uri`、`interval`、`expires_in`；
2. 把 `verification_uri`（`https://github.com/login/device`）与 `user_code`（形如 `XXXX-XXXX`）**吐给操作人员/前端**，由真人在浏览器完成登录与设备授权；
3. 后台按 `interval` 轮询 `POST https://github.com/login/oauth/access_token`，授权完成后即返回 `gho_` 形态的 `access_token`。

> `client_id` 使用 GHC 客户端公开的 OAuth App ID（示例占位：`Iv1.<CLIENT_ID>`；公开实现中常见 VS Code Copilot 的 `Iv1.b507a08c87ecfe98`）。该值是公开客户端标识，非密钥。

### 2.4 凭证保活与刷新（实测修正）

由于 `gho_` token 是长效凭证（直到登出/撤销），**无需短效 token 的定期换取**。因此 refresher 的职责变为「**主动存活性校验**」：周期性用每个账号的 `gho_` 调用 `GET {api_base}/models`：

- **200** → 账号健康，推后 `refresh_at`（默认 30 分钟后再查）；
- **401 / 403 login_required** → 登录失效，隔离账号（`quarantined`），等待操作人员重新 Device Flow 授权；
- **网络抖动 / 5xx** → 视为暂态，不隔离。

每账号校验由 Redis 锁（`lock:account:{id}`）保证多副本下单写者。

### 2.5 模型 API 调用（实测取值）

```
POST {api_base}/chat/completions      # OpenAI 风格（GPT 与 Claude 均可）
POST {api_base}/responses             # Responses API（Codex 等）
POST {api_base}/v1/messages           # Anthropic Messages（Claude Code 原生）
GET  {api_base}/models                # 模型列表（实测 38 个：gpt-5.x / gpt-4o / claude-opus-4.x / sonnet / haiku）
Authorization: Bearer <GHO_OAUTH_TOKEN>
Copilot-Integration-Id: copilot-developer-cli
Editor-Version: copilot/1.0.61
X-GitHub-Api-Version: 2026-06-01
X-Initiator: user
anthropic-version: 2023-06-01            # 仅 /v1/messages 需要
```

> 上述 header 是 GHC 侧校验「请求来自合法客户端」的关键，缺失会被拒（如错误的 `Copilot-Integration-Id` / `Editor-Version`）。取值随客户端版本演进，故在配置中全部可调（见 `src/ghcproxy/common/config.py` 的 `UpstreamConfig`）。`api_base` 按账号套餐而定（企业版 `api.enterprise.githubcopilot.com`）。

### 2.6 需加入防火墙/代理白名单的域名

依据 GitHub 官方 *Copilot allowlist reference*（HTTPS / 443）：

- **认证 / 设备流**：`https://github.com/login/*`
- **用户信息 / 兼容性探测（可选）**：`https://api.github.com/user`；当前实现不调用 `copilot_internal/*`
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
 OpenClaw     ┘  key      │   Ingress Adapters → Router/Binding → Credential Svc    │──▶ api.<plan>.githubcopilot.com
                          │   (协议直通/透传)      (1:1 粘性)   (gho_ Bearer)      │   /chat/completions /responses /models
                          │                                                        │
                          │  [refresher 角色 · 抢锁单例]                            │
                          │   存活性校验 · 健康检查/隔离                            │
                          └───────┬───────────────┬────────────────┬──────────────┘
                                  │               │                │
                            PostgreSQL          Redis            Kafka
                       (accounts/users/     (key 缓存,账号锁    (prompt 日志,
                        bindings/creds/      与健康锁)          usage 计量,
                        usage 聚合)                             audit)
```

- **proxy 角色**：处理前端流量，完全无状态（状态在 PG/Redis），可任意水平扩展；
- **refresher 角色**：负责「主动存活性校验、账号健康检查与隔离」等周期任务，靠 Redis 分布式锁做到**每账号单写者**，避免多副本竞争；Device Flow 由 Admin API 发起并轮询完成。

### 3.2 模块职责

| 模块 | 职责 | 依赖 |
|---|---|---|
| **Ingress Adapters** | 终结前端协议；将 Anthropic Messages 与 OpenAI 风格请求归一化为内部 canonical 请求；流式响应回译。提供 `/v1/messages`（Anthropic）与 `/v1/chat/completions`、`/v1/responses`、`/v1/models`（OpenAI 直通）。 | — |
| **Auth & Key** | 校验前端 `proxy key`，解析出 `user_id`；Key 查找结果走 Redis 短缓存。 | PG/Redis |
| **Router / Binding** | 维护「user ↔ account」1:1 粘性绑定；绑定缺失或账号不健康时分配空闲账号；并发上限（每账号至多 1 个活跃用户）。 | PG/Redis |
| **Credential Svc** | 解密账号 `gho_` token，附加客户端 header，以 Bearer 直接调用模型 API；处理上游 401/限流。 | PG/Redis |
| **Refresher Worker** | 周期扫描到期账号并调用 `/models` 做存活性校验；账号健康探测与隔离。 | PG/Redis |
| **Admin API** | 账号导入、发起登录、轮询完成 Device Flow、查看账号、签发 Key。 | PG |
| **Observability** | 把 prompt、用量、审计事件投递 Kafka；指标暴露 Prometheus。 | Kafka |

---

## 4. 数据流与关键时序

### 4.1 在线推理请求（命中已绑定且健康的账号）

```
Client                Proxy(Ingress→Auth→Router→Cred)         Redis/PG     GHC
  │  POST /v1/messages (Bearer proxy_key)  │                    │            │
  ├───────────────────────────────────────▶                    │            │
  │                       校验 key → user_id│  key 短缓存/PG      │            │
  │                       查/建 binding     ├──PG 原子分配──────▶│            │
  │                          ◀── account_id ┤◀───────────────────┤            │
  │                       解密 gho_ token    │                    │            │
  │             协议直通 / SSE 透传          │                    │            │
  │                                         ├── POST /chat/completions (Bearer gho_) ─▶
  │                                         │            (SSE 流式)            ◀──────────────────
  │   ◀── 上游响应 / SSE 透传 ──────────────┤                    │            │
  │                                         ├──▶ Kafka: prompt 日志 / usage    │
```

要点：前端 Key 查找走 Redis 短缓存，1:1 binding 以 PG 为强一致来源；prompt/usage 投 Kafka 被限时保护，不阻塞响应。

### 4.2 账号存活性校验（Refresher）

```
                Refresher                       Redis                 GHC
 due account ─────────────────────────────────▶ SET lock:account:{id} NX EX
            用账号 gho_ 构造 Bearer header ───────────────────────────▶ GET /models
            ◀── 200 / 401 / 403 / 5xx ─────────────────────────────────────────────
            200: 写 last_seen_at/refresh_at 到 PG
            401/403 login_required: quarantine account + audit
            5xx/网络抖动: 保持原状态，等待下次扫描
```

Refresher Worker 独立地周期扫描 `refresh_at <= now` 的账号，降低用户流量首次遇到失效凭证的概率。

### 4.3 账号登录失效 → 自动改路由（核心容错）

```
Cred Svc ── /chat/completions 或 SSE ─▶ GHC
        ◀── 401 / token_expired / login_required ──┐
        │                                           │
        │ 1) 标记 account 为 quarantined（PG）
        │ 2) Router: 解绑 user，从空闲池挑一个 healthy account 重新 1:1 绑定
        │ 3) 用新账号重试本次请求（至多 1 次）
        │ 4) 旧账号进入「待操作人员重新登录」队列；Refresher 持续探测
        ▼
   返回成功响应 / 或（无空闲账号时）503 + Retry-After
```

> 「主动刷新」让登出尽量不发生；「自动改路由」是兜底。两者叠加实现前端无感的长期保活。

### 4.4 Device Flow 账号上线（操作人员）

```
Operator/Admin UI        Admin API                      GHC
  │ POST /admin/accounts/{login}/login/start ───────────▶ POST /login/device/code
  │                    ◀── user_code, verification_uri ──
  │ ◀── 展示「访问 github.com/login/device 输入 XXXX-XXXX」┤
  │ (真人浏览器完成授权)                                   │
  │ POST /admin/accounts/{login}/login/poll ─────────────▶ POST /login/oauth/access_token
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

> Redis 键：`key:{sha256(proxy_key)}`（Key→user 短缓存）、`lock:account:{account_id}`（存活性校验互斥）。绑定关系以 PostgreSQL 为强一致来源。
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

- **Topics**：`ghcproxy.prompts`（请求 prompt；buffered 请求会附带响应体，流式响应不缓存完整输出）、`ghcproxy.usage`（每请求 token 计量，含 user/account/model）、`ghcproxy.audit`（账号隔离、登录事件）。
- **消费侧**：流式落数仓（用户行为分析）、告警规则（隔离账号数、503 率、刷新失败率）。
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
| 限流/熔断 | 当前透传 GHC 上游 429/5xx；可在后续版本接入每 key 限流与熔断。 |
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

## 10. 代码索引与部署

完整 Python 实现位于 `src/ghcproxy/`，测试位于 `tests/`，部署清单位于 `deploy/`（**均为占位符，无真实密钥**）：

```
src/ghcproxy/
├── __main__.py                 # 进程入口：ROLE=proxy | refresher
├── context.py                  # 依赖装配（PG/Redis/Kafka/httpx/binding/forwarder）
├── common/
│   ├── config.py               # Pydantic 配置（YAML + 环境变量覆盖；upstream header 取值）
│   ├── crypto.py               # gho_ token 的 AES-256-GCM 信封加密
│   └── keys.py                 # 代理 Key 生成 / SHA-256 哈希 / 校验
├── credential/
│   ├── client.py               # 上游 header 构造 + 登录失效判定
│   ├── device_flow.py          # GitHub Device Flow（吐 user_code，轮询取 gho_）
│   └── refresher.py            # 存活性校验 worker（抢锁单例 + 心跳）
├── router/binding.py           # 1:1 粘性绑定与空闲账号原子分配 + 失效改路由
├── proxy/
│   ├── app.py                  # FastAPI 入口：鉴权→绑定→转发→用量/prompt 投 Kafka
│   ├── auth.py                 # 从 Authorization / x-api-key 解析代理 Key
│   ├── forwarder.py            # 转发 + 失效自动改路由重试（核心容错）
│   ├── upstream.py             # httpx 上游客户端（缓冲 + 流式 SSE）
│   └── usage.py                # 从 OpenAI/Anthropic 响应（JSON 与 SSE）提取 token 用量
├── observability/
│   ├── sink.py                 # Kafka 生产者（prompt/usage/audit）+ Null 兜底
│   └── metrics.py              # Prometheus 指标
├── admin/api.py                # 操作人员 API：导入账号 / 发起登录 / 轮询完成 / 签发 Key
└── db/{repo.py,schema.sql}     # asyncpg 仓储（FOR UPDATE SKIP LOCKED）+ DDL

deploy/
├── docker/docker-compose.yaml  # 全栈：PG + Redis + Kafka + proxy + refresher
└── k8s/                        # namespace / config+secret / proxy(Deploy+Svc+HPA) / refresher
```

> 该实现已通过 73 项单元/接口测试，并在本地与 **Azure（rg-dev2，VNet 内）docker-compose 全栈**中用真实 `gho_` 凭证跑通 GPT 与 Claude 调用（OpenAI `/chat/completions` 与 Anthropic `/v1/messages` 均验证）。
> `examples/` 保留早期 TypeScript 说明性骨架，仅供对照，非运行产物。
