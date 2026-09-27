import { render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { AnalysisPanel } from "@/components/analysis/AnalysisPanel";
import type { ApiResult } from "@/lib/api/client";
import type { Scene } from "@/lib/api/schemas";

const listScenes = vi.fn<(sha256: string) => Promise<ApiResult<Scene[]>>>();

vi.mock("@/lib/api/client", () => ({
  listScenes: (sha256: string) => listScenes(sha256),
}));

const CLIP_A = "a".repeat(64);
const CLIP_B = "b".repeat(64);

function scene(sha256: string, id: string): Scene {
  return {
    id,
    sha256,
    sequence_index: 0,
    start_seconds: 0,
    end_seconds: 4,
    start_frame_source: 0,
    end_frame_source: 120,
    has_sampled_frame: false,
    motion_energy: 0.5,
    audio_energy: 0.3,
    energy_score: 0.4,
    vlm: {
      content_type: `content-of-${id}`,
      exercise_guess: null,
      environment: null,
      lighting_quality: null,
      lighting_temperature: null,
      lighting_direction: null,
      energy_level: null,
      aesthetic_notes: null,
    },
    created_at: "2026-08-10T09:00:00Z",
  };
}

beforeEach(() => {
  listScenes.mockReset();
});

describe("AnalysisPanel", () => {
  it("does not show the previous clip's scenes while the new clip's fetch is pending", async () => {
    listScenes.mockResolvedValueOnce({ ok: true, data: [scene(CLIP_A, "scene-a")] });
    const { rerender, container } = render(<AnalysisPanel sha256={CLIP_A} jobs={[]} />);
    await screen.findByText(/content-of-scene-a/);

    listScenes.mockReturnValueOnce(new Promise(() => {}));
    rerender(<AnalysisPanel sha256={CLIP_B} jobs={[]} />);

    expect(screen.queryByText(/content-of-scene-a/)).toBeNull();
    expect(container).toBeEmptyDOMElement();
  });
});
