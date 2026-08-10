import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import axe from "axe-core";
import { describe, expect, it, vi } from "vitest";
import { MediaCard } from "@/components/library/MediaCard";
import type { MediaFile } from "@/lib/api/schemas";

function clip(overrides: Partial<MediaFile> = {}): MediaFile {
  return {
    id: "clip-1",
    project_id: "project-1",
    sha256: "a".repeat(64),
    display_name: "bench-press.mp4",
    position: 0,
    added_at: "2026-08-10T09:00:00Z",
    size_bytes: 52_428_800,
    container_format: "mov,mp4,m4a,3gp,3g2,mj2",
    duration_seconds: 65,
    display_width: 720,
    display_height: 1280,
    rotation_degrees: 90,
    fps_source: 29.97,
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

describe("MediaCard", () => {
  /**
   * The three-valued frame-rate answer, at the one place a user reads it.
   *
   * `null` means the container could not answer — the
   * `r_frame_rate != avg_frame_rate` test is container-dependent, and Matroska
   * reports a genuinely variable clip as constant. A card that renders null as
   * "CFR" is the UI asserting something nothing measured, and beat sync would
   * then drift with no warning anywhere.
   */
  it.each([
    [true, "VFR", "variable frame rate"],
    [false, "CFR", "constant frame rate"],
    [null, "VFR?", "frame rate could not be determined"],
  ])(
    "renders is_variable_frame_rate=%s as %s",
    async (value, text, spoken) => {
      render(
        <MediaCard clip={clip({ is_variable_frame_rate: value })} selected={false} onSelect={() => {}} />,
      );

      expect(screen.getByText(text)).toBeInTheDocument();
      // The Badge speaks its label before the abbreviation, in its own node.
      expect(screen.getByText(new RegExp(spoken))).toBeInTheDocument();
    },
  );

  it("names itself by clip and duration rather than by 'button'", () => {
    render(<MediaCard clip={clip()} selected={false} onSelect={() => {}} />);

    expect(
      screen.getByRole("button", { name: "bench-press.mp4, 1:05" }),
    ).toBeInTheDocument();
  });

  it("reports selection through aria-pressed, not colour alone", () => {
    const { rerender } = render(
      <MediaCard clip={clip()} selected={false} onSelect={() => {}} />,
    );
    expect(screen.getByRole("button")).toHaveAttribute("aria-pressed", "false");

    rerender(<MediaCard clip={clip()} selected onSelect={() => {}} />);
    expect(screen.getByRole("button")).toHaveAttribute("aria-pressed", "true");
  });

  it("hands the whole clip back on select, so the caller need not look it up", async () => {
    const onSelect = vi.fn();
    render(<MediaCard clip={clip()} selected={false} onSelect={onSelect} />);

    await userEvent.click(screen.getByRole("button"));

    expect(onSelect).toHaveBeenCalledWith(expect.objectContaining({ id: "clip-1" }));
  });

  it("says a preview is missing rather than showing a dead player later", () => {
    render(
      <MediaCard
        clip={clip({ has_proxy: false, has_thumbnail_strip: false })}
        selected={false}
        onSelect={() => {}}
      />,
    );

    expect(screen.getByText("no preview")).toBeInTheDocument();
    // The strip is still being generated; the placeholder says so out loud
    // rather than sitting there as an unexplained grey box.
    expect(
      screen.getByRole("status", { name: "Generating thumbnail" }),
    ).toBeInTheDocument();
  });

  it("shows the display resolution, which is not the coded one for phone video", () => {
    render(<MediaCard clip={clip()} selected={false} onSelect={() => {}} />);

    expect(screen.getByText(/720x1280/)).toBeInTheDocument();
  });

  it("has no accessibility violations", async () => {
    const { container } = render(
      <MediaCard clip={clip()} selected={false} onSelect={() => {}} />,
    );

    const results = await axe.run(container, {
      rules: { "color-contrast": { enabled: false } },
    });

    expect(results.violations).toEqual([]);
  });
});
