"use client";

import { Badge } from "@/components/primitives/Badge";
import { Button } from "@/components/primitives/Button";
import { Progress } from "@/components/primitives/Progress";
import type { JobEvent } from "@/lib/api/schemas";
import type { StreamStatus } from "@/lib/jobs/useJobStream";

/**
 * What the engine's job types are called on screen.
 *
 * A fallback rather than an exhaustive `Record`: a newer engine may run a job
 * type this build has never heard of, and showing its raw name is honest —
 * rendering "Working" for everything would be the uninformative wait the design
 * system exists to prevent.
 */
const JOB_LABEL: Record<string, string> = {
  ingest: "Preparing clip",
};

function labelFor(job: JobEvent): string {
  return JOB_LABEL[job.job_type] ?? job.job_type;
}

const STREAM_NOTE: Record<StreamStatus, string | null> = {
  connecting: "Connecting to the engine…",
  open: null,
  closed: "Lost the connection to the engine. Reconnecting…",
};

export interface JobListProps {
  readonly jobs: readonly JobEvent[];
  readonly status: StreamStatus;
  readonly onCancel: (jobId: string) => void;
}

/**
 * Live engine jobs.
 *
 * Every job shows a percentage, a named step and — while it can still be
 * stopped — a cancel control, per `.claude/rules/frontend-and-licensing.md`.
 * A failure shows the engine's own sentence.
 *
 * The stream's own health is rendered too. A socket that dropped looks
 * identical to an idle engine otherwise: bars frozen, no explanation, which is
 * exactly the state that reads as a hung app.
 */
export function JobList({ jobs, status, onCancel }: JobListProps) {
  const note = STREAM_NOTE[status];

  if (jobs.length === 0) {
    return (
      <p className="px-4 py-3 text-sm text-fg-muted">
        {note ?? "No jobs running."}
      </p>
    );
  }

  return (
    <div className="flex flex-col gap-3 px-4 py-3">
      {note !== null && <p className="text-xs text-warning">{note}</p>}

      <ul className="flex flex-col gap-3">
        {jobs.map((job) => {
          const active = job.status === "queued" || job.status === "running";
          const tone =
            job.status === "failed" || job.status === "cancelled"
              ? "failed"
              : job.status === "succeeded"
                ? "succeeded"
                : "running";

          return (
            <li key={job.job_id} className="flex flex-col gap-2">
              <div className="flex items-center justify-between gap-3">
                <span className="truncate text-sm text-fg-primary">
                  {labelFor(job)}
                </span>
                <div className="flex shrink-0 items-center gap-2">
                  <Badge tone={tone === "failed" ? "danger" : "neutral"} label="job status">
                    {job.status}
                  </Badge>
                  {active && (
                    <Button
                      size="sm"
                      variant="ghost"
                      onClick={() => onCancel(job.job_id)}
                    >
                      Cancel
                    </Button>
                  )}
                </div>
              </div>

              <Progress
                value={job.progress}
                label={`${labelFor(job)} progress`}
                step={job.step ?? job.status}
                tone={tone}
              />

              {job.error !== null && (
                <p role="alert" className="text-xs text-danger">
                  {job.error}
                </p>
              )}
            </li>
          );
        })}
      </ul>
    </div>
  );
}
