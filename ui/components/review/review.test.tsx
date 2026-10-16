import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { BeforeAfter } from "@/components/review/BeforeAfter";
import { WhatRepcutDid } from "@/components/review/WhatRepcutDid";
import { sideSeconds } from "@/lib/compare";

const MEDIA = "11111111-1111-4111-8111-111111111111";

function respond(body: unknown) {
  return Promise.resolve(
    new Response(JSON.stringify(body), { status: 200, headers: { "content-type": "application/json" } }),
  );
}

function comparison(ready: boolean) {
  return {
    media_file_id: MEDIA,
    scene_key: "s1",
    start_seconds: 1.2345,
    end_seconds: 3.9,
    fps: 30,
    offset_frames: 38,
    before_offset_seconds: 38 / 30,
    frame_count: 80,
    before_url: `/media/${MEDIA}/proxy`,
    after_url: `/media/${MEDIA}/grade/preview?scene=s1`,
    preview_ready: ready,
    captions_on: true,
    captions_style: "punch",
  };
}

afterEach(() => vi.unstubAllGlobals());

describe("BeforeAfter", () => {
  it("seeks both sides to the same frame and moves the divider", async () => {
    vi.stubGlobal("fetch", vi.fn(() => respond(comparison(true))));
    render(<BeforeAfter mediaFileId={MEDIA} jobs={[]} />);
    const before = (await screen.findByLabelText("Before: the original footage")) as HTMLVideoElement;
    const after = screen.getByLabelText("After: graded and corrected") as HTMLVideoElement;
    expect(before.getAttribute("src")).toMatch(/\/media\/.+\/proxy$/);
    expect(after.getAttribute("src")).toMatch(/\/grade\/preview\?scene=s1$/);

    fireEvent.change(screen.getByLabelText("Position"), { target: { value: "40" } });
    expect(after.currentTime).toBeCloseTo(sideSeconds("after", 40, 38, 30), 6);
    expect(before.currentTime).toBeCloseTo(sideSeconds("before", 40, 38, 30), 6);
    expect(Math.floor(before.currentTime * 30) - 38).toBe(Math.floor(after.currentTime * 30));
    expect(screen.getByText("frame 41 of 80")).toBeInTheDocument();

    fireEvent.change(screen.getByLabelText("Divider"), { target: { value: "30" } });
    expect(before.style.clipPath).toBe("inset(0 70% 0 0)");
    expect(screen.getByText("Captions on")).toBeInTheDocument();
  });

  it("asks for the graded preview before showing a processed side", async () => {
    const calls: string[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn((url: string, init?: RequestInit) => {
        calls.push(`${init?.method ?? "GET"} ${new URL(url).pathname}`);
        if (init?.method === "POST") return respond({ job_id: "j" });
        return respond(comparison(false));
      }),
    );
    render(<BeforeAfter mediaFileId={MEDIA} jobs={[]} />);
    fireEvent.click(await screen.findByRole("button", { name: "Render preview" }));
    await waitFor(() => expect(calls).toContain(`POST /media/${MEDIA}/grade/preview`));
    expect(screen.queryByLabelText("Before: the original footage")).toBeNull();
  });
});

describe("WhatRepcutDid", () => {
  it("lists exactly the engine's checked claims", async () => {
    const claims = [
      { id: "hook", text: "Opens on a 1.9 s teaser of the best moment (squat.mp4)", count: null, facts: [] },
      { id: "cuts", text: "14 cuts on the beat of the music", count: 14, facts: [] },
    ];
    vi.stubGlobal("fetch", vi.fn(() => respond({ project_id: "p", claims, record: {} })));
    render(<WhatRepcutDid projectId="p" jobs={[]} />);
    const items = await screen.findAllByRole("listitem");
    expect(items.map((item) => item.textContent)).toEqual(claims.map((claim) => claim.text));
  });

  it("says so when there is nothing to report", async () => {
    vi.stubGlobal("fetch", vi.fn(() => respond({ project_id: "p", claims: [], record: {} })));
    render(<WhatRepcutDid projectId="p" jobs={[]} />);
    expect(await screen.findByText(/this edit has no changes to report/)).toBeInTheDocument();
  });
});
