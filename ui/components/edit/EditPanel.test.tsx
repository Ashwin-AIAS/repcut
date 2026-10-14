import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { EditPanel } from "@/components/edit/EditPanel";
import type { ApiResult, EditOverride } from "@/lib/api/client";
import type { Edit, Library, Shot } from "@/lib/api/schemas";

const getEdit = vi.fn<(id: string) => Promise<ApiResult<Edit>>>();
const overrideEdit = vi.fn<(id: string, c: EditOverride) => Promise<ApiResult<Edit>>>();
const listTracks = vi.fn<() => Promise<ApiResult<Library>>>();
const measureClips = vi.fn();
const scanMusic = vi.fn();

vi.mock("@/lib/api/client", () => ({
  getEdit: (id: string) => getEdit(id),
  overrideEdit: (id: string, c: EditOverride) => overrideEdit(id, c),
  listTracks: () => listTracks(),
  measureClips: (id: string) => measureClips(id),
  scanMusic: () => scanMusic(),
}));

const PROJECT = "22222222-2222-4222-8222-222222222222";
const A = "a".repeat(64);
const B = "b".repeat(64);
const CLIP = "c".repeat(64);

function shot(id: string, start: number, end: number, locked = false): Shot {
  return {
    id,
    segment_id: `${"c".repeat(16)}-0-9000`,
    sha256: CLIP,
    source_in: start,
    source_out: end,
    timeline_in: start,
    timeline_out: end,
    beat_in: null,
    beat_out: null,
    locked,
  };
}

function edit(over: Partial<Edit> = {}): Edit {
  return {
    project_id: PROJECT,
    theme_id: "moody",
    target_seconds: 30,
    duration_seconds: 30,
    music: "suggested",
    track_sha256: A,
    music_start_seconds: 4,
    snapped: true,
    beats: [0, 0.5, 1, 1.5],
    downbeats: [0, 2],
    shots: [shot("0000000000000001", 0, 10), shot("0000000000000002", 10, 30)],
    segments: [],
    recommendations: [
      { sha256: A, display_name: "fast drop.wav", score: 0.9, bpm: 140, reason: "High energy, 140 BPM matches your fast pacing." },
      { sha256: B, display_name: "slow.wav", score: 0.5, bpm: 96, reason: "Low energy, 96 BPM is a stretch." },
    ],
    gain: [
      { seconds: 0, gain_db: -3 },
      { seconds: 1, gain_db: -12 },
    ],
    speech_measured: true,
    clips_pending: [],
    warnings: [],
    resync_ms: 4,
    resync_budget_ms: 3000,
    ...over,
  };
}

const LIBRARY: Library = {
  tracks: [
    { sha256: A, display_name: "fast drop.wav", status: "analysed", reason: null, bpm: 140, duration_seconds: 90 },
    { sha256: B, display_name: "slow.wav", status: "analysed", reason: null, bpm: 96, duration_seconds: 90 },
    { sha256: "d".repeat(64), display_name: "live.mp3", status: "rejected", reason: "this track's tempo drifts too much", bpm: null, duration_seconds: null },
  ],
  licence_note: "Only tracks you own or that are royalty-free.",
};

beforeEach(() => {
  getEdit.mockReset().mockResolvedValue({ ok: true, data: edit() });
  overrideEdit.mockReset().mockResolvedValue({ ok: true, data: edit() });
  listTracks.mockReset().mockResolvedValue({ ok: true, data: LIBRARY });
});

describe("EditPanel", () => {
  it("shows the AI's pick with its reason, and the tracks that cannot be used, with why", async () => {
    render(<EditPanel projectId={PROJECT} jobs={[]} clipNames={{ [CLIP]: "squat.mp4" }} />);
    expect(await screen.findByText("High energy, 140 BPM matches your fast pacing.", { selector: "span" })).toBeTruthy();
    expect(screen.getByText("AI pick")).toBeTruthy();
    expect(screen.getByText(/tempo drifts too much/)).toBeTruthy();
    expect(screen.getAllByText(/squat.mp4/).length).toBe(2);
  });

  it("sends a track choice, a lock, a removal and export-without-music as overrides", async () => {
    render(<EditPanel projectId={PROJECT} jobs={[]} clipNames={{}} />);
    fireEvent.click(await screen.findByRole("radio", { name: /slow.wav/ }));
    await waitFor(() => expect(overrideEdit).toHaveBeenLastCalledWith(PROJECT, { kind: "music_track", track_sha256: B }));
    fireEvent.click(screen.getByRole("button", { name: "Lock cut 1" }));
    await waitFor(() => expect(overrideEdit).toHaveBeenLastCalledWith(PROJECT, { kind: "cut_locked", shot_id: "0000000000000001" }));
    fireEvent.click(screen.getByRole("button", { name: "Remove cut 2" }));
    await waitFor(() => expect(overrideEdit).toHaveBeenLastCalledWith(PROJECT, { kind: "cut_removed", shot_id: "0000000000000002" }));
    fireEvent.click(screen.getByRole("checkbox", { name: /Export without music/ }));
    await waitFor(() => expect(overrideEdit).toHaveBeenLastCalledWith(PROJECT, { kind: "music_off" }));
  });

  it("re-renders from the engine's answer: a locked cut shows as locked", async () => {
    overrideEdit.mockResolvedValue({
      ok: true,
      data: edit({ music: "chosen", shots: [shot("0000000000000001", 0, 10, true), shot("0000000000000003", 10, 30)] }),
    });
    render(<EditPanel projectId={PROJECT} jobs={[]} clipNames={{}} />);
    fireEvent.click(await screen.findByRole("button", { name: "Lock cut 1" }));
    expect(await screen.findByRole("button", { name: "Unlock cut 1" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Unlock cut 1" }).getAttribute("aria-pressed")).toBe("true");
    // A person's choice: the reset to the AI's track is offered.
    expect(screen.getByRole("group", { name: "Music" })).toBeTruthy();
  });

  it("says when there is no music and when footage still has to be measured", async () => {
    getEdit.mockResolvedValue({
      ok: true,
      data: edit({ music: "off", snapped: false, track_sha256: null, recommendations: [], gain: [], clips_pending: ["x"], warnings: ["your music library is empty"] }),
    });
    render(<EditPanel projectId={PROJECT} jobs={[]} clipNames={{}} />);
    expect(await screen.findByText("your music library is empty")).toBeTruthy();
    expect(screen.getByText(/at the theme's pace/)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Measure footage" }));
    expect(measureClips).toHaveBeenCalledWith(PROJECT);
  });

  it("shows the engine's error sentence when an override is refused", async () => {
    overrideEdit.mockResolvedValue({ ok: false, code: "invalid_edit_override", message: "that cut is not in this edit" });
    render(<EditPanel projectId={PROJECT} jobs={[]} clipNames={{}} />);
    fireEvent.click(await screen.findByRole("button", { name: "Remove cut 1" }));
    expect((await screen.findByRole("alert")).textContent).toBe("that cut is not in this edit");
  });
});
