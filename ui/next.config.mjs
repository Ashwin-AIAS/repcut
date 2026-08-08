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

const csp = [
  "default-src 'self'",
  `script-src 'self' 'unsafe-inline'${isDev ? " 'unsafe-eval'" : ""}`,
  "style-src 'self' 'unsafe-inline'",
  "img-src 'self' blob: data:",
  "media-src 'self' blob:",
  "font-src 'self'",
  // The engine is a separate origin (:8000). Nothing else is reachable — no
  // analytics, no CDN, no font host. P4: the browser cannot phone home.
  "connect-src 'self' http://localhost:8000 http://127.0.0.1:8000",
  "object-src 'none'",
  "base-uri 'self'",
  "form-action 'self'",
  "frame-ancestors 'none'",
].join("; ");

const securityHeaders = [
  { key: "Content-Security-Policy", value: csp },
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
