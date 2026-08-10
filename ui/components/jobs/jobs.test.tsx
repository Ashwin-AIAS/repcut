import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import axe from "axe-core";
import { describe, expect, it, vi } from "vitest";
import { JobList } from "@/components/jobs/JobList";
import type { JobEvent } from "@/lib/api/schemas";

function job(overrides: Partial<JobEvent> = {}): JobEvent {
  return {
    job_id: "job-1",
    job_type: "ingest",
    status: "running",
    progress: 0.4,
    step: "encoding the preview",
    error: null,
    project_id: "project-1",
    sha256: "a".repeat(64),
    updated_at: "2026-08-10T09:15:00Z",
    ...overrides,
  };
}

describe("JobList", () => {
  it("shows a percentage and the named step, never a bare bar", () => {
    render(<JobList jobs={[job()]} status="open" onCancel={() => {}} />);

    const bar = screen.getByRole("progressbar", { name: "Preparing clip progress" });
    expect(bar).toHaveAttribute("aria-valuenow", "40");
    expect(screen.getByText("encoding the preview")).toBeInTheDocument();
  });

  it("renders an unknown job type by name rather than as 'Working'", () => {
    render(
      <JobList jobs={[job({ job_type: "analyze-scenes" })]} status="open" onCancel={() => {}} />,
    );

    expect(screen.getByText("analyze-scenes")).toBeInTheDocument();
  });

  it("shows a failure's cause, and announces it", () => {
    render(
      <JobList
        jobs={[
          job({
            status: "failed",
            error: "this clip's file is missing from the media library",
          }),
        ]}
        status="open"
        onCancel={() => {}}
      />,
    );

    expect(screen.getByRole("alert")).toHaveTextContent(
      "this clip's file is missing from the media library",
    );
  });

  it.each(["queued", "running"] as const)("offers cancel while %s", async (status) => {
    const onCancel = vi.fn();
    render(<JobList jobs={[job({ status })]} status="open" onCancel={onCancel} />);

    await userEvent.click(screen.getByRole("button", { name: "Cancel" }));

    expect(onCancel).toHaveBeenCalledWith("job-1");
  });

  it.each(["succeeded", "failed", "cancelled"] as const)(
    "offers no cancel once %s",
    (status) => {
      render(<JobList jobs={[job({ status })]} status="open" onCancel={() => {}} />);

      expect(screen.queryByRole("button", { name: "Cancel" })).toBeNull();
    },
  );

  /**
   * A dropped socket looks exactly like an idle engine otherwise: bars frozen,
   * no explanation. That is the state a user reads as a hung app, so the
   * stream's own health is rendered rather than assumed.
   */
  it("says when the stream is not connected", () => {
    render(<JobList jobs={[job()]} status="closed" onCancel={() => {}} />);

    expect(
      screen.getByText("Lost the connection to the engine. Reconnecting…"),
    ).toBeInTheDocument();
  });

  it("says it is connecting rather than claiming there are no jobs", () => {
    render(<JobList jobs={[]} status="connecting" onCancel={() => {}} />);

    expect(screen.getByText("Connecting to the engine…")).toBeInTheDocument();
    expect(screen.queryByText("No jobs running.")).toBeNull();
  });

  it("has no accessibility violations", async () => {
    const { container } = render(
      <JobList
        jobs={[job(), job({ job_id: "job-2", status: "succeeded", progress: 1 })]}
        status="open"
        onCancel={() => {}}
      />,
    );

    const results = await axe.run(container, {
      rules: { "color-contrast": { enabled: false } },
    });

    expect(results.violations).toEqual([]);
  });
});
