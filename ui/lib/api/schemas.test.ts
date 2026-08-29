import { describe, expect, it } from "vitest";
import {
  jobEventSchema,
  jobSchema,
  mediaFileSchema,
  sceneSchema,
  sceneVlmSchema,
  uploadSchema,
} from "@/lib/api/schemas";

/**
 * The engine's two job shapes, verbatim.
 *
 * `socketFrame` is what `JobEvent.model_dump(mode="json")` produces in
 * `engine/repcut/jobs.py`; `httpRow` is `JobResponse` from
 * `engine/repcut/api/schemas.py`. They are different on purpose — an event is
 * one observation, not the row — and the difference is why the UI needs two
 * schemas.
 *
 * The engine asserts the same field list from its side in
 * `test_the_socket_payload_carries_the_fields_the_ui_parses`. Renaming a field
 * fails one of the two.
 */
const socketFrame = {
  job_id: "6f1c2c0a-0000-4000-8000-000000000001",
  job_type: "ingest",
  status: "running",
  progress: 0.42,
  step: "encoding the preview",
  error: null,
  project_id: "6f1c2c0a-0000-4000-8000-000000000002",
  sha256: "a".repeat(64),
  updated_at: "2026-08-10T09:15:00Z",
};

const httpRow = {
  id: "6f1c2c0a-0000-4000-8000-000000000001",
  job_type: "ingest",
  status: "succeeded",
  progress: 1,
  step: null,
  error: null,
  project_id: "6f1c2c0a-0000-4000-8000-000000000002",
  sha256: "a".repeat(64),
  created_at: "2026-08-10T09:14:00Z",
  updated_at: "2026-08-10T09:15:00Z",
};

describe("the job socket contract", () => {
  it("parses a frame the engine actually sends", () => {
    const parsed = jobEventSchema.safeParse(socketFrame);

    expect(parsed.success).toBe(true);
  });

  /**
   * The bug this pins cost the whole jobs panel and reported nothing.
   *
   * A frame that fails to parse is how the keepalive is recognised, so parsing
   * socket frames with `jobSchema` — which wants `id` and `created_at` — dropped
   * every event silently. No error anywhere; the panel simply stayed empty. The
   * assertion is written as "the HTTP schema rejects this" so that anyone who
   * later collapses the two shapes into one has to delete a test that says why.
   */
  it("is not the HTTP shape, and the HTTP schema says so", () => {
    expect(jobSchema.safeParse(socketFrame).success).toBe(false);
    expect(jobEventSchema.safeParse(httpRow).success).toBe(false);
    expect(jobSchema.safeParse(httpRow).success).toBe(true);
  });

  it("keeps the keepalive out", () => {
    expect(jobEventSchema.safeParse({ type: "ping" }).success).toBe(false);
  });
});

describe("mediaFileSchema", () => {
  const clip = {
    id: "c",
    project_id: "p",
    sha256: "b".repeat(64),
    display_name: "clip.mp4",
    position: 0,
    added_at: "2026-08-10T09:00:00Z",
    size_bytes: 1024,
    container_format: "mov,mp4,m4a,3gp,3g2,mj2",
    duration_seconds: 5.01,
    display_width: 720,
    display_height: 1280,
    rotation_degrees: 90,
    fps_source: 30,
    fps_normalized: 30,
    is_variable_frame_rate: null,
    video_codec: "h264",
    audio_codec: "aac",
    audio_sample_rate: 48000,
    has_proxy: true,
    has_thumbnail_strip: true,
  };

  it("keeps an unknown frame-rate answer null rather than false", () => {
    const parsed = mediaFileSchema.parse(clip);

    expect(parsed.is_variable_frame_rate).toBeNull();
  });

  it("survives a newer engine adding a field", () => {
    const parsed = mediaFileSchema.safeParse({ ...clip, scene_count: 4 });

    expect(parsed.success).toBe(true);
  });

  it("rejects a field that changed type, rather than passing undefined on", () => {
    const parsed = mediaFileSchema.safeParse({ ...clip, duration_seconds: "5.01" });

    expect(parsed.success).toBe(false);
  });
});

describe("sceneSchema", () => {
  const tagged = {
    id: "scene-1",
    sha256: "c".repeat(64),
    sequence_index: 0,
    start_seconds: 0,
    end_seconds: 4.5,
    start_frame_source: 0,
    end_frame_source: 135,
    has_sampled_frame: true,
    motion_energy: 0.62,
    audio_energy: 0.4,
    energy_score: 0.51,
    vlm: {
      content_type: "exercise demonstration",
      exercise_guess: "barbell back squat",
      environment: "home gym",
      lighting_quality: "even",
      lighting_temperature: "neutral",
      lighting_direction: "front",
      energy_level: "high",
      aesthetic_notes: "handheld, slight motion blur",
    },
    created_at: "2026-08-10T09:00:00Z",
  };

  it("round-trips a scene with a full Gemini tag", () => {
    const parsed = sceneSchema.parse(tagged);

    expect(parsed.vlm?.exercise_guess).toBe("barbell back squat");
    expect(parsed.has_sampled_frame).toBe(true);
  });

  /**
   * `vlm: null` is one wire state for three real reasons — not reached yet,
   * degraded, or a response that never parsed — per `SceneResponse.vlm`'s own
   * doc comment in `engine/repcut/api/schemas.py`. The schema has to accept
   * the null exactly as readily as the full object.
   */
  it("accepts a scene analysis has not reached yet", () => {
    const parsed = sceneSchema.parse({
      ...tagged,
      has_sampled_frame: false,
      motion_energy: null,
      audio_energy: null,
      energy_score: null,
      vlm: null,
    });

    expect(parsed.vlm).toBeNull();
    expect(parsed.has_sampled_frame).toBe(false);
  });

  it("keeps every scene VLM field independently nullable", () => {
    const parsed = sceneVlmSchema.parse({
      content_type: "exercise demonstration",
      exercise_guess: null,
      environment: null,
      lighting_quality: null,
      lighting_temperature: null,
      lighting_direction: null,
      energy_level: null,
      aesthetic_notes: null,
    });

    expect(parsed.content_type).toBe("exercise demonstration");
    expect(parsed.exercise_guess).toBeNull();
  });
});

describe("uploadSchema", () => {
  it("carries the resume offset and whether it resumed", () => {
    const parsed = uploadSchema.parse({
      id: "u",
      project_id: "p",
      display_name: "clip.mp4",
      declared_size_bytes: 900,
      chunk_size_bytes: 300,
      bytes_received: 600,
      status: "in_progress",
      resumed: true,
    });

    expect(parsed.bytes_received).toBe(600);
    expect(parsed.resumed).toBe(true);
  });
});
