import { render, screen, within } from "@testing-library/react";
import axe from "axe-core";
import { describe, expect, it } from "vitest";
import { EnergySparkline } from "@/components/analysis/EnergySparkline";
import { parseSendingStep, PrivacyDisclosure } from "@/components/analysis/PrivacyDisclosure";
import { SceneStrip } from "@/components/analysis/SceneStrip";
import type { Scene } from "@/lib/api/schemas";

const SHA256 = "d".repeat(64);

function scene(overrides: Partial<Scene> = {}): Scene {
  return {
    id: "scene-1",
    sha256: SHA256,
    sequence_index: 0,
    start_seconds: 0,
    end_seconds: 4,
    start_frame_source: 0,
    end_frame_source: 120,
    has_sampled_frame: true,
    motion_energy: 0.5,
    audio_energy: 0.3,
    energy_score: 0.4,
    vlm: null,
    created_at: "2026-08-10T09:00:00Z",
    ...overrides,
  };
}

describe("SceneStrip", () => {
  it("shows a tagged scene's content type, exercise guess and environment", () => {
    render(
      <SceneStrip
        sha256={SHA256}
        scenes={[
          scene({
            vlm: {
              content_type: "exercise demonstration",
              exercise_guess: "barbell back squat",
              environment: "home gym",
              lighting_quality: "even",
              lighting_temperature: "neutral",
              lighting_direction: "front",
              energy_level: "high",
              aesthetic_notes: "handheld, slight motion blur",
            },
          }),
        ]}
      />,
    );

    const card = screen.getByRole("group", { name: /Scene 1/ });
    expect(within(card).getByText("exercise demonstration")).toBeInTheDocument();
    expect(within(card).getByText("barbell back squat")).toBeInTheDocument();
    expect(within(card).getByText("home gym")).toBeInTheDocument();
    // The denser lighting/aesthetic fields sit behind a native `<details>`
    // disclosure rather than on the card face.
    const disclosure = within(card).getByText("More detail").closest("details");
    expect(disclosure).not.toBeNull();
    expect(disclosure).not.toHaveAttribute("open");
    expect(within(card).getByText("even")).toBeInTheDocument();
  });

  /**
   * `vlm: null` collapses three different reasons on the wire — not reached
   * yet, degraded, or a response that never parsed — and the UI renders all
   * three as one state rather than guessing which happened
   * (`SceneResponse.vlm`'s doc comment, `engine/repcut/api/schemas.py`).
   */
  it("shows 'not yet analyzed' when vlm is null, without inventing a reason", () => {
    render(<SceneStrip sha256={SHA256} scenes={[scene({ vlm: null })]} />);

    expect(screen.getByText("Not yet analyzed")).toBeInTheDocument();
  });

  it("does not render an image when the sampler has not produced a frame yet", () => {
    render(
      <SceneStrip sha256={SHA256} scenes={[scene({ has_sampled_frame: false })]} />,
    );

    expect(screen.queryByRole("img")).toBeNull();
    expect(screen.getByText("No sampled frame yet")).toBeInTheDocument();
  });

  it("renders the sampled frame from the sha256-keyed scene route once it exists", () => {
    render(
      <SceneStrip
        sha256={SHA256}
        scenes={[scene({ id: "scene-9", has_sampled_frame: true })]}
      />,
    );

    const image = screen.getByRole("group", { name: /Scene 1/ }).querySelector("img");
    expect(image).not.toBeNull();
    expect(image?.getAttribute("src")).toContain(`/media/${SHA256}/scenes/scene-9/frame`);
  });

  it("orders cards by sequence_index regardless of input order", () => {
    render(
      <SceneStrip
        sha256={SHA256}
        scenes={[
          scene({ id: "b", sequence_index: 1 }),
          scene({ id: "a", sequence_index: 0 }),
        ]}
      />,
    );

    const groups = screen.getAllByRole("group");
    expect(groups[0]).toHaveAccessibleName(/Scene 1/);
    expect(groups[1]).toHaveAccessibleName(/Scene 2/);
  });

  it("has no accessibility violations", async () => {
    const { container } = render(
      <SceneStrip
        sha256={SHA256}
        scenes={[
          scene({ id: "a", sequence_index: 0, vlm: null }),
          scene({
            id: "b",
            sequence_index: 1,
            vlm: {
              content_type: "exercise demonstration",
              exercise_guess: "deadlift",
              environment: "gym floor",
              lighting_quality: null,
              lighting_temperature: null,
              lighting_direction: null,
              energy_level: null,
              aesthetic_notes: null,
            },
          }),
        ]}
      />,
    );

    const results = await axe.run(container, {
      rules: { "color-contrast": { enabled: false } },
    });

    expect(results.violations).toEqual([]);
  });
});

describe("EnergySparkline", () => {
  it("charts a real energy array, in order", () => {
    render(
      <EnergySparkline
        scenes={[
          scene({ id: "a", sequence_index: 0, energy_score: 0.2, start_seconds: 0, end_seconds: 4 }),
          scene({ id: "b", sequence_index: 1, energy_score: 0.9, start_seconds: 4, end_seconds: 9 }),
          scene({ id: "c", sequence_index: 2, energy_score: 0.4, start_seconds: 9, end_seconds: 13 }),
        ]}
      />,
    );

    expect(screen.getByRole("img", { name: /Energy across 3 scenes/ })).toBeInTheDocument();
  });

  it("says energy has not been scored rather than drawing a flat zero line", () => {
    render(
      <EnergySparkline
        scenes={[scene({ energy_score: null }), scene({ id: "b", sequence_index: 1, energy_score: null })]}
      />,
    );

    expect(screen.getByText("Energy has not been scored yet.")).toBeInTheDocument();
    expect(screen.queryByRole("img")).toBeNull();
  });

  it("says there is nothing to chart for an empty scene list", () => {
    render(<EnergySparkline scenes={[]} />);

    expect(screen.getByText("No scenes to chart yet.")).toBeInTheDocument();
  });
});

describe("parseSendingStep", () => {
  it("reads the engine's exact wording", () => {
    expect(parseSendingStep("sending scene 2 of 5 to Gemini for analysis")).toEqual({
      index: 2,
      total: 5,
    });
  });

  it.each([null, "encoding the preview", "sending scenes to Gemini"])(
    "does not match %s",
    (step) => {
      expect(parseSendingStep(step)).toBeNull();
    },
  );
});

describe("PrivacyDisclosure", () => {
  it("appears at the moment a frame is sent, naming which one", () => {
    render(<PrivacyDisclosure step="sending scene 3 of 7 to Gemini for analysis" />);

    expect(screen.getByRole("status")).toHaveTextContent(
      "Sending frame 3 of 7 to Gemini for analysis.",
    );
    expect(screen.getByText(/nothing else/i)).toBeInTheDocument();
  });

  it("renders nothing outside that moment", () => {
    const { container } = render(<PrivacyDisclosure step="encoding the preview" />);

    expect(container).toBeEmptyDOMElement();
  });

  it("renders nothing when there is no step at all", () => {
    const { container } = render(<PrivacyDisclosure step={null} />);

    expect(container).toBeEmptyDOMElement();
  });

  it("has no accessibility violations while visible", async () => {
    const { container } = render(
      <PrivacyDisclosure step="sending scene 1 of 1 to Gemini for analysis" />,
    );

    const results = await axe.run(container, {
      rules: { "color-contrast": { enabled: false } },
    });

    expect(results.violations).toEqual([]);
  });
});
