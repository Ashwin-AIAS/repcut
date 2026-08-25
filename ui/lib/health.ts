import { z } from "zod";
import { ENGINE_URL } from "@/lib/env";

/**
 * Shape of the engine's `GET /health` response.
 *
 * Several fields are nullable because the probe behind them can legitimately
 * fail: FFmpeg may not be installed, and there may be no GPU at all (the engine
 * is required to boot and run on CPU). `null` means "we could not determine
 * this", which the UI must render as an explicit "Unavailable" rather than a
 * blank cell.
 *
 * Unknown extra keys are stripped rather than rejected, so a newer engine that
 * adds a field does not break an older UI.
 */
export const healthSchema = z.object({
  engine_version: z.string(),
  /**
   * The asyncio loop the engine is serving on, and whether it can start child
   * processes. Not trivia: on Windows, uvicorn selects a loop with no
   * subprocess transport whenever `--reload` is passed, and on that loop every
   * FFmpeg and ffprobe call fails — so `ffmpeg_version` can report a perfectly
   * good install that the engine is nonetheless unable to run. This is the
   * field that tells the two apart.
   */
  event_loop: z.string(),
  event_loop_can_spawn: z.boolean(),
  /**
   * Whether the engine can serve `/ws/jobs`: route mounted, worker alive,
   * WebSocket protocol available. This is the engine's half of the answer only
   * — it cannot see a browser refusing the connection, which is what actually
   * broke the socket. `components/status/JobStreamProbe` supplies the other
   * half, from inside the browser.
   */
  jobs_socket_ready: z.boolean(),
  ffmpeg_version: z.string().nullable(),
  ffmpeg_has_libx264: z.boolean(),
  cuda_available: z.boolean(),
  gpu_name: z.string().nullable(),
  vram_free_mb: z.number().nullable(),
  vram_total_mb: z.number().nullable(),
  torch_device_active: z.union([z.literal("cuda"), z.literal("cpu")]),
  data_dir_writable: z.boolean(),
  gemini_api_key_set: z.boolean(),
});

export type Health = z.infer<typeof healthSchema>;

/** Failure classes worth telling the user apart, because each has a different fix. */
export type HealthErrorKind = "unreachable" | "bad_status" | "invalid_response";

export type HealthResult =
  | { readonly ok: true; readonly data: Health }
  | {
      readonly ok: false;
      readonly kind: HealthErrorKind;
      /** One human-readable sentence. Never a stack trace or a raw error object. */
      readonly cause: string;
      /** What the user should actually do about it, in prose. */
      readonly fix: string;
      /**
       * The shell command that fixes it, when there is one. Kept separate from
       * `fix` so the UI can render it as code — this module stays free of
       * presentation markup.
       */
      readonly command?: string;
    };

const REQUEST_TIMEOUT_MS = 5000;

function unreachable(cause: string): HealthResult {
  return {
    ok: false,
    kind: "unreachable",
    cause,
    fix: `Start the engine, then reload this page. Repcut expects it at ${ENGINE_URL}.`,
    command: "make dev",
  };
}

/**
 * Turn a thrown fetch failure into a sentence.
 *
 * Node's fetch reports a refused connection as an opaque `TypeError: fetch
 * failed`, which is useless to a user, so we translate rather than forward it.
 */
function describeFetchFailure(error: unknown): HealthResult {
  if (error instanceof Error && error.name === "TimeoutError") {
    return unreachable(
      `The engine did not respond within ${REQUEST_TIMEOUT_MS / 1000} seconds.`,
    );
  }
  return unreachable(`No response from the engine at ${ENGINE_URL}.`);
}

/**
 * Fetch and validate engine health.
 *
 * Returns a discriminated result instead of throwing: the status page renders
 * the failure as content, so a dead engine is a readable card, not a 500.
 */
export async function fetchHealth(): Promise<HealthResult> {
  let response: Response;
  try {
    response = await fetch(`${ENGINE_URL}/health`, {
      cache: "no-store",
      headers: { accept: "application/json" },
      signal: AbortSignal.timeout(REQUEST_TIMEOUT_MS),
    });
  } catch (error: unknown) {
    // Connection refused, DNS failure, or timeout — the engine is not there.
    return describeFetchFailure(error);
  }

  if (!response.ok) {
    return {
      ok: false,
      kind: "bad_status",
      cause: `The engine answered with HTTP ${response.status} ${response.statusText}.`.trim(),
      fix: "The engine is running but unhealthy. Check its terminal output for the failing startup step.",
    };
  }

  let body: unknown;
  try {
    body = await response.json();
  } catch {
    // Body was not JSON — usually a proxy or the wrong service on this port.
    return {
      ok: false,
      kind: "invalid_response",
      cause: "The engine's reply was not valid JSON.",
      fix: `Check that ${ENGINE_URL} is the Repcut engine and not another service on that port.`,
    };
  }

  const parsed = healthSchema.safeParse(body);
  if (!parsed.success) {
    const fields = parsed.error.issues
      .map((issue) => issue.path.join("."))
      .filter((path) => path.length > 0);
    const detail =
      fields.length > 0 ? ` Problem fields: ${[...new Set(fields)].join(", ")}.` : "";
    return {
      ok: false,
      kind: "invalid_response",
      cause: `The engine's health response did not match the expected shape.${detail}`,
      fix: "The UI and engine versions are probably out of step. Update both to the same commit.",
    };
  }

  return { ok: true, data: parsed.data };
}
