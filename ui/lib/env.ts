import { z } from "zod";

/**
 * Server-side environment. `ENGINE_URL` has no `NEXT_PUBLIC_` prefix on
 * purpose: the engine address is read in Server Components only and is never
 * inlined into the client bundle.
 *
 * No secret is read here, and no absolute filesystem path is ever hardcoded —
 * everything machine-specific comes from `.env` (gitignored).
 */
const DEFAULT_ENGINE_URL = "http://localhost:8000";

const engineUrlSchema = z
  .string()
  .trim()
  .url()
  .transform((value) => value.replace(/\/+$/, "")); // avoid building `//health`

function readEngineUrl(): string {
  const parsed = engineUrlSchema.safeParse(process.env.ENGINE_URL);
  return parsed.success ? parsed.data : DEFAULT_ENGINE_URL;
}

/**
 * The engine base URL, with no trailing slash.
 *
 * An unset or malformed value falls back to the local default rather than
 * throwing: a broken env var should surface as a readable "engine unreachable"
 * card on /status, not as a build-time crash.
 */
export const ENGINE_URL: string = readEngineUrl();
