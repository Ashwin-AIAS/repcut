import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ExportPanel } from "@/components/export/ExportPanel";
import type { ApiResult, ExportChange } from "@/lib/api/client";
import type { ExportSettings, JobEvent } from "@/lib/api/schemas";

const getExportSettings = vi.fn<(id: string) => Promise<ApiResult<ExportSettings>>>();
const changeExport = vi.fn<(id: string, c: ExportChange) => Promise<ApiResult<ExportSettings>>>();
const startExport = vi.fn();

vi.mock("@/lib/api/client", () => ({
  getExportSettings: (id: string) => getExportSettings(id),
  changeExport: (id: string, c: ExportChange) => changeExport(id, c),
  startExport: (id: string) => startExport(id),
  exportFileUrl: (id: string, preset: string) => `http://engine/projects/${id}/export/${preset}/file`,
}));

const PROJECT = "22222222-2222-4222-8222-222222222222";
const SCENE = `${"a".repeat(16)}:clip`;

function settings(over: Partial<ExportSettings> = {}): ExportSettings {
  return {
    project_id: PROJECT,
    preset_id: "reel",
    suggested_preset_id: "reel",
    presets: [
      { id: "reel", label: "Reel 9:16", width: 1080, height: 1920 },
      { id: "square", label: "Square 1:1", width: 1080, height: 1080 },
      { id: "landscape", label: "Landscape 16:9", width: 1920, height: 1080 },
    ],
    scenes: [
      { key: SCENE, clip_name: "squat.mp4", index: 0, start_seconds: 0, end_seconds: 9, in_edit: true, offset: 0 },
      { key: `${"b".repeat(16)}:clip`, clip_name: "unused.mp4", index: 0, start_seconds: 0, end_seconds: 3, in_edit: false, offset: 0 },
    ],
    latest: null,
    ...over,
  };
}

function job(over: Partial<JobEvent>): JobEvent {
  return {
    job_id: "j1",
    job_type: "export",
    status: "running",
    progress: 0.42,
    step: "rendering shots",
    error: null,
    project_id: PROJECT,
    sha256: null,
    updated_at: "2026-10-13T12:00:00Z",
    ...over,
  };
}

describe("ExportPanel", () => {
  beforeEach(() => {
    getExportSettings.mockReset();
    changeExport.mockReset();
    startExport.mockReset();
  });

  it("offers the three formats with reel suggested, and frames only scenes in the edit", async () => {
    getExportSettings.mockResolvedValue({ ok: true, data: settings() });
    render(<ExportPanel projectId={PROJECT} jobs={[]} />);
    expect(await screen.findByRole("radio", { name: /Reel 9:16/ })).toHaveAttribute("aria-checked", "true");
    expect(screen.getAllByRole("radio")).toHaveLength(3);
    expect(screen.getByRole("slider", { name: /squat\.mp4, scene 1/ })).toBeInTheDocument();
    expect(screen.queryByRole("slider", { name: /unused/ })).toBeNull();
  });

  it("sends a format change and a framing nudge as changes", async () => {
    getExportSettings.mockResolvedValue({ ok: true, data: settings() });
    changeExport.mockResolvedValue({ ok: true, data: settings({ preset_id: "square" }) });
    render(<ExportPanel projectId={PROJECT} jobs={[]} />);
    fireEvent.click(await screen.findByRole("radio", { name: /Square/ }));
    await waitFor(() => expect(changeExport).toHaveBeenCalledWith(PROJECT, { preset_id: "square" }));
    const slider = screen.getByRole("slider");
    fireEvent.change(slider, { target: { value: "0.5" } });
    fireEvent.pointerUp(slider);
    await waitFor(() =>
      expect(changeExport).toHaveBeenCalledWith(PROJECT, { scene: SCENE, offset: 0.5 }),
    );
  });

  it("shows render progress and blocks a second export while one runs", async () => {
    getExportSettings.mockResolvedValue({ ok: true, data: settings() });
    render(<ExportPanel projectId={PROJECT} jobs={[job({})]} />);
    expect(await screen.findByText("rendering shots")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Export" })).toBeDisabled();
  });

  it("starts an export, then offers the download with stage timings and reuse", async () => {
    getExportSettings.mockResolvedValue({
      ok: true,
      data: settings({
        latest: {
          preset_id: "reel",
          size_bytes: 12_400_000,
          segments_total: 9,
          segments_rendered: 2,
          segments_reused: 7,
          timings_ms: { plan: 3, segments: 41000, music: 900, mux: 400, check: 120 },
          problems: [],
          warnings: ["captions are switched off for this export"],
        },
      }),
    });
    startExport.mockResolvedValue({ ok: true, data: { job_ids: ["j"] } });
    render(<ExportPanel projectId={PROJECT} jobs={[]} />);
    const download = await screen.findByRole("link", { name: "Download" });
    expect(download).toHaveAttribute("href", expect.stringContaining("/export/reel/file"));
    expect(screen.getByText(/7 reused/)).toBeInTheDocument();
    expect(screen.getByText("41.0 s")).toBeInTheDocument();
    expect(screen.getByText(/captions are switched off/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Export again" }));
    expect(startExport).toHaveBeenCalledWith(PROJECT);
  });

  it("names the cause when the last export failed", async () => {
    getExportSettings.mockResolvedValue({ ok: true, data: settings() });
    render(
      <ExportPanel
        projectId={PROJECT}
        jobs={[job({ status: "failed", error: "the exported file is not safe to post: x" })]}
      />,
    );
    expect(await screen.findByText(/not safe to post/)).toBeInTheDocument();
  });
});
