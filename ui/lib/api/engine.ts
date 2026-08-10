/**
 * The engine's address as the *browser* sees it.
 *
 * `lib/env.ts` holds the server-side `ENGINE_URL`, deliberately without a
 * `NEXT_PUBLIC_` prefix so it never reaches the client bundle. Upload cannot
 * use it: chunked transfer has to go browser → engine directly, because routing
 * a multi-gigabyte clip through the Next server would copy every byte through a
 * second process for no benefit and blow the RSS budget criterion 13 measures.
 *
 * So this value is public, and that is a decision rather than a convenience
 * (`.claude/rules/security.md`). It is safe to publish because it is not a
 * secret in any sense: it is `http://localhost:8000`, an address on the user's
 * own machine, already visible in every network panel and in the page's own
 * requests. Nothing sensitive may follow it across that prefix.
 */

const DEFAULT_ENGINE_ORIGIN = "http://localhost:8000";

/**
 * Restrict the scheme, exactly as `lib/env.ts` does for the server value.
 *
 * `new URL()` accepts `javascript:` and `data:`, and this string is
 * concatenated into `fetch` calls and into a `<video src>`. A stale or
 * hand-edited `.env.local` should degrade to "engine unreachable", not to a URL
 * the browser will execute.
 */
function readEngineOrigin(): string {
  // Referenced statically, not through a computed key: Next inlines
  // `process.env.NEXT_PUBLIC_*` at build time by textual substitution, and a
  // dynamic lookup produces `undefined` in the browser with no warning.
  const raw = process.env.NEXT_PUBLIC_ENGINE_URL;
  if (raw === undefined || raw.trim() === "") return DEFAULT_ENGINE_ORIGIN;

  try {
    const parsed = new URL(raw.trim());
    if (parsed.protocol !== "http:" && parsed.protocol !== "https:") {
      return DEFAULT_ENGINE_ORIGIN;
    }
    return raw.trim().replace(/\/+$/, ""); // avoid building `//projects`
  } catch {
    return DEFAULT_ENGINE_ORIGIN;
  }
}

export const ENGINE_ORIGIN: string = readEngineOrigin();

/** The `/ws/jobs` address, derived rather than configured separately. */
export function engineWebSocketUrl(path: string): string {
  // Keeping one source for the origin means the socket cannot end up pointing
  // somewhere the HTTP calls do not.
  return `${ENGINE_ORIGIN.replace(/^http/, "ws")}${path}`;
}
