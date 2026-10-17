import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { StylePanel } from "@/components/profile/StylePanel";
import type { ApiResult } from "@/lib/api/client";
import type { Profile, ProfileItem } from "@/lib/api/schemas";

const getProfile = vi.fn<() => Promise<ApiResult<Profile>>>();
const setProfileEnabled = vi.fn<(on: boolean) => Promise<ApiResult<Profile>>>();
const resetProfile = vi.fn<(item: string) => Promise<ApiResult<Profile>>>();

vi.mock("@/lib/api/client", () => ({
  getProfile: () => getProfile(),
  setProfileEnabled: (on: boolean) => setProfileEnabled(on),
  resetProfile: (item: string) => resetProfile(item),
}));

function item(over: Partial<ProfileItem>): ProfileItem {
  return {
    item: "theme",
    active: true,
    events: 10,
    why: "You chose Moody over the suggested theme 10 times (of 10 theme changes); it now ranks higher in theme suggestions.",
    value: null,
    leanings: { moody: 0.27 },
    reset_at: null,
    ...over,
  };
}

function profile(over: Partial<Profile> = {}): Profile {
  return {
    enabled: true,
    items: [
      item({}),
      item({ item: "pacing", why: "From slow motion turned off 10 times: edits are cut faster (shots about 30% shorter).", value: 0.7, leanings: {} }),
      item({ item: "music_energy", active: false, events: 0, why: "No music changes yet.", value: 0, leanings: {} }),
      item({ item: "caption_style", active: false, events: 2, why: "You picked the Classic caption style twice; new edits start with it.", leanings: {} }),
    ],
    notes: [],
    ...over,
  };
}

describe("StylePanel", () => {
  beforeEach(() => {
    getProfile.mockReset();
    setProfileEnabled.mockReset();
    resetProfile.mockReset();
  });

  it("shows what was learned, why, and what is still learning", async () => {
    getProfile.mockResolvedValue({ ok: true, data: profile() });
    render(<StylePanel />);
    expect(await screen.findByText(/You chose Moody over the suggested theme/)).toBeInTheDocument();
    expect(screen.getByText(/shots about 30% shorter/)).toBeInTheDocument();
    expect(screen.getByText("still learning")).toBeInTheDocument();
    // An item with no evidence is not shown at all: nothing to explain.
    expect(screen.queryByText("No music changes yet.")).not.toBeInTheDocument();
    expect(screen.getAllByText("in use")).toHaveLength(2);
  });

  it("forgets one item, and tells the edit to re-plan", async () => {
    getProfile.mockResolvedValue({ ok: true, data: profile() });
    resetProfile.mockResolvedValue({
      ok: true,
      data: profile({ items: [item({ active: false, events: 0, why: "No theme changes yet." })] }),
    });
    const changed = vi.fn();
    render(<StylePanel onChanged={changed} />);
    fireEvent.click(await screen.findByRole("button", { name: "Forget Theme" }));
    await waitFor(() => expect(resetProfile).toHaveBeenCalledWith("theme"));
    await waitFor(() => expect(changed).toHaveBeenCalled());
  });

  it("can be switched off; learned items then read as paused", async () => {
    getProfile.mockResolvedValue({ ok: true, data: profile() });
    setProfileEnabled.mockResolvedValue({ ok: true, data: profile({ enabled: false }) });
    render(<StylePanel />);
    const toggle = await screen.findByRole("button", { name: "On" });
    expect(toggle).toHaveAttribute("aria-pressed", "true");
    fireEvent.click(toggle);
    await waitFor(() => expect(setProfileEnabled).toHaveBeenCalledWith(false));
    expect(await screen.findAllByText("paused")).toHaveLength(2);
  });

  it("says plainly when nothing has been learned", async () => {
    getProfile.mockResolvedValue({
      ok: true,
      data: profile({ items: [item({ active: false, events: 0, why: "No theme changes yet." })] }),
    });
    render(<StylePanel />);
    expect(await screen.findByText(/Nothing learned yet/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Forget everything" })).not.toBeInTheDocument();
  });
});
