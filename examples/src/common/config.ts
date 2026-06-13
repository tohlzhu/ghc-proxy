/**
 * 集中配置 —— 所有可变端点、header、client_id 均可通过环境变量覆盖。
 * 注意：全部为占位符，请勿写入真实密钥 / 租户 ID / token。
 */

export const config = {
  // --- GitHub / Copilot 端点（公开信息，非密钥） ---
  github: {
    apiBase: process.env.GHC_GITHUB_API_BASE ?? "https://api.github.com",
    // Device Flow 端点
    deviceCodeUrl: "https://github.com/login/device/code",
    accessTokenUrl: "https://github.com/login/oauth/access_token",
    verificationUri: "https://github.com/login/device",
    // gho_ -> copilot token 交换接口
    tokenExchangePath: "/copilot_internal/v2/token",
    // GHC 客户端公开 OAuth App ID（非密钥）。默认 CLI OAuth App（颁发 gho_，直连作
    // Bearer）；如需编辑器客户端改为 Iv1.b507a08c87ecfe98（颁发 ghu_，需换取 token B）。
    clientId: process.env.GHC_OAUTH_CLIENT_ID ?? "Ov23ctDVkRmgkPke0Mmm",
    scope: process.env.GHC_OAUTH_SCOPE ?? "read:user",
  },

  // --- 调用模型 API 时附带的客户端 header（取值随客户端版本演进，需可配置） ---
  upstreamHeaders: {
    "Editor-Version": process.env.GHC_EDITOR_VERSION ?? "vscode/1.95.0",
    "Editor-Plugin-Version":
      process.env.GHC_EDITOR_PLUGIN_VERSION ?? "copilot-chat/0.26.7",
    "Copilot-Integration-Id":
      process.env.GHC_INTEGRATION_ID ?? "vscode-chat",
    "X-GitHub-Api-Version":
      process.env.GHC_API_VERSION ?? "2025-04-01",
    "User-Agent": process.env.GHC_USER_AGENT ?? "GitHubCopilotChat/0.26.7",
  } as Record<string, string>,

  // --- 刷新策略 ---
  refresh: {
    // 在 copilot token 过期前预留的安全窗口（秒）；若 refresh_in 缺失则回退此值。
    skewSeconds: Number(process.env.GHC_REFRESH_SKEW ?? 120),
    // Refresher worker 扫描周期（毫秒）。
    scanIntervalMs: Number(process.env.GHC_REFRESH_SCAN_MS ?? 30_000),
    // 每账号刷新锁的持有时长（秒）。
    lockTtlSeconds: Number(process.env.GHC_REFRESH_LOCK_TTL ?? 15),
  },

  // --- 基础设施连接串（占位） ---
  postgres: { url: process.env.DATABASE_URL ?? "postgres://USER:PASS@HOST:5432/ghcproxy" },
  redis: { url: process.env.REDIS_URL ?? "redis://HOST:6379/0" },
  kafka: {
    brokers: (process.env.KAFKA_BROKERS ?? "HOST:9092").split(","),
    topics: {
      prompts: "ghcproxy.prompts",
      usage: "ghcproxy.usage",
      audit: "ghcproxy.audit",
    },
  },

  // --- 加密：信封加密所用数据密钥（运行时应由 KMS 下发，切勿硬编码真实值） ---
  crypto: {
    // base64 编码的 32 字节密钥占位；生产环境请用 KMS 信封加密替代。
    dataKeyB64: process.env.GHC_DATA_KEY_B64 ?? "<BASE64_32_BYTE_KEY_PLACEHOLDER>",
  },

  server: {
    port: Number(process.env.PORT ?? 8080),
    // 单账号最大活跃用户数（强制 1:1）。
    maxUsersPerAccount: 1,
  },
} as const;

export type AppConfig = typeof config;
