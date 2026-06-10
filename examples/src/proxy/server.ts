/**
 * Proxy 入口骨架 —— proxy 角色进程运行（无状态，可水平扩展）。
 *
 * 热路径：校验 proxy key → 解析 user → 取 1:1 绑定账号 → 取 copilot token
 *        → 协议归一化 → 转发到 GHC 模型 API → 流式回译 → 异步投 Kafka。
 *
 * 本文件只展示装配与转发主链路；鉴权、限流、Kafka 生产者等以接口形式注入。
 */
import Fastify from "fastify";
import type { Redis } from "ioredis";
import type { Pool } from "pg";
import { config } from "../common/config.js";
import { getCopilotToken, LoginExpiredError } from "../credential/tokenExchange.js";
import { resolveAccountForUser, rerouteOnFailure, NoIdleAccountError } from "../router/binding.js";
import { anthropicToOpenAI, streamOpenAIToAnthropic } from "./anthropicAdapter.js";

interface Deps {
  pool: Pool;
  redis: Redis;
  authenticateKey: (authHeader?: string) => Promise<{ userId: string; keyId: string }>;
  loadGhoToken: (accountId: string) => Promise<string>;
  emit: (topic: string, event: object) => void; // Kafka 生产者（异步、不阻塞）
}

export function buildServer(deps: Deps) {
  const app = Fastify({ logger: true });

  // ---- Anthropic Messages 入口（Claude Code）----
  app.post("/v1/messages", async (req, reply) => {
    const { userId, keyId } = await deps.authenticateKey(req.headers.authorization);
    const openaiReq = anthropicToOpenAI(req.body as Record<string, unknown>);
    return forward(deps, { userId, keyId, reply, openaiReq, translateBack: true });
  });

  // ---- OpenAI 直通入口（Codex / OpenClaw / 通用）----
  app.post("/v1/chat/completions", async (req, reply) => {
    const { userId, keyId } = await deps.authenticateKey(req.headers.authorization);
    return forward(deps, {
      userId, keyId, reply,
      openaiReq: req.body as Record<string, unknown>,
      translateBack: false,
    });
  });

  app.get("/healthz", async () => ({ ok: true }));
  return app;
}

interface ForwardArgs {
  userId: string;
  keyId: string;
  reply: import("fastify").FastifyReply;
  openaiReq: Record<string, unknown>;
  translateBack: boolean;
}

async function forward(deps: Deps, a: ForwardArgs) {
  const { pool, redis, loadGhoToken, emit } = deps;

  // 至多重试一次：用于「账号登录失效 → 改路由 → 再试」。
  for (let attempt = 0; attempt < 2; attempt++) {
    let accountId: string;
    try {
      accountId = await resolveAccountForUser(pool, a.userId);
    } catch (e) {
      if (e instanceof NoIdleAccountError) {
        a.reply.header("Retry-After", "30");
        return a.reply.code(503).send({ error: "no_idle_account" });
      }
      throw e;
    }

    try {
      const tok = await getCopilotToken(redis, accountId, () => loadGhoToken(accountId));
      const upstream = await fetch(`${tok.apiBase}/chat/completions`, {
        method: "POST",
        headers: {
          Authorization: `Bearer ${tok.token}`,
          "Content-Type": "application/json",
          ...config.upstreamHeaders,
        },
        body: JSON.stringify(a.openaiReq),
      });

      if (upstream.status === 401) {
        // 登录态在请求时失效：改路由后重试。
        await rerouteOnFailure(pool, a.userId, accountId);
        emit(config.kafka.topics.audit, { type: "account.quarantined", accountId, reason: "upstream_401" });
        continue;
      }
      if (!upstream.ok || !upstream.body) {
        return a.reply.code(upstream.status).send(await safeText(upstream));
      }

      // 计量 + prompt 留存（异步）。
      emit(config.kafka.topics.prompts, { userId: a.userId, accountId, request: a.openaiReq });
      emit(config.kafka.topics.usage, { userId: a.userId, keyId: a.keyId, accountId });

      a.reply.header("Content-Type", "text/event-stream");
      if (a.translateBack) {
        return a.reply.send(streamOpenAIToAnthropic(upstream.body)); // SSE 回译为 Anthropic 事件
      }
      return a.reply.send(upstream.body); // OpenAI 直通
    } catch (e) {
      if (e instanceof LoginExpiredError) {
        await rerouteOnFailure(pool, a.userId, accountId);
        continue;
      }
      throw e;
    }
  }

  a.reply.header("Retry-After", "30");
  return a.reply.code(503).send({ error: "no_healthy_account_after_reroute" });
}

async function safeText(res: Response): Promise<string> {
  try { return await res.text(); } catch { return ""; }
}
