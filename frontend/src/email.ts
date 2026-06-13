// Builds the operator-facing email that asks an end user to re-authorize their
// backend GHC account via GitHub Device Flow. The operator copies this text and
// sends it to the user out-of-band (their own email client) — the console never
// sends mail itself. Keep it professional, polite, and free of internal IDs
// (device_code/session_id/account_id never belong in a user-facing message).
import { DeviceFlowStart } from "./api";

export interface ReloginEmail {
  subject: string;
  body: string;
  // A single blob (Subject line + blank line + body) for one-click copy into an
  // email client.
  asPlainText(): string;
}

function minutes(seconds: number): number {
  return Math.max(1, Math.round(seconds / 60));
}

export function buildReloginEmail(device: DeviceFlowStart): ReloginEmail {
  const validFor = `${minutes(device.expires_in)} minutes`;
  const subject = "Action required: re-authorize your GitHub Copilot access";

  const body = [
    "Hello,",
    "",
    "To keep your GitHub Copilot access active, we need you to re-authorize " +
      "your account. This takes less than a minute and only has to be done once.",
    "",
    "Please follow these steps:",
    "",
    `  1. Open this page in your browser: ${device.verification_uri}`,
    `  2. Sign in to GitHub if you are prompted to.`,
    `  3. Enter the following one-time device code when asked:`,
    "",
    `        ${device.user_code}`,
    "",
    `For security, this code expires in about ${validFor}. If it expires ` +
      "before you finish, just let us know and we will send you a new one.",
    "",
    "If you did not expect this request, or if anything looks unfamiliar, " +
      "please contact us before entering the code.",
    "",
    "Thank you for your help, and we appreciate your cooperation.",
    "",
    "Best regards,",
    "The Operations Team",
  ].join("\n");

  return {
    subject,
    body,
    asPlainText() {
      return `Subject: ${subject}\n\n${body}`;
    },
  };
}
