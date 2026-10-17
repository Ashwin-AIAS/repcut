import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { HookStrip } from "@/components/edit/HookStrip";
import type { Edit } from "@/lib/api/schemas";

const SHA = "a".repeat(64);

function candidate(rank: number, chosen: boolean) {
  return {
    segment_id: `${"a".repeat(16)}-${rank}000-${rank}900`,
    sha256: SHA,
    peak: rank * 2,
    start: rank * 2 - 1,
    end: rank * 2 + 1,
    hero_score: 0.8,
    hero_rank: rank,
    visual_impact: 0.9,
    score: 0.72 - rank / 100,
    rank,
    reasons: ["the most movement in this clip"],
    chosen,
  };
}

function edit(mode: "on" | "off" | "manual", candidates = [candidate(1, true), candidate(2, false), candidate(3, false)]): Edit {
  return {
    project_id: "p",
    theme_id: "moody",
    target_seconds: 30,
    duration_seconds: 30,
    music: "suggested",
    track_sha256: null,
    music_start_seconds: 0,
    snapped: true,
    beats: [],
    downbeats: [],
    shots: [
      {
        id: "0123456789abcdef", segment_id: candidates[0]?.segment_id ?? "x", sha256: SHA,
        source_in: 1, source_out: 2.9, timeline_in: 0, timeline_out: 1.9, beat_in: 0, beat_out: 4,
        locked: false, speed: 1, fallback_in: null, hero_score: 0.7, hero_reason: "opening teaser", hook: true,
      },
    ],
    heroes: [],
    hook_mode: mode,
    hook_candidates: candidates,
    hook_placement: "duplicated",
    segments: [],
    recommendations: [],
    gain: [],
    speech_measured: false,
    clips_pending: [],
    warnings: [],
    resync_ms: 1,
    resync_budget_ms: 3000,
    profile_notes: [],
  };
}

describe("HookStrip", () => {
  it("offers the top three openings and swaps with one tap", () => {
    const apply = vi.fn();
    render(<HookStrip edit={edit("on")} busy={false} apply={apply} clipNames={{ [SHA]: "squat.mp4" }} clipIds={{ [SHA]: "m1" }} />);
    const items = screen.getAllByRole("listitem");
    expect(items).toHaveLength(3);
    expect(items[0]).toHaveAttribute("data-chosen", "true");
    expect(screen.getByText(/1.9 s teaser, plays again later/)).toBeInTheDocument();
    const preview = screen.getByLabelText("Preview of squat.mp4 at 0:02");
    expect(preview.getAttribute("src")).toMatch(/\/media\/m1\/proxy#t=1\.000,3\.000$/);
    fireEvent.click(screen.getByRole("button", { name: "Open the edit with candidate 3" }));
    expect(apply).toHaveBeenCalledWith({ kind: "hook_choose", segment_id: candidate(3, false).segment_id });
    expect(screen.getByRole("button", { name: "Open the edit with candidate 1" })).toBeDisabled();
  });

  it("turns the teaser off and back to the AI's pick", () => {
    const apply = vi.fn();
    render(<HookStrip edit={edit("manual")} busy={false} apply={apply} clipNames={{}} clipIds={{}} />);
    fireEvent.click(screen.getByRole("radio", { name: "No teaser" }));
    fireEvent.click(screen.getByRole("radio", { name: "AI pick" }));
    expect(apply.mock.calls).toEqual([[{ kind: "hook_off" }], [{ kind: "hook_on" }]]);
  });

  it("shows nothing when the session has no hero moment", () => {
    const { container } = render(
      <HookStrip edit={edit("on", [])} busy={false} apply={vi.fn()} clipNames={{}} clipIds={{}} />,
    );
    expect(container).toBeEmptyDOMElement();
  });
});
