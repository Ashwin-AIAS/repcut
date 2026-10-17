import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { GradePanel } from "@/components/grading/GradePanel";
import type { ApiResult, ChoiceChange } from "@/lib/api/client";
import type { ClipGrade, Pack, SceneGrade } from "@/lib/api/schemas";

const getGrade = vi.fn<(id: string) => Promise<ApiResult<ClipGrade>>>();
const chooseForClip = vi.fn<(id: string, c: ChoiceChange) => Promise<ApiResult<ClipGrade>>>();
const chooseForScene =
  vi.fn<(id: string, scene: string, c: ChoiceChange) => Promise<ApiResult<ClipGrade>>>();
const resetGrade = vi.fn<
  (id: string, scope: { sceneKey?: string; everything?: boolean }) => Promise<ApiResult<ClipGrade>>
>();
const startGradedPreview = vi.fn();

const PACK: Pack = {
  id: "gym",
  name: "Gym",
  description: "d",
  default_theme: "raw-plus",
  themes: ["raw-plus", "moody", "hardstyle", "aesthetic", "old-school"].map((id) => ({
    id,
    name: id.toUpperCase(),
    description: `about ${id}`,
    default_intensity: 80,
  })),
};

vi.mock("@/lib/api/client", () => ({
  getPack: () => Promise.resolve({ ok: true, data: PACK }),
  getGrade: (id: string) => getGrade(id),
  chooseForClip: (id: string, c: ChoiceChange) => chooseForClip(id, c),
  chooseForScene: (id: string, s: string, c: ChoiceChange) => chooseForScene(id, s, c),
  resetGrade: (id: string, scope: { sceneKey?: string; everything?: boolean }) =>
    resetGrade(id, scope),
  startGradedPreview: (id: string) => startGradedPreview(id),
  gradedStillUrl: (id: string, scene: string, theme?: string) =>
    `still/${id}/${scene}/${theme ?? "current"}`,
  gradedPreviewUrl: (id: string, scene: string) => `preview/${id}/${scene}`,
}));

const CLIP = "11111111-1111-4111-8111-111111111111";

function sceneGrade(key: string, index: number, over: Partial<SceneGrade> = {}): SceneGrade {
  return {
    key,
    sequence_index: index,
    start_seconds: index * 2,
    end_seconds: index * 2 + 2,
    analysed: { lighting_temperature: null, lighting_quality: null },
    effective: { lighting_temperature: null, lighting_quality: null },
    chosen: { theme_id: null, intensity: null, lighting_temperature: null, lighting_quality: null },
    theme_id: "raw-plus",
    intensity: 100,
    suggested: true,
    correction: { exposure_stops: 0.2, temperature_shift_k: -100, denoise: 0, rules: [] },
    preview_ready: false,
    ...over,
  };
}

function grade(over: Partial<ClipGrade> = {}): ClipGrade {
  return {
    media_file_id: CLIP,
    pack_id: "gym",
    recommended_theme_id: "raw-plus",
    recommended_why: null,
    scores: {},
    chosen: { theme_id: null, intensity: null, lighting_temperature: null, lighting_quality: null },
    theme_id: "raw-plus",
    intensity: 100,
    suggested: true,
    scenes: [sceneGrade("s-1", 0), sceneGrade("s-2", 1)],
    ...over,
  };
}

beforeEach(() => {
  for (const mock of [getGrade, chooseForClip, chooseForScene, resetGrade, startGradedPreview]) {
    mock.mockReset();
  }
});

describe("GradePanel", () => {
  it("shows five graded thumbnails and marks the state as the AI's suggestion", async () => {
    getGrade.mockResolvedValue({ ok: true, data: grade() });
    render(<GradePanel mediaFileId={CLIP} sha256={"a".repeat(64)} jobs={[]} />);

    const radios = await screen.findAllByRole("radio");
    expect(radios).toHaveLength(5);
    expect(radios[0]).toHaveAttribute("aria-checked", "true");
    expect(screen.getAllByText("AI suggested").length).toBeGreaterThanOrEqual(2);
    expect(screen.queryByRole("button", { name: /Reset all/ })).toBeNull();
  });

  it("picking a theme sends one clip choice and renders the engine's answer", async () => {
    getGrade.mockResolvedValue({ ok: true, data: grade() });
    chooseForClip.mockResolvedValue({
      ok: true,
      data: grade({
        theme_id: "moody",
        suggested: false,
        chosen: { theme_id: "moody", intensity: null, lighting_temperature: null, lighting_quality: null },
      }),
    });
    render(<GradePanel mediaFileId={CLIP} sha256={"a".repeat(64)} jobs={[]} />);

    fireEvent.click(await screen.findByRole("radio", { name: /MOODY/ }));

    expect(chooseForClip).toHaveBeenCalledExactlyOnceWith(CLIP, { theme_id: "moody" });
    await waitFor(() =>
      expect(screen.getByRole("radio", { name: /MOODY/ })).toHaveAttribute("aria-checked", "true"),
    );
    expect(screen.getByText("Your choice")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Reset all/ })).toBeInTheDocument();
  });

  it("a scene's lighting override goes to that scene, and its reset clears only it", async () => {
    const overridden = grade({
      suggested: false,
      scenes: [
        sceneGrade("s-1", 0),
        sceneGrade("s-2", 1, {
          suggested: false,
          effective: { lighting_temperature: "warm", lighting_quality: null },
          chosen: { theme_id: null, intensity: null, lighting_temperature: "warm", lighting_quality: null },
        }),
      ],
    });
    getGrade.mockResolvedValue({ ok: true, data: grade() });
    chooseForScene.mockResolvedValue({ ok: true, data: overridden });
    resetGrade.mockResolvedValue({ ok: true, data: grade() });
    render(<GradePanel mediaFileId={CLIP} sha256={"a".repeat(64)} jobs={[]} />);

    fireEvent.click(await screen.findByRole("tab", { name: /Scene 2/ }));
    fireEvent.change(screen.getByLabelText("Scene light colour"), { target: { value: "warm" } });
    expect(chooseForScene).toHaveBeenCalledExactlyOnceWith(CLIP, "s-2", {
      lighting_temperature: "warm",
    });

    const reset = await screen.findByRole("button", { name: /Reset Scene 2 to the AI suggestion/ });
    fireEvent.click(reset);
    expect(resetGrade).toHaveBeenCalledExactlyOnceWith(CLIP, { sceneKey: "s-2" });
  });

  it("an intensity drag is one choice, sent on release", async () => {
    getGrade.mockResolvedValue({ ok: true, data: grade() });
    chooseForClip.mockResolvedValue({ ok: true, data: grade({ intensity: 40 }) });
    render(<GradePanel mediaFileId={CLIP} sha256={"a".repeat(64)} jobs={[]} />);

    const slider = await screen.findByRole("slider", { name: "Intensity" });
    fireEvent.change(slider, { target: { value: "60" } });
    fireEvent.change(slider, { target: { value: "40" } });
    expect(chooseForClip).not.toHaveBeenCalled();
    fireEvent.pointerUp(slider);
    expect(chooseForClip).toHaveBeenCalledExactlyOnceWith(CLIP, { intensity: 40 });
  });
});
