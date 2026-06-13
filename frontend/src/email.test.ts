import { describe, it, expect } from "vitest";
import { buildReloginEmail } from "./email";
import { DeviceFlowStart } from "./api";

const sample: DeviceFlowStart = {
  login: "octo-account",
  account_id: "acc-1",
  session_id: "sess-1",
  user_code: "ABCD-1234",
  verification_uri: "https://github.com/login/device",
  interval: 5,
  expires_in: 900,
};

describe("buildReloginEmail", () => {
  it("returns a subject and a body", () => {
    const mail = buildReloginEmail(sample);
    expect(mail.subject).toBeTruthy();
    expect(mail.body).toBeTruthy();
  });

  it("embeds the device code and verification URL in the body", () => {
    const { body } = buildReloginEmail(sample);
    expect(body).toContain("ABCD-1234");
    expect(body).toContain("https://github.com/login/device");
  });

  it("states how long the code stays valid (rounded to minutes)", () => {
    // 900s -> 15 minutes
    const { body } = buildReloginEmail(sample);
    expect(body).toContain("15 minutes");
  });

  it("is polite and professional (greeting + sign-off + a request to act)", () => {
    const { body } = buildReloginEmail(sample);
    const lower = body.toLowerCase();
    expect(lower).toContain("hello");
    expect(lower).toMatch(/thank you|regards|sincerely|appreciate/);
    // it must ask the user to authorize / sign in
    expect(lower).toMatch(/authori|sign in|verify/);
  });

  it("provides a single plaintext blob suitable to paste into an email client", () => {
    const text = buildReloginEmail(sample).asPlainText();
    // subject line first, then a blank line, then the body
    expect(text.startsWith("Subject: ")).toBe(true);
    expect(text).toContain("ABCD-1234");
    expect(text).toContain("https://github.com/login/device");
  });

  it("does not leak internal identifiers (device_code, session_id, account_id)", () => {
    const text = buildReloginEmail(sample).asPlainText();
    expect(text).not.toContain("sess-1");
    expect(text).not.toContain("acc-1");
  });
});
