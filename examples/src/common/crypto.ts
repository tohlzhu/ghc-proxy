/**
 * gho_ OAuth token 的应用层 AEAD 加密（AES-256-GCM）示例。
 *
 * 生产建议：使用「信封加密」——数据密钥(DEK)由 KMS 加密后随密文一起保存，
 * 进程启动时向 KMS 申请解密 DEK。此处用单一对称密钥简化演示。
 *
 * 不变量：明文 gho_ token 绝不写入日志、不落明文磁盘。
 */
import { createCipheriv, createDecipheriv, randomBytes } from "node:crypto";
import { config } from "./config.js";

const ALG = "aes-256-gcm";

function dataKey(): Buffer {
  const b = Buffer.from(config.crypto.dataKeyB64, "base64");
  if (b.length !== 32) {
    throw new Error("数据密钥必须是 32 字节（base64 编码）。请通过 KMS 下发占位符的真实值。");
  }
  return b;
}

/** 加密为可入库的紧凑二进制：[12B IV][16B authTag][密文]。 */
export function encryptToken(plaintext: string): Buffer {
  const iv = randomBytes(12);
  const cipher = createCipheriv(ALG, dataKey(), iv);
  const enc = Buffer.concat([cipher.update(plaintext, "utf8"), cipher.final()]);
  const tag = cipher.getAuthTag();
  return Buffer.concat([iv, tag, enc]);
}

/** 从入库二进制解密回明文 token。 */
export function decryptToken(blob: Buffer): string {
  const iv = blob.subarray(0, 12);
  const tag = blob.subarray(12, 28);
  const enc = blob.subarray(28);
  const decipher = createDecipheriv(ALG, dataKey(), iv);
  decipher.setAuthTag(tag);
  return Buffer.concat([decipher.update(enc), decipher.final()]).toString("utf8");
}

/** 日志安全：仅暴露前缀，便于排查而不泄露完整 token。 */
export function maskToken(token: string): string {
  if (token.length <= 8) return "<redacted>";
  return `${token.slice(0, 4)}…(${token.length} chars)`;
}
