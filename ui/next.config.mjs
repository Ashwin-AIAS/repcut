/**
 * Security headers for a local-first app.
 *
 * "It only runs on localhost" is the reason these are usually skipped and the
 * reason they matter here. The UI is a real origin in a real browser, sharing a
 * cookie jar and a window namespace with every other tab the user has open, and
 * it renders values the user does not control: filenames, caption text
 * transcribed from audio, and scene descriptions written by Gemini. React
 * escapes those, but React is one `dangerouslySetInnerHTML` away from not
 * escaping them, and CSP is the layer that still holds when it does.
 *
 * `frame-ancestors 'none'` is the one that is load-bearing today: without it any
 * page can iframe `http://localhost:3000` and clickjack the editor's controls —
 * including, later, a destructive one.
 *
 * @type {import('next').NextConfig}
 */

// `unsafe-inline` for styles is required by Next's runtime style injection, and
// `unsafe-eval` in dev by React Fast Refresh. Both are scoped deliberately
// rather than left on everywhere: a nonce-based policy is Prompt 13 territory,
// when the app is actually served to someone other than its author.
const isDev = process.env.NODE_ENV !== "production";

const DEFAULT_ENGINE_ORIGIN = "http://localhost:8000";

/** The other spelling of the same loopback host, or null when it is not one. */
function loopbackSibling(origin) {
  const url = new URL(origin);
  if (url.hostname === "localhost") url.hostname = "127.0.0.1";
  else if (url.hostname === "127.0.0.1") url.hostname = "localhost";
  else return null;
  return url.origin;
}

/**
 * The engine origin the *browser* will connect to.
 *
 * This must agree with `readEngineOrigin` in `lib/api/engine.ts`, because that
 * is the value the client actually puts in `fetch`, `new WebSocket` and
 * `<video src>` — and CSP is enforced against exactly those URLs.
 * `lib/api/csp.test.ts` asserts the two stay in step; they were written twice
 * with different rules once already, and the browser was the only thing that
 * noticed.
 *
 * Exported so the test can call it, and deliberately parameterised on `env`
 * rather than reading `process.env` directly, so the test does not have to
 * mutate the process to ask a question.
 */
export function resolveEngineOrigin(env = process.env) {
  const raw = env.NEXT_PUBLIC_ENGINE_URL;
  if (raw === undefined || raw.trim() === "") return DEFAULT_ENGINE_ORIGIN;

  try {
    const parsed = new URL(raw.trim());
    if (parsed.protocol !== "http:" && parsed.protocol !== "https:") {
      return DEFAULT_ENGINE_ORIGIN;
    }
    return raw.trim().replace(/\/+$/, ""); // avoid building `//projects`
  } catch {
    // Named: `new URL` throws only on an unparseable string — a stale or
    // hand-edited `.env.local`. Degrade to the local default rather than
    // failing the build.
    return DEFAULT_ENGINE_ORIGIN;
  }
}

/**
 * Every origin string the engine is reachable at, HTTP and WebSocket forms.
 *
 * The WebSocket forms are the point. CSP source expressions match schemes
 * narrowly: an `http:` source matches `http:` and `https:` URLs and **not**
 * `ws:`, so `connect-src http://localhost:8000` permits every `fetch` to the
 * engine and blocks `ws://localhost:8000/ws/jobs` — silently, before the
 * handshake is sent, which is why uvicorn logged nothing at all while the jobs
 * panel sat on "Connecting to the engine…". Only `'self'` gets the implicit
 * ws upgrade, and the engine is not `'self'`; it is a second origin on :8000.
 *
 * Both loopback spellings are listed because `localhost` and `127.0.0.1` are
 * different origins to a browser and the user may configure either.
 */
function engineSources(env = process.env) {
  const origin = resolveEngineOrigin(env);
  const sibling = loopbackSibling(origin);
  const origins = sibling === null ? [origin] : [origin, sibling];
  return origins.flatMap((value) => [value, value.replace(/^http/, "ws")]);
}

/**
 * The policy, with the engine's origins substituted in.
 *
 * Exported for `lib/api/csp.test.ts`: a policy that is only ever read by a
 * browser is a policy whose regressions are found by a person with footage.
 */
export function buildContentSecurityPolicy(env = process.env, dev = isDev) {
  const engine = engineSources(env);
  // Only the HTTP forms: `img-src` and `media-src` fetch over HTTP, and adding
  // ws: sources to them would widen the policy for no reason.
  const engineHttp = engine.filter((value) => value.startsWith("http"));

  return [
    "default-src 'self'",
    `script-src 'self' 'unsafe-inline'${dev ? " 'unsafe-eval'" : ""}`,
    "style-src 'self' 'unsafe-inline'",
    // The thumbnail strip and the proxy preview are served by the engine, at
    // the engine's origin — `'self'` is the Next server and does not cover
    // them. Missing here, the library rendered empty frames and the player
    // rendered a dead element, with the cause visible only in the console.
    `img-src 'self' blob: data: ${engineHttp.join(" ")}`,
    `media-src 'self' blob: ${engineHttp.join(" ")}`,
    "font-src 'self'",
    // The engine is a separate origin (:8000). Nothing else is reachable — no
    // analytics, no CDN, no font host. P4: the browser cannot phone home.
    `connect-src 'self' ${engine.join(" ")}`,
    "object-src 'none'",
    "base-uri 'self'",
    "form-action 'self'",
    "frame-ancestors 'none'",
  ].join("; ");
}

const securityHeaders = [
  { key: "Content-Security-Policy", value: buildContentSecurityPolicy() },
  // Redundant with frame-ancestors for modern browsers, kept for the older ones.
  { key: "X-Frame-Options", value: "DENY" },
  { key: "X-Content-Type-Options", value: "nosniff" },
  // Local paths and project names end up in URLs. Never send them off-origin.
  { key: "Referrer-Policy", value: "no-referrer" },
  // Repcut needs none of these. Denying them means a future dependency cannot
  // quietly start using one.
  {
    key: "Permissions-Policy",
    value: "camera=(), microphone=(), geolocation=(), interest-cohort=()",
  },
];

const nextConfig = {
  // Do not advertise the framework version to anything that can reach the port.
  poweredByHeader: false,
  async headers() {
    return [{ source: "/:path*", headers: securityHeaders }];
  },
};

export default nextConfig;
