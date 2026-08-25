import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import axe from "axe-core";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { Workspace } from "@/components/workspace/Workspace";
import type { MediaFile, Project } from "@/lib/api/schemas";

const project: Project = {
  id: "project-1",
  name: "Push day",
  created_at: "2026-08-10T09:00:00Z",
  updated_at: "2026-08-10T09:00:00Z",
};

function clip(overrides: Partial<MediaFile> = {}): MediaFile {
  return {
    id: "clip-1",
    project_id: "project-1",
    sha256: "a".repeat(64),
    display_name: "bench.mp4",
    position: 0,
    added_at: "2026-08-10T09:00:00Z",
    size_bytes: 1024,
    container_format: "mov,mp4,m4a,3gp,3g2,mj2",
    duration_seconds: 30,
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

/**
 * jsdom has no WebSocket. The shell opens one on mount, so without a stand-in
 * every test here would be measuring a constructor that throws rather than the
 * shell.
 */
class SilentSocket {
  static readonly OPEN = 1;
  onopen: (() => void) | null = null;
  onmessage: ((event: MessageEvent<string>) => void) | null = null;
  onclose: (() => void) | null = null;
  onerror: (() => void) | null = null;
  close(): void {}
}

beforeEach(() => {
  vi.stubGlobal("WebSocket", SilentSocket);
  // The shell refetches its library on mount; an empty answer keeps the props
  // it was given as the thing under test.
  vi.stubGlobal(
    "fetch",
    vi.fn(async () =>
      new Response(JSON.stringify([clip()]), {
        status: 200,
        headers: { "content-type": "application/json" },
      }),
    ),
  );
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("Workspace", () => {
  it("names its regions, so a dense editor screen is navigable", async () => {
    render(<Workspace project={project} initialClips={[clip()]} />);

    for (const name of ["Media library", "Preview", "Transfers", "Engine jobs"]) {
      expect(await screen.findByRole("region", { name })).toBeInTheDocument();
    }
    expect(screen.getByRole("heading", { name: "Push day" })).toBeInTheDocument();
  });

  it("previews the clip the user selects", async () => {
    const both = [clip(), clip({ id: "clip-2", display_name: "row.mp4" })];
    // The shell refetches on mount and renders what the engine returns, so the
    // stub has to agree with the props or the second card is gone by the time
    // the test clicks it.
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        new Response(JSON.stringify(both), {
          status: 200,
          headers: { "content-type": "application/json" },
        }),
      ),
    );

    render(<Workspace project={project} initialClips={both} />);

    expect(
      await screen.findByRole("group", { name: "Preview: bench.mp4" }),
    ).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: /row\.mp4/ }));

    expect(
      screen.getByRole("group", { name: "Preview: row.mp4" }),
    ).toBeInTheDocument();
  });

  it("says the library is empty rather than showing an empty box", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        new Response("[]", {
          status: 200,
          headers: { "content-type": "application/json" },
        }),
      ),
    );

    render(<Workspace project={project} initialClips={[]} />);

    expect(
      await screen.findByText("No clips yet. Drop some in above."),
    ).toBeInTheDocument();
  });

  /**
   * The engine's own sentence, surfaced where the user is working. A library
   * that fails to load must not leave the previous grid on screen looking
   * current.
   */
  it("reports a library that could not be loaded", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        new Response(
          JSON.stringify({
            error: { code: "project_not_found", message: "that project does not exist" },
          }),
          { status: 404, headers: { "content-type": "application/json" } },
        ),
      ),
    );

    render(<Workspace project={project} initialClips={[clip()]} />);

    await waitFor(() => {
      expect(screen.getByRole("alert")).toHaveTextContent("that project does not exist");
    });
  });

  it("has no accessibility violations", async () => {
    const { container } = render(
      <Workspace project={project} initialClips={[clip()]} />,
    );
    await screen.findByRole("region", { name: "Media library" });

    const results = await axe.run(container, {
      rules: { "color-contrast": { enabled: false } },
    });

    expect(results.violations).toEqual([]);
  });
});
