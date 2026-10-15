import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ReferencePanel } from "@/components/reference/ReferencePanel";
import type { MediaFile } from "@/lib/api/schemas";

const PROJECT = "11111111-1111-4111-8111-111111111111";
const REF = "22222222-2222-4222-8222-222222222222";

function clip(id: string, role: "footage" | "reference", name: string): MediaFile {
  return {
    id,
    project_id: PROJECT,
    sha256: id.replace(/-/g, "").padEnd(64, "a"),
    display_name: name,
    position: 0,
    added_at: "2026-10-15T00:00:00Z",
    size_bytes: 1,
    container_format: "mp4",
    duration_seconds: 10,
    display_width: 1080,
    display_height: 1920,
    rotation_degrees: 0,
    fps_source: 30,
    fps_normalized: 30,
    is_variable_frame_rate: false,
    video_codec: "h264",
    audio_codec: null,
    audio_sample_rate: null,
    has_proxy: true,
    has_thumbnail_strip: true,
    role,
  };
}

const PROFILE = {
  media_file_id: REF,
  look: {
    look: {
      contrast: 0.3,
      saturation: 0.8,
      warmth: 0,
      black_lift: 0,
      black_crush: 0.03,
      highlight_rolloff: 0.1,
      shadows: null,
      highlights: null,
    },
    capped: ["saturation"],
  },
  rhythm: {
    duration_seconds: 20,
    shot_count: 25,
    shot_median: 0.8,
    cuts_per_10s: 12,
    bpm: 128,
    beat_alignment: 0.9,
    slowmo_share: 0,
  },
  pace: { energy: 1, calm_beats: 2, peak_beats: 2, slowmo_every_seconds: 30 },
  captions: { present: true, position: "center", size: "medium", source: "local" },
  caption_match: { style_id: "punch", enabled: true, differences: [] },
};

const MATCH = {
  reference_media_file_id: REF,
  theme_id: "ref-look-of-theirs",
  look_on: true,
  pacing_on: true,
  captions_on: true,
  pace: { energy: 1 },
  caption_style_id: "punch",
  capped: ["saturation"],
  caption_differences: ["the reference's captions are small; this style's are a different size"],
  reference_shot_median: 0.8,
};

function respond(body: unknown) {
  return Promise.resolve(new Response(JSON.stringify(body), { status: 200, headers: { "content-type": "application/json" } }));
}

afterEach(() => vi.unstubAllGlobals());

describe("ReferencePanel", () => {
  it("matches all three parts, then detaches one alone", async () => {
    const calls: string[] = [];
    let matched: unknown = null;
    vi.stubGlobal(
      "fetch",
      vi.fn((url: string, init?: RequestInit) => {
        const method = init?.method ?? "GET";
        calls.push(`${method} ${new URL(url).pathname}`);
        if (url.endsWith("/reference-match") && method === "PUT") {
          matched = MATCH;
          return respond(MATCH);
        }
        if (url.endsWith("/reference-match/pacing")) return respond({ ...MATCH, pacing_on: false });
        if (url.endsWith("/reference-match")) return respond(matched);
        return respond(PROFILE);
      }),
    );
    render(
      <ReferencePanel
        projectId={PROJECT}
        clips={[clip("33333333-3333-4333-8333-333333333333", "footage", "mine.mp4"), clip(REF, "reference", "theirs.mp4")]}
        jobs={[]}
        onChanged={() => undefined}
      />,
    );
    expect(await screen.findByText(/past Repcut's limits/)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Match this reference" }));
    const pacing = await screen.findByRole("checkbox", { name: /Pacing/ });
    expect((pacing as HTMLInputElement).checked).toBe(true);
    expect(screen.getByText("ref-look-of-theirs")).toBeTruthy();
    expect(screen.getByText(/different size/)).toBeTruthy();
    fireEvent.click(pacing);
    await waitFor(() =>
      expect(calls).toContain(`DELETE /projects/${PROJECT}/reference-match/pacing`),
    );
    await waitFor(() =>
      expect((screen.getByRole("checkbox", { name: /Pacing/ }) as HTMLInputElement).checked).toBe(false),
    );
    expect((screen.getByRole("checkbox", { name: /Look/ }) as HTMLInputElement).checked).toBe(true);
  });

  it("asks which clip is the reference, and says nothing is downloaded", async () => {
    vi.stubGlobal("fetch", vi.fn(() => respond(null)));
    render(
      <ReferencePanel
        projectId={PROJECT}
        clips={[clip("33333333-3333-4333-8333-333333333333", "footage", "mine.mp4")]}
        jobs={[]}
        onChanged={() => undefined}
      />,
    );
    expect(screen.getByText(/never downloads anything/)).toBeTruthy();
    expect(screen.getByRole("combobox")).toBeTruthy();
  });
});
