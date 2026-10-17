import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { SetCaptions } from "@/components/gym/SetCaptions";
import type { ApiResult, GymSetConfirm } from "@/lib/api/client";
import type { GymSet, GymSets } from "@/lib/api/schemas";

const getGymSets = vi.fn<(id: string) => Promise<ApiResult<GymSets>>>();
const confirmGymSet = vi.fn<(p: string, s: string, v: GymSetConfirm) => Promise<ApiResult<GymSet>>>();
const dismissGymSet = vi.fn<(p: string, s: string) => Promise<ApiResult<GymSet>>>();
const countReps = vi.fn();

vi.mock("@/lib/api/client", () => ({
  getGymSets: (id: string) => getGymSets(id),
  confirmGymSet: (p: string, s: string, v: GymSetConfirm) => confirmGymSet(p, s, v),
  dismissGymSet: (p: string, s: string) => dismissGymSet(p, s),
  countReps: (id: string) => countReps(id),
}));

const PROJECT = "22222222-2222-4222-8222-222222222222";
const BOUNDARY = "Repcut counts reps; it never scores form or analyses a body.";

function set(over: Partial<GymSet> = {}): GymSet {
  return {
    id: "s1",
    segment_id: "seg-1",
    start_seconds: 12,
    end_seconds: 31.5,
    status: "suggested",
    reps_suggested: 8,
    exercise_suggested: "Back squat",
    reason: null,
    suggestion: "Back squat · 8 reps",
    reps: null,
    exercise: null,
    weight: null,
    weight_unit: "kg",
    caption: null,
    ...over,
  };
}

describe("SetCaptions", () => {
  beforeEach(() => {
    getGymSets.mockReset();
    confirmGymSet.mockReset();
    dismissGymSet.mockReset();
    countReps.mockReset();
  });

  it("fills the suggestion in, and burns nothing until confirmed with the person's numbers", async () => {
    getGymSets.mockResolvedValue({
      ok: true,
      data: { project_id: PROJECT, sets: [set()], boundary: BOUNDARY },
    });
    confirmGymSet.mockResolvedValue({
      ok: true,
      data: set({ status: "confirmed", reps: 7, exercise: "Back squat", weight: 100, caption: "Back squat · 7 reps · 100 kg" }),
    });
    render(<SetCaptions projectId={PROJECT} jobs={[]} />);
    const reps = await screen.findByLabelText("Reps");
    expect(reps).toHaveValue("8");
    expect(screen.getByLabelText("Exercise")).toHaveValue("Back squat");
    expect(screen.getByText("suggested")).toBeInTheDocument();
    expect(screen.queryByText(/In the export/)).not.toBeInTheDocument();
    expect(screen.getByText(BOUNDARY)).toBeInTheDocument();

    fireEvent.change(reps, { target: { value: "7" } });
    fireEvent.change(screen.getByLabelText("Weight"), { target: { value: "100" } });
    fireEvent.click(screen.getByRole("button", { name: "Confirm the set at 0:12.0" }));
    await waitFor(() =>
      expect(confirmGymSet).toHaveBeenCalledWith(PROJECT, "s1", {
        reps: 7,
        exercise: "Back squat",
        weight: 100,
        weight_unit: "kg",
      }),
    );
    expect(await screen.findByText("Back squat · 7 reps · 100 kg")).toBeInTheDocument();
    expect(screen.getByText("confirmed")).toBeInTheDocument();
  });

  it("says why there is no count, and counting is a job", async () => {
    getGymSets.mockResolvedValue({
      ok: true,
      data: {
        project_id: PROJECT,
        sets: [set({ reps_suggested: null, reason: "rep counting needs PyTorch and ultralytics (make setup-gpu); type the reps yourself, or leave them out" })],
        boundary: BOUNDARY,
      },
    });
    countReps.mockResolvedValue({ ok: true, data: { job_ids: ["j1"] } });
    render(<SetCaptions projectId={PROJECT} jobs={[]} />);
    expect(await screen.findByText(/needs PyTorch/)).toBeInTheDocument();
    expect(screen.getByLabelText("Reps")).toHaveValue("");
    fireEvent.click(screen.getByRole("button", { name: "Count reps" }));
    await waitFor(() => expect(countReps).toHaveBeenCalledWith(PROJECT));
  });

  it("can leave a set out", async () => {
    getGymSets.mockResolvedValue({ ok: true, data: { project_id: PROJECT, sets: [set()], boundary: BOUNDARY } });
    dismissGymSet.mockResolvedValue({ ok: true, data: set({ status: "dismissed" }) });
    render(<SetCaptions projectId={PROJECT} jobs={[]} />);
    fireEvent.click(await screen.findByRole("button", { name: "Leave out the set at 0:12.0" }));
    expect(await screen.findByText("not shown")).toBeInTheDocument();
  });

  it("renders nothing when the edit has no best moments yet", async () => {
    getGymSets.mockResolvedValue({ ok: true, data: { project_id: PROJECT, sets: [], boundary: BOUNDARY } });
    const { container } = render(<SetCaptions projectId={PROJECT} jobs={[]} />);
    await waitFor(() => expect(getGymSets).toHaveBeenCalled());
    expect(container).toBeEmptyDOMElement();
  });
});
