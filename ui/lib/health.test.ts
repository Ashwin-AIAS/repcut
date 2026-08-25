import { afterEach, describe, expect, it, vi } from "vitest";
import { fetchHealth, healthSchema, type Health } from "@/lib/health";

/**
 * A GPU-present engine reply. Written out longhand rather than generated, so
 * the test fails if the contract changes rather than following it.
 */
const validPayload: Health = {
  engine_version: "0.1.0",
  event_loop: "ProactorEventLoop",
  event_loop_can_spawn: true,
  jobs_socket_ready: true,
  ffmpeg_version: "6.1.1",
  ffmpeg_has_libx264: true,
  cuda_available: true,
  gpu_name: "NVIDIA GeForce RTX 3050 Laptop GPU",
  vram_free_mb: 3120,
  vram_total_mb: 4096,
  torch_device_active: "cuda",
  data_dir_writable: true,
  gemini_api_key_set: false,
};

describe("healthSchema", () => {
  it("parses a full engine response into the expected object", () => {
    const result = healthSchema.safeParse(validPayload);

    expect(result.success).toBe(true);
    expect(healthSchema.parse(validPayload)).toEqual(validPayload);
  });

  it("accepts the CPU-only machine, where every probe that can be null is null", () => {
    const cpuOnly = {
      ...validPayload,
      ffmpeg_version: null,
      ffmpeg_has_libx264: false,
      cuda_available: false,
      gpu_name: null,
      vram_free_mb: null,
      vram_total_mb: null,
      torch_device_active: "cpu",
    };

    expect(healthSchema.parse(cpuOnly).torch_device_active).toBe("cpu");
  });

  it("strips unknown keys so a newer engine does not break an older UI", () => {
    const withExtra = { ...validPayload, some_future_field: "ignored" };

    expect(healthSchema.parse(withExtra)).toEqual(validPayload);
  });

  it("rejects a payload with a missing required field", () => {
    const missingField: Record<string, unknown> = { ...validPayload };
    delete missingField.engine_version;
    const result = healthSchema.safeParse(missingField);

    expect(result.success).toBe(false);
    if (!result.success) {
      expect(result.error.issues.map((issue) => issue.path.join("."))).toContain(
        "engine_version",
      );
    }
  });

  it("rejects a field of the wrong type", () => {
    const wrongType = { ...validPayload, vram_total_mb: "4096" };
    const result = healthSchema.safeParse(wrongType);

    expect(result.success).toBe(false);
    if (!result.success) {
      expect(result.error.issues.map((issue) => issue.path.join("."))).toContain(
        "vram_total_mb",
      );
    }
  });

  it("rejects a torch device outside the allowed union", () => {
    const badDevice = { ...validPayload, torch_device_active: "mps" };

    expect(healthSchema.safeParse(badDevice).success).toBe(false);
  });

  it("rejects null where the contract requires a boolean", () => {
    const nulledBoolean = { ...validPayload, data_dir_writable: null };

    expect(healthSchema.safeParse(nulledBoolean).success).toBe(false);
  });
});

describe("fetchHealth", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("returns the parsed payload when the engine is healthy", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => Response.json(validPayload)),
    );

    const result = await fetchHealth();

    expect(result.ok).toBe(true);
    if (result.ok) {
      expect(result.data).toEqual(validPayload);
    }
  });

  it("reports an unreachable engine instead of throwing", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => {
        throw new TypeError("fetch failed");
      }),
    );

    const result = await fetchHealth();

    expect(result.ok).toBe(false);
    if (!result.ok) {
      expect(result.kind).toBe("unreachable");
      // The user gets a sentence and a command, never the raw fetch error.
      expect(result.cause).not.toContain("fetch failed");
      expect(result.command).toBe("make dev");
    }
  });

  it("distinguishes a non-2xx reply from an unreachable engine", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response("nope", { status: 503 })),
    );

    const result = await fetchHealth();

    expect(result.ok).toBe(false);
    if (!result.ok) {
      expect(result.kind).toBe("bad_status");
      expect(result.cause).toContain("503");
    }
  });

  it("names the offending field when the payload fails validation", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => Response.json({ ...validPayload, cuda_available: "yes" })),
    );

    const result = await fetchHealth();

    expect(result.ok).toBe(false);
    if (!result.ok) {
      expect(result.kind).toBe("invalid_response");
      expect(result.cause).toContain("cuda_available");
    }
  });
});
