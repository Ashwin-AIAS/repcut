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
 * What opening a clip enqueued to bring it up to the current recipe versions.
 * Empty on every open after the first (amendment 012: lazy regeneration).
 */
export const ensureCurrentSchema = z.object({
  media_file_id: z.string(),
  enqueued_job_ids: z.array(z.string()),
});
export type EnsureCurrent = z.infer<typeof ensureCurrentSchema>;

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
/**
 * A scene's cached Gemini tagging — mirrors `SceneVLMResponse`
 * (`engine/repcut/api/schemas.py`).
 *
 * Every field is independently nullable: a partial answer is still usable,
 * and a field the model was not confident about renders as null rather than
 * dropping the whole scene's tag.
 */
export const sceneVlmSchema = z.object({
  content_type: z.string().nullable(),
  exercise_guess: z.string().nullable(),
  environment: z.string().nullable(),
  lighting_quality: z.string().nullable(),
  lighting_temperature: z.string().nullable(),
  lighting_direction: z.string().nullable(),
  energy_level: z.string().nullable(),
  aesthetic_notes: z.string().nullable(),
});
export type SceneVlm = z.infer<typeof sceneVlmSchema>;

/**
 * One detected scene — mirrors `SceneResponse` (`engine/repcut/api/schemas.py`).
 *
 * `vlm: null` collapses three different reasons on the wire (not analyzed
 * yet, degraded, or a response that never parsed) into one state, on
 * purpose (the response model's own doc comment) — the UI renders all three
 * as "not yet analyzed" rather than guessing which one happened.
 *
 * `has_sampled_frame` is what decides whether
 * `GET /media/{sha256}/scenes/{id}/frame` will serve an image; there is no
 * `sampled_frame_path` here; a stored path carries the OS username
 * (`.claude/rules/secrets.md`), so the engine never sends it.
 */
export const sceneSchema = z.object({
  id: z.string(),
  sha256: z.string(),
  sequence_index: z.number(),
  start_seconds: z.number(),
  end_seconds: z.number(),
  start_frame_source: z.number(),
  end_frame_source: z.number(),
  has_sampled_frame: z.boolean(),
  motion_energy: z.number().nullable(),
  audio_energy: z.number().nullable(),
  energy_score: z.number().nullable(),
  vlm: sceneVlmSchema.nullable(),
  created_at: z.string(),
});
export type Scene = z.infer<typeof sceneSchema>;

export const apiErrorSchema = z.object({
  error: z.object({
    code: z.string(),
    message: z.string(),
  }),
});
export type ApiErrorBody = z.infer<typeof apiErrorSchema>;

// --- grading (engine/repcut/api/grading.py) ---------------------------------

export const themeSummarySchema = z.object({
  id: z.string(),
  name: z.string(),
  description: z.string(),
  default_intensity: z.number().int(),
});
export type ThemeSummary = z.infer<typeof themeSummarySchema>;

export const packSchema = z.object({
  id: z.string(),
  name: z.string(),
  description: z.string(),
  default_theme: z.string(),
  themes: z.array(themeSummarySchema),
});
export type Pack = z.infer<typeof packSchema>;

export const lightingTemperatures = ["warm", "neutral", "cool"] as const;
export const lightingQualities = ["bright", "dim", "harsh", "soft", "mixed"] as const;

const tagsSchema = z.object({
  lighting_temperature: z.string().nullable(),
  lighting_quality: z.string().nullable(),
});

const choiceSchema = z.object({
  theme_id: z.string().nullable(),
  intensity: z.number().int().nullable(),
  lighting_temperature: z.string().nullable(),
  lighting_quality: z.string().nullable(),
});
export type GradeChoice = z.infer<typeof choiceSchema>;

export const sceneGradeSchema = z.object({
  key: z.string(),
  sequence_index: z.number().int(),
  start_seconds: z.number(),
  end_seconds: z.number(),
  analysed: tagsSchema,
  effective: tagsSchema,
  chosen: choiceSchema,
  theme_id: z.string(),
  intensity: z.number().int(),
  suggested: z.boolean(),
  correction: z.object({
    exposure_stops: z.number(),
    temperature_shift_k: z.number(),
    denoise: z.number(),
    rules: z.array(z.string()),
  }),
  preview_ready: z.boolean(),
});
export type SceneGrade = z.infer<typeof sceneGradeSchema>;

export const clipGradeSchema = z.object({
  media_file_id: z.string(),
  pack_id: z.string(),
  recommended_theme_id: z.string(),
  scores: z.record(z.string(), z.number()),
  chosen: choiceSchema,
  theme_id: z.string(),
  intensity: z.number().int(),
  suggested: z.boolean(),
  scenes: z.array(sceneGradeSchema),
});
export type ClipGrade = z.infer<typeof clipGradeSchema>;

export const previewJobSchema = z.object({ job_id: z.string() });

// --- the edit (engine/repcut/api/edit.py) -------------------------------------

export const trackSchema = z.object({
  sha256: z.string(),
  display_name: z.string(),
  status: z.enum(["pending", "analysed", "rejected", "failed"]),
  reason: z.string().nullable(),
  bpm: z.number().nullable(),
  duration_seconds: z.number().nullable(),
});
export type Track = z.infer<typeof trackSchema>;

export const librarySchema = z.object({
  tracks: z.array(trackSchema),
  licence_note: z.string(),
});
export type Library = z.infer<typeof librarySchema>;

export const jobsStartedSchema = z.object({ job_ids: z.array(z.string()) });

export const recommendationSchema = z.object({
  sha256: z.string(),
  display_name: z.string(),
  score: z.number(),
  bpm: z.number(),
  reason: z.string(),
});
export type TrackRecommendation = z.infer<typeof recommendationSchema>;

export const shotSchema = z.object({
  id: z.string(),
  segment_id: z.string(),
  sha256: z.string(),
  source_in: z.number(),
  source_out: z.number(),
  timeline_in: z.number(),
  timeline_out: z.number(),
  beat_in: z.number().int().nullable(),
  beat_out: z.number().int().nullable(),
  locked: z.boolean(),
  // Slow motion (half speed at most, P1). Older engines omit these fields.
  speed: z.number().min(0.5).max(1).default(1),
  fallback_in: z.number().nullable().default(null),
  hero_score: z.number().nullable().default(null),
  hero_reason: z.string().max(200).nullable().default(null),
});
export type Shot = z.infer<typeof shotSchema>;

export const heroSchema = z.object({
  segment_id: z.string(),
  sha256: z.string(),
  peak: z.number(),
  score: z.number(),
  rank: z.number().int(),
  reasons: z.array(z.string().max(200)),
  slow_in_edit: z.boolean(),
  slowmo_off: z.boolean(),
});
export type Hero = z.infer<typeof heroSchema>;

export const editSegmentSchema = z.object({
  id: z.string(),
  sha256: z.string(),
  start: z.number(),
  end: z.number(),
  quality: z.number(),
  energy: z.number(),
  used: z.boolean(),
  removed: z.boolean(),
  forced: z.boolean(),
  slowmo_on: z.boolean().default(false),
});
export type EditSegment = z.infer<typeof editSegmentSchema>;

export const musicModes = ["suggested", "chosen", "off"] as const;

export const editSchema = z.object({
  project_id: z.string(),
  theme_id: z.string(),
  target_seconds: z.number(),
  duration_seconds: z.number(),
  music: z.enum(musicModes),
  track_sha256: z.string().nullable(),
  music_start_seconds: z.number(),
  snapped: z.boolean(),
  beats: z.array(z.number()),
  downbeats: z.array(z.number()),
  shots: z.array(shotSchema),
  heroes: z.array(heroSchema).default([]),
  segments: z.array(editSegmentSchema),
  recommendations: z.array(recommendationSchema),
  gain: z.array(z.object({ seconds: z.number(), gain_db: z.number() })),
  speech_measured: z.boolean(),
  clips_pending: z.array(z.string()),
  warnings: z.array(z.string()),
  resync_ms: z.number(),
  resync_budget_ms: z.number(),
});
export type Edit = z.infer<typeof editSchema>;

export const overrideKinds = [
  "music_track",
  "music_off",
  "music_suggested",
  "target_duration",
  "cut_removed",
  "cut_locked",
  "cut_unlocked",
  "segment_forced",
  "segment_unforced",
  "regenerate",
  "scene_boundary",
  "slowmo_on",
  "slowmo_off",
] as const;
export type OverrideKind = (typeof overrideKinds)[number];

// --- captions and export (Prompt 06) -----------------------------------------

export const captionWordSchema = z.object({
  id: z.string(),
  text: z.string(),
  original: z.string(),
  start: z.number(),
  end: z.number(),
  probability: z.number(),
  edited: z.boolean(),
});
export type CaptionWord = z.infer<typeof captionWordSchema>;

export const captionLineSchema = z.object({
  id: z.string(),
  start: z.number(),
  end: z.number(),
  hidden: z.boolean(),
  words: z.array(captionWordSchema),
});
export type CaptionLine = z.infer<typeof captionLineSchema>;

export const captionsSchema = z.object({
  project_id: z.string(),
  enabled: z.boolean(),
  style_id: z.string(),
  suggested_style_id: z.string(),
  styles: z.array(z.object({ id: z.string(), name: z.string(), description: z.string() })),
  lines: z.array(captionLineSchema),
  model_installed: z.boolean(),
  model_message: z.string().nullable(),
  clips_untranscribed: z.array(z.string()),
  languages: z.array(z.string()),
  dropped_segments: z.number(),
});
export type Captions = z.infer<typeof captionsSchema>;

export const exportSceneSchema = z.object({
  key: z.string(),
  clip_name: z.string(),
  index: z.number(),
  start_seconds: z.number(),
  end_seconds: z.number(),
  in_edit: z.boolean(),
  offset: z.number(),
});
export type ExportScene = z.infer<typeof exportSceneSchema>;

export const exportSettingsSchema = z.object({
  project_id: z.string(),
  preset_id: z.string(),
  suggested_preset_id: z.string(),
  presets: z.array(
    z.object({ id: z.string(), label: z.string(), width: z.number(), height: z.number() }),
  ),
  scenes: z.array(exportSceneSchema),
  latest: z
    .object({
      preset_id: z.string(),
      size_bytes: z.number(),
      segments_total: z.number(),
      segments_rendered: z.number(),
      segments_reused: z.number(),
      timings_ms: z.record(z.string(), z.number()),
      problems: z.array(z.string()),
      warnings: z.array(z.string()),
    })
    .nullable(),
});
export type ExportSettings = z.infer<typeof exportSettingsSchema>;
