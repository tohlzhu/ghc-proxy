# GHC Proxy 示例代码与配置（早期 TypeScript 骨架 · 已被实现取代）

> ⚠️ **本目录是早期的 TypeScript 说明性骨架，已被仓库根的完整 Python 实现取代。**
> 实际运行代码见 [`../src/ghcproxy/`](../src/ghcproxy/)，部署见 [`../deploy/`](../deploy/)，
> 索引见 [`../ghc-proxy-design.md`](../ghc-proxy-design.md) §10。本目录仅留作设计对照，**勿用于生产**。
>
> 注意：此处早期片段假设「`gho_` → 短效 copilot token 两段式换取」，
> 实测已修正为「`gho_` 直接作 Bearer」（见设计文档 §2.1）。

> 配合设计文档 [`../ghc-proxy-design.md`](../ghc-proxy-design.md) 阅读。
> 这些是**说明性骨架/片段**，展示关键机制（token 交换、主动刷新、1:1 绑定、协议转换、部署），
> 并非完整可运行产品。所有值均为**占位符**，不含真实密钥 / 租户 ID / token。

## 目录

| 路径 | 作用 |
|---|---|
| `src/common/config.ts` | 集中配置；端点、客户端 header、client_id、刷新策略（占位）。 |
| `src/common/crypto.ts` | `gho_` token 的 AES-256-GCM 信封加密示例。 |
| `src/credential/deviceFlow.ts` | Device Flow 登录：吐 `user_code` 给真人，轮询取 `gho_` token。 |
| `src/credential/tokenExchange.ts` | `gho_` → 短效 Copilot token 交换 + Redis 缓存（每账号抢锁防惊群）。 |
| `src/credential/refresher.ts` | 主动刷新 worker；登录失效则隔离账号。 |
| `src/router/binding.ts` | 1:1 粘性绑定与空闲账号原子分配（`FOR UPDATE SKIP LOCKED` + 双 UNIQUE）。 |
| `src/proxy/server.ts` | 代理入口骨架：鉴权 → 路由 → 转发 → 失败改路由重试。 |
| `src/proxy/anthropicAdapter.ts` | Anthropic Messages ⇄ OpenAI 转换骨架（含 SSE 回译）。 |
| `db/schema.sql` | PostgreSQL DDL。 |
| `config/ghc-proxy.example.yaml` | 运行配置示例 + 白名单域名。 |
| `docker/docker-compose.yaml` | 本地 PG / Redis / Kafka 依赖。 |
| `k8s/` | proxy Deployment + HPA + Service + Secret 占位；refresher Deployment。 |

## 类型检查

```bash
cd examples
npm install
npm run typecheck   # tsc --noEmit，仅校验骨架类型
```

> 依赖（`fastify`、`ioredis`、`pg`）仅用于让骨架类型自洽；落地时按需替换/补全。
