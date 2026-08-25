import { afterEach, describe, expect, it, vi } from "vitest";
import {
  buildContentSecurityPolicy,
  resolveEngineOrigin,
} from "../../next.config.mjs";

/**
 * The Content-Security-Policy is the only part of the UI whose regressions the
 * test suite could not see: it is a header, enforced by a browser, and every
 * test here runs in Node or jsdom where nothing enforces it. So it shipped
 * naming `http://localhost:8000` and nothing else, and the jobs socket —
 * `ws://localhost:8000/ws/jobs` — was refused by Chrome before the handshake
 * was sent. The engine logged nothing, because nothing reached it.
 *
 * These tests model the browser's own matching rule rather than searching the
 * policy for a substring, so they fail for the reason the browser would.
 */

/** The source list of one directive, or null when the directive is absent. */
function directive(policy: string, name: string): readonly string[] | null {
  for (const part of policy.split(";")) {
    const tokens = part.trim().split(/\s+/);
    if (tokens[0] === name) return tokens.slice(1);
  }
  return null;
}

/**
 * CSP3 §6.6.2.5, "match a scheme against a scheme". The rule that matters here
 * is the one that is *not* in the list: an `http` source does not match a `ws`
 * URL. Only `'self'` carries the implicit ws upgrade, and the engine is a
 * second origin, so `'self'` never covered it.
 */
function schemeMatches(source: string, url: string): boolean {
  if (source === url) return true;
  if (source === "http:") return url === "https:";
  if (source === "ws:") return url === "wss:" || url === "http:" || url === "https:";
  if (source === "wss:") return url === "https:";
  return false;
}

/** Whether a browser would permit `target` under `sources`, given the page's origin. */
function permits(
  sources: readonly string[],
  target: string,
  pageOrigin = "http://localhost:3000",
): boolean {
  const url = new URL(target);
  return sources.some((source) => {
    if (source === "'self'") {
      const self = new URL(pageOrigin);
      return (
        self.host === url.host &&
        // 'self' is the one expression that does upgrade http → ws.
        (self.protocol === url.protocol ||
          (self.protocol === "http:" &&
            ["https:", "ws:", "wss:"].includes(url.protocol)))
      );
    }
    if (!source.includes("://")) return false;
    const allowed = new URL(source);
    return schemeMatches(allowed.protocol, url.protocol) && allowed.host === url.host;
  });
}

/** The engine module reads `process.env` once, at import. Re-read it per case. */
async function engineModule(engineUrl: string | undefined) {
  vi.resetModules();
  if (engineUrl === undefined) vi.stubEnv("NEXT_PUBLIC_ENGINE_URL", "");
  else vi.stubEnv("NEXT_PUBLIC_ENGINE_URL", engineUrl);
  return import("@/lib/api/engine");
}

const CASES: readonly (string | undefined)[] = [
  undefined,
  "http://localhost:8000",
  "http://127.0.0.1:8000",
  "http://localhost:8010/",
  "javascript:alert(1)",
  "not a url",
];

afterEach(() => {
  vi.unstubAllEnvs();
  vi.resetModules();
});

describe("the policy and the client agree on the engine origin", () => {
  it.each(CASES)("resolves NEXT_PUBLIC_ENGINE_URL=%s identically", async (value) => {
    const { ENGINE_ORIGIN } = await engineModule(value);
    const env = { NEXT_PUBLIC_ENGINE_URL: value ?? "" };

    expect(resolveEngineOrigin(env)).toBe(ENGINE_ORIGIN);
  });
});

describe("the policy permits what the client actually opens", () => {
  it.each(CASES)(
    "allows the jobs socket with NEXT_PUBLIC_ENGINE_URL=%s",
    async (value) => {
      const { engineWebSocketUrl } = await engineModule(value);
      const env = { NEXT_PUBLIC_ENGINE_URL: value ?? "" };
      const connect = directive(buildContentSecurityPolicy(env), "connect-src");

      expect(connect).not.toBeNull();
      expect(permits(connect ?? [], engineWebSocketUrl("/ws/jobs"))).toBe(true);
    },
  );

  it.each(CASES)(
    "allows the thumbnail strip and the proxy with NEXT_PUBLIC_ENGINE_URL=%s",
    async (value) => {
      const { thumbnailStripUrl, proxyUrl } = await engineModule(value).then(
        async () => import("@/lib/api/client"),
      );
      const env = { NEXT_PUBLIC_ENGINE_URL: value ?? "" };
      const policy = buildContentSecurityPolicy(env);

      expect(permits(directive(policy, "img-src") ?? [], thumbnailStripUrl("m1"))).toBe(
        true,
      );
      expect(permits(directive(policy, "media-src") ?? [], proxyUrl("m1"))).toBe(true);
    },
  );

  it("still permits the engine's HTTP calls", async () => {
    const env = { NEXT_PUBLIC_ENGINE_URL: "" };
    const connect = directive(buildContentSecurityPolicy(env), "connect-src");

    expect(permits(connect ?? [], "http://localhost:8000/projects")).toBe(true);
  });
});

describe("the matcher itself", () => {
  /**
   * The negative control. Without this the two tests above would pass against a
   * matcher that returned `true` for everything — which is the shape of bug
   * this whole file exists to catch.
   */
  it("refuses a ws URL under the http-only policy that shipped", () => {
    const shipped = ["'self'", "http://localhost:8000", "http://127.0.0.1:8000"];

    expect(permits(shipped, "ws://localhost:8000/ws/jobs")).toBe(false);
    expect(permits(shipped, "http://localhost:8000/projects")).toBe(true);
  });

  it("refuses an origin nobody allowed", () => {
    const policy = buildContentSecurityPolicy({ NEXT_PUBLIC_ENGINE_URL: "" });

    expect(
      permits(directive(policy, "connect-src") ?? [], "https://example.test/collect"),
    ).toBe(false);
  });
});
