import { afterEach, describe, expect, it, vi } from "vitest";

/**
 * `ENGINE_URL_DISPLAY` is computed once at module load from `process.env`, so
 * each case sets the variable and re-imports with a fresh module registry.
 */
async function loadDisplayUrl(value: string | undefined): Promise<string> {
  vi.resetModules();
  if (value === undefined) {
    delete process.env.ENGINE_URL;
  } else {
    process.env.ENGINE_URL = value;
  }
  const mod = await import("@/lib/env");
  return mod.ENGINE_URL_DISPLAY;
}

const originalEngineUrl = process.env.ENGINE_URL;

afterEach(() => {
  if (originalEngineUrl === undefined) {
    delete process.env.ENGINE_URL;
  } else {
    process.env.ENGINE_URL = originalEngineUrl;
  }
  vi.resetModules();
});

describe("ENGINE_URL_DISPLAY", () => {
  it("strips embedded credentials, which secrets.md forbids displaying", async () => {
    await expect(
      loadDisplayUrl("http://admin:hunter2@tunnel.example:8000"),
    ).resolves.toBe("http://tunnel.example:8000");
  });

  it("strips a username even when no password is present", async () => {
    await expect(loadDisplayUrl("http://admin@tunnel.example:8000")).resolves.toBe(
      "http://tunnel.example:8000",
    );
  });

  it("leaves a credential-free URL untouched", async () => {
    await expect(loadDisplayUrl("http://127.0.0.1:8010")).resolves.toBe(
      "http://127.0.0.1:8010",
    );
  });

  it("falls back to the local default when unset", async () => {
    await expect(loadDisplayUrl(undefined)).resolves.toBe("http://localhost:8000");
  });
});
