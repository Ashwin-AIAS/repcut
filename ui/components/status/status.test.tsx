import { render, screen, waitFor } from "@testing-library/react";
import axe from "axe-core";
import { afterEach, describe, expect, it, vi } from "vitest";
import { JobStreamProbe } from "@/components/status/JobStreamProbe";
import type { StreamStatus } from "@/lib/jobs/useJobStream";

/**
 * The row that makes `/status` stop lying.
 *
 * Every other row on that page reports what the *engine* said, and the engine
 * was healthy the whole time the jobs panel sat on "Connecting to the engine…" —
 * the browser was refusing the socket under the page's own
 * Content-Security-Policy. This row is the only one that can see that, so its
 * blocked state is the one that has to be right.
 */

const stream = vi.hoisted(() => ({
  status: "connecting" as StreamStatus,
  attempts: 0,
}));

vi.mock("@/lib/jobs/useJobStream", () => ({
  useJobStream: () => ({ jobs: [], status: stream.status, attempts: stream.attempts }),
}));

/** The probe renders a `<dt>`/`<dd>` pair, so it needs a `<dl>` around it. */
function renderProbe() {
  return render(
    <dl>
      <JobStreamProbe />
    </dl>,
  );
}

afterEach(() => {
  stream.status = "connecting";
  stream.attempts = 0;
});

describe("JobStreamProbe", () => {
  it("reports nothing until the socket has resolved either way", () => {
    renderProbe();

    expect(screen.getByText("Checking…")).toBeInTheDocument();
    expect(screen.getByText("This browser can reach it")).toBeInTheDocument();
  });

  it("reports a retry as blocked, not as still checking", async () => {
    // "connecting" after a failed attempt and "connecting" before the first one
    // are the same status, and only one of them means the product works.
    stream.status = "connecting";
    stream.attempts = 2;
    renderProbe();

    await waitFor(() =>
      expect(screen.getByText(/jobs will not report progress/)).toBeInTheDocument(),
    );
  });

  it("reports the stream reachable once the socket opens", async () => {
    stream.status = "open";
    renderProbe();

    await waitFor(() => expect(screen.getByText("Yes")).toBeInTheDocument());
    expect(
      screen.getByText(/Job progress will stream live into the editor/),
    ).toBeInTheDocument();
  });

  it("reports it blocked when the socket closes, and says what that costs", async () => {
    stream.status = "closed";
    renderProbe();

    await waitFor(() =>
      expect(screen.getByText(/jobs will not report progress/)).toBeInTheDocument(),
    );
    // The fix has to be actionable without opening a devtools console.
    expect(screen.getByText(/NEXT_PUBLIC_ENGINE_URL/)).toBeInTheDocument();
  });

  it("never says the engine is unreachable in a raw error or a path", async () => {
    stream.status = "closed";
    renderProbe();

    await waitFor(() =>
      expect(screen.getByText(/jobs will not report progress/)).toBeInTheDocument(),
    );
    // `.claude/rules/secrets.md`: no absolute path may reach the screen.
    expect(document.body.textContent ?? "").not.toMatch(/[A-Za-z]:[\\/]Users[\\/]/);
  });

  it("has no accessibility violations", async () => {
    stream.status = "closed";
    const { container } = renderProbe();

    const results = await axe.run(container, {
      rules: { "color-contrast": { enabled: false } },
    });

    expect(results.violations).toEqual([]);
  });
});
