import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import axe from "axe-core";
import { describe, expect, it } from "vitest";
import { ProxyPlayer } from "@/components/player/ProxyPlayer";
import type { MediaFile } from "@/lib/api/schemas";

function clip(overrides: Partial<MediaFile> = {}): MediaFile {
  return {
    id: "clip-1",
    project_id: "project-1",
    sha256: "a".repeat(64),
    display_name: "squat.mp4",
    position: 0,
    added_at: "2026-08-10T09:00:00Z",
    size_bytes: 1024,
    container_format: "mov,mp4,m4a,3gp,3g2,mj2",
    duration_seconds: 10,
    display_width: 720,
    display_height: 1280,
    rotation_degrees: 90,
    fps_source: 30,
    fps_normalized: 30,
    is_variable_frame_rate: false,
    video_codec: "h264",
    audio_codec: "aac",
    audio_sample_rate: 48_000,
    has_proxy: true,
    has_thumbnail_strip: true,
    ...overrides,
  };
}

/** jsdom decodes nothing, so duration has to be supplied for seeking to clamp. */
function stubDuration(seconds: number): void {
  const video = document.querySelector("video");
  if (video === null) throw new Error("no video element rendered");
  Object.defineProperty(video, "duration", { configurable: true, value: seconds });
}

describe("ProxyPlayer", () => {
  it("asks for a clip rather than rendering an empty frame", () => {
    render(<ProxyPlayer clip={null} />);

    expect(screen.getByText("Select a clip to preview it.")).toBeInTheDocument();
    expect(document.querySelector("video")).toBeNull();
  });

  it("says why there is no preview instead of showing a broken player", () => {
    render(<ProxyPlayer clip={clip({ has_proxy: false })} />);

    expect(screen.getByText("No preview yet.")).toBeInTheDocument();
    expect(document.querySelector("video")).toBeNull();
  });

  it("plays the proxy, never the source bytes", () => {
    render(<ProxyPlayer clip={clip()} />);

    expect(document.querySelector("video")?.getAttribute("src")).toContain(
      "/media/clip-1/proxy",
    );
  });

  /**
   * A frame step, not a second step. At 30fps a whole second is thirty frames
   * of ambiguity, which is useless for judging where a cut lands.
   */
  it("steps one frame with an arrow and one second with shift", async () => {
    render(<ProxyPlayer clip={clip()} />);
    stubDuration(10);

    const group = screen.getByRole("group", { name: "Preview: squat.mp4" });
    group.focus();

    await userEvent.keyboard("{ArrowRight}");
    expect(screen.getByText("0:00.01")).toBeInTheDocument();

    await userEvent.keyboard("{Shift>}{ArrowRight}{/Shift}");
    expect(screen.getByText("0:01.01")).toBeInTheDocument();
  });

  it("clamps at both ends rather than seeking past them", async () => {
    render(<ProxyPlayer clip={clip()} />);
    stubDuration(10);

    const group = screen.getByRole("group", { name: "Preview: squat.mp4" });
    group.focus();

    await userEvent.keyboard("{ArrowLeft}");
    expect(screen.getByText("0:00.00")).toBeInTheDocument();

    await userEvent.keyboard("{End}");
    expect(screen.getByText("0:10.00")).toBeInTheDocument();
  });

  /**
   * Selecting another clip must not inherit the previous one's position. The
   * player is keyed on the clip for exactly this, so the reset is a remount
   * rather than a second render correcting the first.
   */
  it("starts a newly selected clip at zero", async () => {
    const { rerender } = render(<ProxyPlayer clip={clip()} />);
    stubDuration(10);

    screen.getByRole("group", { name: "Preview: squat.mp4" }).focus();
    await userEvent.keyboard("{End}");
    expect(screen.getByText("0:10.00")).toBeInTheDocument();

    rerender(<ProxyPlayer clip={clip({ id: "clip-2", display_name: "deadlift.mp4" })} />);

    expect(screen.getByText("0:00.00")).toBeInTheDocument();
  });

  it("has no accessibility violations", async () => {
    const { container } = render(<ProxyPlayer clip={clip()} />);

    const results = await axe.run(container, {
      rules: { "color-contrast": { enabled: false } },
    });

    expect(results.violations).toEqual([]);
  });
});
