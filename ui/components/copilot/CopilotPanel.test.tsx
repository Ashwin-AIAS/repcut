import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { CopilotPanel } from "@/components/copilot/CopilotPanel";
import type { ApiResult } from "@/lib/api/client";
import type { Copilot, CopilotMessage, CopilotReply, History, Undone } from "@/lib/api/schemas";

const getCopilot = vi.fn<(id: string) => Promise<ApiResult<Copilot>>>();
const getHistory = vi.fn<(id: string) => Promise<ApiResult<History>>>();
const sendCopilotMessage = vi.fn<(id: string, text: string) => Promise<ApiResult<CopilotReply>>>();
const undoChange = vi.fn<(id: string, entry?: string) => Promise<ApiResult<Undone>>>();

vi.mock("@/lib/api/client", () => ({
  getCopilot: (id: string) => getCopilot(id),
  getHistory: (id: string) => getHistory(id),
  sendCopilotMessage: (id: string, text: string) => sendCopilotMessage(id, text),
  undoChange: (id: string, entry?: string) => undoChange(id, entry),
}));

const PROJECT = "33333333-3333-4333-8333-333333333333";
const ENTRY = "44444444-4444-4444-8444-444444444444";

function copilot(over: Partial<Copilot> = {}): Copilot {
  return { project_id: PROJECT, available: true, reason: null, messages: [], ...over };
}

function reply(over: Partial<CopilotMessage> = {}): CopilotMessage {
  return {
    id: "m2",
    role: "assistant",
    text: "Theme set to moody on every clip.",
    outcome: "done",
    actions: [
      {
        operation: "set_theme",
        summary: "Theme set to moody on every clip.",
        entry_id: ENTRY,
        changed: true,
        undone: false,
        undoable: true,
      },
    ],
    ...over,
  };
}

describe("CopilotPanel", () => {
  beforeEach(() => {
    getCopilot.mockReset();
    getHistory.mockReset();
    sendCopilotMessage.mockReset();
    undoChange.mockReset();
    getHistory.mockResolvedValue({ ok: true, data: { project_id: PROJECT, entries: [] } });
  });

  it("is disabled with the reason when the copilot is off, and says what would be sent", async () => {
    const reason = "The copilot is off: no Gemini key is configured. Every control still works.";
    getCopilot.mockResolvedValue({ ok: true, data: copilot({ available: false, reason }) });
    render(<CopilotPanel projectId={PROJECT} revision={0} onChanged={() => {}} />);
    expect(await screen.findByRole("status")).toHaveTextContent(reason);
    expect(screen.getByLabelText("Tell the copilot what to change")).toBeDisabled();
    expect(screen.getByRole("button", { name: "Send" })).toBeDisabled();
    expect(screen.getByText(/No footage, no frames/)).toBeInTheDocument();
  });

  it("a dead engine is a reason, not a crash", async () => {
    getCopilot.mockResolvedValue({ ok: false, code: "unreachable", message: "The engine is not running." });
    render(<CopilotPanel projectId={PROJECT} revision={0} onChanged={() => {}} />);
    expect(await screen.findByRole("status")).toHaveTextContent("The engine is not running.");
  });

  it("sends, renders each change as a card, and undoes it by its entry", async () => {
    // Empty first; after the turn the engine has both lines saved.
    const user: CopilotMessage = { id: "m1", role: "user", text: "moodier colours", outcome: null, actions: [] };
    getCopilot
      .mockResolvedValueOnce({ ok: true, data: copilot() })
      .mockResolvedValue({ ok: true, data: copilot({ messages: [user, reply()] }) });
    sendCopilotMessage.mockResolvedValue({
      ok: true,
      data: { reply: reply(), available: true, reason: null },
    });
    undoChange.mockResolvedValue({
      ok: true,
      data: { entry_id: ENTRY, operation: "set_theme", summary: "Theme set to moody on every clip." },
    });
    const onChanged = vi.fn();
    render(<CopilotPanel projectId={PROJECT} revision={0} onChanged={onChanged} />);
    const box = await screen.findByLabelText("Tell the copilot what to change");
    fireEvent.change(box, { target: { value: "  moodier colours  " } });
    fireEvent.keyDown(box, { key: "Enter" });
    await waitFor(() => expect(sendCopilotMessage).toHaveBeenCalledWith(PROJECT, "moodier colours"));
    const card = await screen.findByRole("list", { name: "Changes" });
    expect(card).toHaveTextContent("Theme set to moody on every clip.");
    expect(onChanged).toHaveBeenCalledTimes(1);
    fireEvent.click(screen.getByRole("button", { name: "Undo" }));
    await waitFor(() => expect(undoChange).toHaveBeenCalledWith(PROJECT, ENTRY));
    expect(onChanged).toHaveBeenCalledTimes(2);
  });

  it("renders text as text, and a card that is undone offers no undo", async () => {
    getCopilot.mockResolvedValue({
      ok: true,
      data: copilot({
        messages: [
          { id: "m1", role: "user", text: "<img src=x onerror=alert(1)>", outcome: null, actions: [] },
          reply({
            actions: [
              { ...reply().actions[0]!, undone: true, undoable: false },
            ],
          }),
        ],
      }),
    });
    render(<CopilotPanel projectId={PROJECT} revision={0} onChanged={() => {}} />);
    expect(await screen.findByText("<img src=x onerror=alert(1)>")).toBeInTheDocument();
    expect(document.querySelector("img")).toBeNull();
    expect(screen.getByText("Undone")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Undo" })).toBeNull();
  });

  it("shows the shared history with which door each change came through", async () => {
    getCopilot.mockResolvedValue({ ok: true, data: copilot() });
    getHistory.mockResolvedValue({
      ok: true,
      data: {
        project_id: PROJECT,
        entries: [
          { id: ENTRY, source: "copilot", operation: "set_theme", summary: "Theme set.", undone: false },
          { id: "e0", source: "ui", operation: "remove_cut", summary: "Cut removed.", undone: true },
        ],
      },
    });
    render(<CopilotPanel projectId={PROJECT} revision={0} onChanged={() => {}} />);
    fireEvent.click(await screen.findByRole("tab", { name: "History" }));
    const log = await screen.findByRole("list", { name: "Change history" });
    expect(log).toHaveTextContent("Chat");
    expect(log).toHaveTextContent("Control");
    expect(screen.getAllByRole("button", { name: "Undo" })).toHaveLength(1);
  });
});
