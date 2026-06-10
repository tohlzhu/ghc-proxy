/**
 * Anthropic Messages  ⇄  OpenAI Chat Completions 适配骨架。
 *
 * GHC 模型 API 说 OpenAI 方言，因此：
 *   - Codex / OpenClaw 等 OpenAI 客户端：直通，无需转换；
 *   - Claude Code（Anthropic Messages）：请求转 OpenAI，响应 SSE 回译为 Anthropic 事件。
 *
 * 这里仅给出最小映射骨架，标注落地时需补全的点（system、tool_use、图片块、停止原因等）。
 */

/** Anthropic Messages 请求 → OpenAI Chat Completions 请求（最小映射）。 */
export function anthropicToOpenAI(body: Record<string, unknown>): Record<string, unknown> {
  const messages: Array<{ role: string; content: unknown }> = [];

  // system：Anthropic 顶层 system → OpenAI 的 system 消息。
  if (typeof body.system === "string" && body.system) {
    messages.push({ role: "system", content: body.system });
  }

  for (const m of (body.messages as Array<{ role: string; content: unknown }>) ?? []) {
    messages.push({ role: m.role, content: flattenContent(m.content) });
    // TODO: 处理 tool_use / tool_result 块、image 块到 OpenAI 对应结构。
  }

  return {
    model: body.model,
    messages,
    max_tokens: body.max_tokens,
    temperature: body.temperature,
    stream: body.stream ?? true,
    // TODO: tools / tool_choice / stop_sequences 等字段映射。
  };
}

/** Anthropic content 可能是字符串或块数组；取文本拼接（骨架）。 */
function flattenContent(content: unknown): string {
  if (typeof content === "string") return content;
  if (Array.isArray(content)) {
    return content
      .map((b) => (b && typeof b === "object" && "text" in b ? String((b as { text: unknown }).text) : ""))
      .join("");
  }
  return "";
}

/**
 * OpenAI SSE 流 → Anthropic Messages SSE 事件流（骨架）。
 * 真实实现需发出 message_start / content_block_start / content_block_delta /
 * content_block_stop / message_delta / message_stop 等事件并映射 usage。
 */
export function streamOpenAIToAnthropic(openaiStream: ReadableStream<Uint8Array>): ReadableStream<Uint8Array> {
  const encoder = new TextEncoder();
  const decoder = new TextDecoder();
  const reader = openaiStream.getReader();

  return new ReadableStream<Uint8Array>({
    async start(controller) {
      // message_start（占位；真实需带 id/role/usage 等）。
      controller.enqueue(encoder.encode(`event: message_start\ndata: {"type":"message_start"}\n\n`));
      controller.enqueue(
        encoder.encode(`event: content_block_start\ndata: {"type":"content_block_start","index":0}\n\n`),
      );

      for (;;) {
        const { value, done } = await reader.read();
        if (done) break;
        for (const line of decoder.decode(value).split("\n")) {
          if (!line.startsWith("data:")) continue;
          const payload = line.slice(5).trim();
          if (payload === "[DONE]") continue;
          try {
            const delta = JSON.parse(payload)?.choices?.[0]?.delta?.content;
            if (delta) {
              const ev = { type: "content_block_delta", index: 0, delta: { type: "text_delta", text: delta } };
              controller.enqueue(encoder.encode(`event: content_block_delta\ndata: ${JSON.stringify(ev)}\n\n`));
            }
          } catch {
            // 跳过无法解析的分片。
          }
        }
      }

      controller.enqueue(encoder.encode(`event: content_block_stop\ndata: {"type":"content_block_stop","index":0}\n\n`));
      controller.enqueue(encoder.encode(`event: message_stop\ndata: {"type":"message_stop"}\n\n`));
      controller.close();
    },
  });
}
