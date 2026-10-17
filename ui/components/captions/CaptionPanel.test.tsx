import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { CaptionPanel } from "@/components/captions/CaptionPanel";
import type { ApiResult, CaptionChange } from "@/lib/api/client";
import type { Captions } from "@/lib/api/schemas";

const getCaptions = vi.fn<(id: string) => Promise<ApiResult<Captions>>>();
const changeCaptions = vi.fn<(id: string, c: CaptionChange) => Promise<ApiResult<Captions>>>();
const transcribeClips = vi.fn();

vi.mock("@/lib/api/client", () => ({
  getCaptions: (id: string) => getCaptions(id),
  changeCaptions: (id: string, c: CaptionChange) => changeCaptions(id, c),
  transcribeClips: (id: string) => transcribeClips(id),
  // Set captions (Prompt 10) sit in this panel; with no hero moments they render nothing.
  getGymSets: (id: string) =>
    Promise.resolve({ ok: true, data: { project_id: id, sets: [], boundary: "" } }),
}));

const PROJECT = "22222222-2222-4222-8222-222222222222";
const LINE = `${"a".repeat(16)}-0`;
const HOSTILE = '"<script>alert(1)</script>\\N{\\c&H0000FF&}';

function captions(over: Partial<Captions> = {}): Captions {
  return {
    project_id: PROJECT,
    enabled: true,
    style_id: "punch",
    suggested_style_id: "punch",
    styles: [
      { id: "punch", name: "Punch", description: "Big bold words." },
      { id: "classic", name: "Classic", description: "Clean subtitles." },
      { id: "minimal", name: "Minimal", description: "Lower third." },
    ],
    lines: [
      {
        id: LINE,
        start: 1.0,
        end: 2.2,
        hidden: false,
        words: [
          { id: `${LINE}-0`, text: "noch", original: "noch", start: 1.0, end: 1.3, probability: 0.92, edited: false },
          { id: `${LINE}-1`, text: HOSTILE, original: HOSTILE, start: 1.4, end: 2.2, probability: 0.3, edited: false },
        ],
      },
    ],
    model_installed: true,
    model_message: null,
    clips_untranscribed: [],
    languages: ["de", "en"],
    dropped_segments: 2,
    ...over,
  };
}

describe("CaptionPanel", () => {
  beforeEach(() => {
    getCaptions.mockReset();
    changeCaptions.mockReset();
    transcribeClips.mockReset();
  });

  it("renders hostile caption text literally, never as markup", async () => {
    getCaptions.mockResolvedValue({ ok: true, data: captions() });
    const { container } = render(<CaptionPanel projectId={PROJECT} jobs={[]} />);
    expect(await screen.findByText(HOSTILE)).toBeInTheDocument();
    expect(container.querySelector("script")).toBeNull();
    expect(screen.getByText(/2 likely-invented line/)).toBeInTheDocument();
    expect(screen.getByText(/de \+ en/)).toBeInTheDocument();
  });

  it("edits a word by id and sends the typed text as data", async () => {
    getCaptions.mockResolvedValue({ ok: true, data: captions() });
    changeCaptions.mockResolvedValue({ ok: true, data: captions() });
    render(<CaptionPanel projectId={PROJECT} jobs={[]} />);
    fireEvent.click(await screen.findByRole("button", { name: /^noch, / }));
    const input = screen.getByRole("textbox", { name: /Edit the word/ });
    fireEvent.change(input, { target: { value: "<b>NOCH</b>" } });
    fireEvent.keyDown(input, { key: "Enter" });
    await waitFor(() =>
      expect(changeCaptions).toHaveBeenCalledWith(PROJECT, {
        kind: "word",
        word_id: `${LINE}-0`,
        text: "<b>NOCH</b>",
      }),
    );
  });

  it("escape cancels an edit without a change", async () => {
    getCaptions.mockResolvedValue({ ok: true, data: captions() });
    render(<CaptionPanel projectId={PROJECT} jobs={[]} />);
    fireEvent.click(await screen.findByRole("button", { name: /^noch, / }));
    fireEvent.keyDown(screen.getByRole("textbox"), { key: "Escape" });
    expect(changeCaptions).not.toHaveBeenCalled();
  });

  it("switches style, hides a line and turns captions off, each as one change", async () => {
    getCaptions.mockResolvedValue({ ok: true, data: captions() });
    changeCaptions.mockResolvedValue({ ok: true, data: captions({ style_id: "classic" }) });
    render(<CaptionPanel projectId={PROJECT} jobs={[]} />);
    fireEvent.click(await screen.findByRole("radio", { name: /Classic/ }));
    await waitFor(() => expect(changeCaptions).toHaveBeenCalledTimes(1));
    expect(await screen.findByText("Your choice")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /Hide the line/ }));
    fireEvent.click(screen.getByRole("checkbox", { name: /Burn captions/ }));
    await waitFor(() => expect(changeCaptions).toHaveBeenCalledTimes(3));
    expect(changeCaptions.mock.calls.map((call) => call[1].kind)).toEqual(["style", "line", "enabled"]);
  });

  it("says when the speech model is missing instead of showing an empty transcript as truth", async () => {
    getCaptions.mockResolvedValue({
      ok: true,
      data: captions({ model_installed: false, model_message: "run scripts/fetch_models.py", lines: [] }),
    });
    render(<CaptionPanel projectId={PROJECT} jobs={[]} />);
    expect(await screen.findByText(/fetch_models/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Transcribe" })).toBeNull();
  });

  it("offers transcription for clips without a transcript", async () => {
    getCaptions.mockResolvedValue({ ok: true, data: captions({ clips_untranscribed: ["x"] }) });
    render(<CaptionPanel projectId={PROJECT} jobs={[]} />);
    fireEvent.click(await screen.findByRole("button", { name: "Transcribe" }));
    expect(transcribeClips).toHaveBeenCalledWith(PROJECT);
  });
});
