/**
 * Types for the two functions `next.config.mjs` exports for testing.
 *
 * The config has to stay `.mjs` — Next loads it before any TypeScript exists —
 * but `lib/api/csp.test.ts` imports it, and `allowJs` is off (and staying off).
 * Declaring the surface here keeps the import typed without an `any` and
 * without loosening the compiler for the whole project.
 */

/** A `process.env`-shaped bag. Only `NEXT_PUBLIC_ENGINE_URL` is read. */
type EnvLike = Record<string, string | undefined>;

/** The engine origin the browser will connect to. Mirrors `lib/api/engine.ts`. */
export declare function resolveEngineOrigin(env?: EnvLike): string;

/** The Content-Security-Policy header value, engine origins substituted in. */
export declare function buildContentSecurityPolicy(env?: EnvLike, dev?: boolean): string;

declare const nextConfig: import("next").NextConfig;
export default nextConfig;
