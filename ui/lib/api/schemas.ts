import { z } from "zod";

/**
 * Zod mirrors of `engine/repcut/api/schemas.py`.
 *
 * Parsed, never cast (`.claude/rules/code-style.md`). The engine runs on the
 * same laptop, which makes it feel like an internal call and is exactly why the
 * boundary is worth keeping: the two halves version independently, and a field
 * that changed shape should surface as one readable error here rather than as
 * `undefined` reaching a render three components deep.
 *
 * Unknown keys are stripped rather than rejected throughout, so a newer engine
 * that adds a field does not break an older UI.
 */

export const jobStatusSchema = z.enum([
  "queued",
  "running",
  "succeeded",
  "failed",
  "cancelled",
]);
export type JobStatus = z.infer<typeof jobStatusSchema>;

export const uploadStatusSchema = z.enum([
  "in_progress",
  "completed",
  "aborted",
]);
export type UploadStatus = z.infer<typeof uploadStatusSchema>;

export const projectSchema = z.object({
  id: z.string(),
  name: z.string(),
  created_at: z.string(),
  updated_at: z.string(),
});
export type Project = z.infer<typeof projectSchema>;

export const mediaFileSchema = z.object({
  id: z.string(),
  project_id: z.string(),
  sha256: z.string(),
  display_name: z.string(),
  position: z.number(),
  added_at: z.string(),

  size_bytes: z.number(),
  container_format: z.string().nullable(),
  duration_seconds: z.number().nullable(),
  display_width: z.number().nullable(),
  display_height: z.number().nullable(),
  rotation_degrees: z.number().nullable(),
  fps_source: z.number().nullable(),
  fps_normalized: z.number().nullable(),

  /**
   * Three-valued, and the UI must keep it that way.
   *
   * `true` measured variable, `false` measured constant, **`null` unknown**.
   * The `r_frame_rate != avg_frame_rate` test is container-dependent —
   * Matroska reports a genuinely variable clip as constant — so `null` means
   * "this container cannot answer", not "no". Rendering null as "constant" is
   * the drift bug the column exists to prevent, so `z.boolean().nullable()`
   * here and a three-branch render at the badge.
   */
  is_variable_frame_rate: z.boolean().nullable(),

  video_codec: z.string().nullable(),
  audio_codec: z.string().nullable(),
  audio_sample_rate: z.number().nullable(),

  has_proxy: z.boolean(),
  has_thumbnail_strip: z.boolean(),
});
export type MediaFile = z.infer<typeof mediaFileSchema>;

export const jobSchema = z.object({
  id: z.string(),
  job_type: z.string(),
  status: jobStatusSchema,
  progress: z.number(),
  step: z.string().nullable(),
  error: z.string().nullable(),
  project_id: z.string().nullable(),
  sha256: z.string().nullable(),
  created_at: z.string(),
  updated_at: z.string(),
});
export type Job = z.infer<typeof jobSchema>;

/**
 * The `/ws/jobs` frame, which is **not** the same shape as `GET /jobs`.
 *
 * The socket payload is `JobEvent` in `engine/repcut/jobs.py`: keyed `job_id`
 * rather than `id`, and carrying no `created_at`, because an event is one
 * observation of a job rather than the row. Parsing socket frames with
 * `jobSchema` fails on every frame — and since a frame that does not parse is
 * how a keepalive is recognised, the failure is silent on both sides and shows
 * up only as a job list that stays empty.
 *
 * `test_the_socket_payload_carries_the_fields_the_ui_parses` pins the same
 * field list from the engine. Rename one and one of the two suites fails.
 */
export const jobEventSchema = z.object({
  job_id: z.string(),
  job_type: z.string(),
  status: jobStatusSchema,
  progress: z.number(),
  step: z.string().nullable(),
  error: z.string().nullable(),
  project_id: z.string().nullable(),
  sha256: z.string().nullable(),
  updated_at: z.string(),
});
export type JobEvent = z.infer<typeof jobEventSchema>;

export const uploadSchema = z.object({
  id: z.string(),
  project_id: z.string(),
  display_name: z.string(),
  declared_size_bytes: z.number(),
  chunk_size_bytes: z.number(),
  /** The authoritative resume offset — where to send from next. */
  bytes_received: z.number(),
  status: uploadStatusSchema,
  resumed: z.boolean(),
});
export type Upload = z.infer<typeof uploadSchema>;

export const uploadFinalizeSchema = z.object({
  sha256: z.string(),
  media_file_id: z.string(),
  project_id: z.string(),
  /** Null when the artifacts already existed and nothing needed deriving. */
  job_id: z.string().nullable(),
  duplicate: z.boolean(),
});
export type UploadFinalize = z.infer<typeof uploadFinalizeSchema>;

/**
 * The engine's named-error envelope (`engine/repcut/api/errors.py`).
 *
 * `code` is the stable identifier to branch on; `message` is a fixed sentence
 * written for a person. The UI shows the message and never invents its own
 * wording for a failure the engine already named.
 */
export const apiErrorSchema = z.object({
  error: z.object({
    code: z.string(),
    message: z.string(),
  }),
});
export type ApiErrorBody = z.infer<typeof apiErrorSchema>;
