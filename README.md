# GHC Proxy

> 将 **GitHub Copilot（GHC）** 的模型 API 包装成统一端点，兼容 **Claude Code / Codex / OpenClaw**。
> 前端用户拿一个代理分发的 Key 即可经代理访问 GHC 的全部模型，每个用户流量严格对应一个后端 GHC 账号，
> 实现集中化的 token 计量、prompt 留存与用户行为分析。

⚠️ 本仓库当前处于**设计与示例骨架阶段**，`examples/` 下为说明性代码片段，并非完整可运行产品。
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

### 技术基石：双 Token 模型

GHC 认证为两段式：

| Token | 来源 | 生命周期 | 用途 |
|---|---|---|---|
| **OAuth Token（长效）** | GitHub Device Flow，`gho_` 前缀 | 长期有效（直到撤销/登出） | 持久身份凭证，用于换取短效 token |
| **Copilot Token（短效）** | 用 OAuth token 调接口换取 | 约 30 分钟 | 实际请求模型 API 的 Bearer |

**关键推论**：每账号需持久化的只有一行 `gho_` OAuth token，短效 token 是一次无状态 HTTP 调用换来的缓存值。
因此**管理 N 个账号无需 N 个容器 / 文件系统隔离**——数据库存 N 行加密的 `gho_` token 即可，凭证由定时任务主动刷新长期保活。

### 总体架构

采用「**模块化单体 + 两类 K8s 工作负载**」：同一镜像，按角色启动不同进程。

```
 Claude Code ─┐            ┌──────────── GHC Proxy 镜像 ─────────────┐
 Codex        ┼─ proxy ───▶│ [proxy 角色 · 无状态 · HPA]            │──▶ api.github.com
 OpenClaw     ┘   key      │  Ingress Adapter→Router/Binding→Cred   │   /copilot_internal/v2/token
                           │ [refresher 角色 · 抢锁单例]            │──▶ api.<plan>.githubcopilot.com
                           │  定时刷新 · Device Flow · 健康隔离      │   /chat/completions /responses
                           └──────┬─────────────┬─────────────┬─────┘
                              PostgreSQL       Redis         Kafka
                          (账号/用户/绑定/  (token 缓存/绑定  (prompt 日志/
                           凭证/用量聚合)    缓存/锁/会话)     用量计量/审计)
```

- **proxy 角色**：处理前端流量，完全无状态（状态在 PG/Redis），可任意水平扩展；
- **refresher 角色**：主动刷新 token、引导 Device Flow 登录、账号健康检查与隔离，靠 Redis 分布式锁做到每账号单写者。

### 关键能力

- **1:1 粘性绑定**：`bindings` 表对 `user_id` 与 `account_id` 双 `UNIQUE`，从存储层杜绝一账号绑定多用户；空闲账号用 `FOR UPDATE SKIP LOCKED` 原子分配。
- **凭证长期保活**：在 `now + refresh_in` 提前刷新短效 token；用户长期不访问也不掉线。
- **自动改路由容错**：账号登录失效时隔离该账号，从空闲池重新绑定 healthy 账号并重试本次请求。
- **协议兼容**：同时兼容 Anthropic Messages（Claude Code）与 OpenAI 风格（Codex/OpenClaw），含 SSE 流式回译。
- **可观测性**：prompt / 用量 / 审计事件投递 Kafka，指标暴露 Prometheus。

---

## 技术栈

- **语言**：TypeScript（示例）/ Python（可选）
- **数据库**：PostgreSQL（持久状态）、Redis（缓存、会话、分布式锁）
- **消息队列**：Kafka（prompt 日志、用量计量、审计）
- **部署**：Kubernetes（无状态 proxy 层 + HPA，refresher 抢锁单例）

---

## 仓库结构

```
.
├── README.md                 # 本文件
├── ghc-proxy-design.md       # 详细设计文档（原理 / 架构 / 时序 / 数据模型）
├── todo.md                   # 原始需求背景与任务说明
├── CLAUDE.md                 # 面向 AI 助手的项目概览
└── examples/                 # 示例骨架代码与配置（占位符，非可运行产品）
    ├── src/
    │   ├── common/           # config / crypto（gho_ token AEAD 信封加密）
    │   ├── credential/       # deviceFlow / tokenExchange / refresher
    │   ├── router/           # binding（1:1 粘性绑定与空闲账号分配）
    │   └── proxy/            # server / anthropicAdapter（协议转换）
    ├── db/schema.sql         # PostgreSQL DDL
    ├── config/               # 运行配置示例 + 白名单域名
    ├── docker/               # 本地 PG / Redis / Kafka 依赖
    └── k8s/                  # proxy / refresher Deployment + HPA + Service + Secret 占位
```

---

## 项目现状

| 模块 | 状态 | 说明 |
|---|---|---|
| 可行性调研 | ✅ 已完成 | GHC 双 Token 机制已在本机验证，并与开源实现交叉核对一致。 |
| 设计文档 | ✅ 已完成 | 见 [`ghc-proxy-design.md`](./ghc-proxy-design.md)，含架构、时序、数据模型、HA/扩展、风险缓解。 |
| 示例骨架 | 🚧 进行中 | `examples/` 展示 token 交换、主动刷新、1:1 绑定、协议转换、部署等关键机制的说明性片段。 |
| 生产实现 | ⬜ 未开始 | 错误处理、重试、测试、安全加固、Admin API/UI、监控告警等需在落地时补全。 |

**当前阶段定位**：方案设计与关键机制验证已就绪，示例代码用于阐明实现路径，**尚不可直接用于生产**。

### 试用示例（仅类型检查）

```bash
cd examples
npm install
npm run typecheck   # tsc --noEmit，仅校验骨架类型
```

> 依赖（`fastify`、`ioredis`、`pg`）仅用于让骨架类型自洽；落地时按需替换/补全。

---

## 延伸阅读

- 设计文档：[`ghc-proxy-design.md`](./ghc-proxy-design.md)
- 示例说明：[`examples/README.md`](./examples/README.md)
- 需求背景：[`todo.md`](./todo.md)

## License

[MIT](./LICENSE)
