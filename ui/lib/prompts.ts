import { z } from "zod";
import { ENGINE_URL } from "@/lib/env";

export const promptMetadataSchema = z.object({
  id: z.string(),
  name: z.string(),
  wave: z.string(),
  wave_number: z.number(),
  summary: z.string(),
  deliverables: z.array(z.string()),
  human_review: z.boolean(),
  human_review_type: z.string().nullable(),
  key_tech: z.array(z.string()),
  gate_command: z.string(),
  report_file: z.string(),
  estimated_timeline: z.string(),
});

export const promptStatusItemSchema = z.object({
  metadata: promptMetadataSchema,
  status: z.union([z.literal("PASSED"), z.literal("IN_PROGRESS"), z.literal("PENDING")]),
  report_exists: z.boolean(),
  gate_script_exists: z.boolean(),
  notes: z.string(),
});

export const waveSummarySchema = z.object({
  wave_number: z.number(),
  wave_title: z.string(),
  total_prompts: z.number(),
  passed_prompts: z.number(),
  in_progress_prompts: z.number(),
  pending_prompts: z.number(),
  completion_percentage: z.number(),
  estimated_timeline: z.string(),
});

export const promptsTrackerResponseSchema = z.object({
  total_prompts: z.number(),
  passed_count: z.number(),
  in_progress_count: z.number(),
  pending_count: z.number(),
  overall_completion_percentage: z.number(),
  prompts: z.array(promptStatusItemSchema),
  waves: z.array(waveSummarySchema),
});

export type PromptMetadata = z.infer<typeof promptMetadataSchema>;
export type PromptStatusItem = z.infer<typeof promptStatusItemSchema>;
export type WaveSummary = z.infer<typeof waveSummarySchema>;
export type PromptsTrackerResponse = z.infer<typeof promptsTrackerResponseSchema>;

export type PromptsErrorKind = "unreachable" | "bad_status" | "invalid_response";

export type PromptsResult =
  | { readonly ok: true; readonly data: PromptsTrackerResponse }
  | {
      readonly ok: false;
      readonly kind: PromptsErrorKind;
      readonly cause: string;
      readonly fix: string;
      readonly command?: string;
    };

const REQUEST_TIMEOUT_MS = 5000;

function unreachable(cause: string): PromptsResult {
  return {
    ok: false,
    kind: "unreachable",
    cause,
    fix: `Start the engine, then reload this page. Repcut expects it at ${ENGINE_URL}.`,
    command: "make dev",
  };
}

export async function fetchPrompts(): Promise<PromptsResult> {
  let response: Response;
  try {
    response = await fetch(`${ENGINE_URL}/prompts`, {
      cache: "no-store",
      headers: { accept: "application/json" },
      signal: AbortSignal.timeout(REQUEST_TIMEOUT_MS),
    });
  } catch (error: unknown) {
    if (error instanceof Error && error.name === "TimeoutError") {
      return unreachable(
        `The engine did not respond within ${REQUEST_TIMEOUT_MS / 1000} seconds.`,
      );
    }
    return unreachable(`No response from the engine at ${ENGINE_URL}.`);
  }

  if (!response.ok) {
    return {
      ok: false,
      kind: "bad_status",
      cause: `The engine answered with HTTP ${response.status} ${response.statusText}.`.trim(),
      fix: "The engine is running but unhealthy. Check its terminal output for the failing endpoint.",
    };
  }

  let body: unknown;
  try {
    body = await response.json();
  } catch {
    return {
      ok: false,
      kind: "invalid_response",
      cause: "The engine's reply was not valid JSON.",
      fix: `Check that ${ENGINE_URL} is the Repcut engine and not another service on that port.`,
    };
  }

  const parsed = promptsTrackerResponseSchema.safeParse(body);
  if (!parsed.success) {
    const fields = parsed.error.issues
      .map((issue) => issue.path.join("."))
      .filter((path) => path.length > 0);
    const detail =
      fields.length > 0 ? ` Problem fields: ${[...new Set(fields)].join(", ")}.` : "";
    return {
      ok: false,
      kind: "invalid_response",
      cause: `The engine's response did not match the expected schema.${detail}`,
      fix: "The UI and engine schemas may be out of step. Update both.",
    };
  }

  return { ok: true, data: parsed.data };
}
