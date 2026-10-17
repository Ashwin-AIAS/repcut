"use client";

import { useCallback, useEffect, useState } from "react";
import { Badge } from "@/components/primitives/Badge";
import { Button } from "@/components/primitives/Button";
import { confirmGymSet, countReps, dismissGymSet, getGymSets } from "@/lib/api/client";
import type { ApiResult } from "@/lib/api/client";
import type { GymSet, GymSets, JobEvent } from "@/lib/api/schemas";

export interface SetCaptionsProps {
  readonly projectId: string;
  readonly jobs: readonly JobEvent[];
}

const REFRESH_ON = new Set(["gym_reps", "edit_signals", "analysis"]);

function clock(seconds: number): string {
  const minutes = Math.floor(seconds / 60);
  return `${minutes}:${(seconds - minutes * 60).toFixed(1).padStart(4, "0")}`;
}

interface Draft {
  reps: string;
  exercise: string;
  weight: string;
  unit: "kg" | "lb";
}

function draftOf(set: GymSet): Draft {
  return {
    reps: String(set.reps ?? set.reps_suggested ?? ""),
    exercise: set.exercise ?? set.exercise_suggested ?? "",
    weight: set.weight === null ? "" : String(set.weight),
    unit: set.weight_unit,
  };
}

function wholeOrNull(text: string): number | null {
  const value = Number.parseInt(text, 10);
  return Number.isFinite(value) ? value : null;
}

function numberOrNull(text: string): number | null {
  const value = Number.parseFloat(text.replace(",", "."));
  return Number.isFinite(value) ? value : null;
}

/**
 * Set captions for the edit's best moments: "Back squat · 8 reps · 100 kg".
 *
 * Every number is a suggestion until the person confirms it (P2): the counted
 * reps and the guessed exercise fill the fields, the weight is one tap per
 * set (plates are never read off the picture), and nothing is burned into an
 * export before "Confirm". Repcut counts reps; it never scores form.
 */
export function SetCaptions({ projectId, jobs }: SetCaptionsProps) {
  const [data, setData] = useState<GymSets | null>(null);
  const [drafts, setDrafts] = useState<Record<string, Draft>>({});
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const counting = jobs.some(
    (job) => job.job_type === "gym_reps" && (job.status === "queued" || job.status === "running"),
  );
  const finished = jobs
    .filter((job) => REFRESH_ON.has(job.job_type) && job.status === "succeeded")
    .map((job) => job.job_id)
    .join(",");

  useEffect(() => {
    let stale = false;
    void getGymSets(projectId).then((result) => {
      if (stale) return;
      if (result.ok) {
        setData(result.data);
        setDrafts(Object.fromEntries(result.data.sets.map((s) => [s.id, draftOf(s)])));
        setError(null);
      } else {
        setError(result.message);
      }
    });
    return () => {
      stale = true;
    };
  }, [projectId, finished]);

  const settle = useCallback((promise: Promise<ApiResult<GymSet>>) => {
    setBusy(true);
    void promise.then((result) => {
      setBusy(false);
      if (result.ok) {
        const updated = result.data;
        setData((old) =>
          old === null
            ? old
            : { ...old, sets: old.sets.map((s) => (s.id === updated.id ? updated : s)) },
        );
        setError(null);
      } else {
        setError(result.message);
      }
    });
  }, []);

  if (data === null || data.sets.length === 0) {
    return error === null ? null : <p className="text-sm text-fg-muted">{error}</p>;
  }

  const edit = (id: string, change: Partial<Draft>) =>
    setDrafts((old) => ({ ...old, [id]: { ...(old[id] as Draft), ...change } }));

  return (
    <section aria-label="Set captions" className="flex flex-col gap-3">
      <div className="flex items-center justify-between gap-3">
        <h3 className="text-sm text-fg-secondary">Set captions on your best moments</h3>
        <Button
          size="sm"
          variant="secondary"
          disabled={busy || counting}
          onClick={() =>
            void countReps(projectId).then((r) => setError(r.ok ? null : r.message))
          }
        >
          {counting ? "Counting…" : "Count reps"}
        </Button>
      </div>
      {error !== null && (
        <p role="alert" className="text-sm text-danger">
          {error}
        </p>
      )}
      <ul className="flex flex-col gap-3">
        {data.sets.map((set) => {
          const draft = drafts[set.id] ?? draftOf(set);
          const label = `the set at ${clock(set.start_seconds)}`;
          return (
            <li key={set.id} data-set={set.id} className="flex flex-col gap-2 rounded-lg border border-line p-2">
              <div className="flex items-center justify-between gap-2 text-xs text-fg-muted">
                <span className="font-mono">
                  {clock(set.start_seconds)}–{clock(set.end_seconds)}
                </span>
                {set.status === "confirmed" ? (
                  <Badge tone="positive">confirmed</Badge>
                ) : set.status === "dismissed" ? (
                  <Badge>not shown</Badge>
                ) : (
                  <Badge tone="ai">suggested</Badge>
                )}
              </div>
              {set.reps_suggested === null && set.reason !== null && (
                <p className="text-xs text-fg-muted">{set.reason}</p>
              )}
              <div className="flex flex-wrap items-end gap-2">
                <label className="flex flex-col text-xs text-fg-secondary">
                  Exercise
                  <input
                    value={draft.exercise}
                    maxLength={60}
                    disabled={busy}
                    onChange={(e) => edit(set.id, { exercise: e.target.value })}
                    className="w-40 rounded border border-line bg-surface px-2 py-1 text-sm text-fg-primary"
                  />
                </label>
                <label className="flex flex-col text-xs text-fg-secondary">
                  Reps
                  <input
                    inputMode="numeric"
                    value={draft.reps}
                    disabled={busy}
                    onChange={(e) => edit(set.id, { reps: e.target.value })}
                    className="w-16 rounded border border-line bg-surface px-2 py-1 text-sm text-fg-primary"
                  />
                </label>
                <label className="flex flex-col text-xs text-fg-secondary">
                  Weight
                  <input
                    inputMode="decimal"
                    value={draft.weight}
                    disabled={busy}
                    onChange={(e) => edit(set.id, { weight: e.target.value })}
                    className="w-20 rounded border border-line bg-surface px-2 py-1 text-sm text-fg-primary"
                  />
                </label>
                <label className="flex flex-col text-xs text-fg-secondary">
                  Unit
                  <select
                    value={draft.unit}
                    disabled={busy}
                    onChange={(e) => edit(set.id, { unit: e.target.value === "lb" ? "lb" : "kg" })}
                    className="rounded border border-line bg-surface px-2 py-1 text-sm text-fg-primary"
                  >
                    <option value="kg">kg</option>
                    <option value="lb">lb</option>
                  </select>
                </label>
                <Button
                  size="sm"
                  disabled={busy}
                  aria-label={`Confirm ${label}`}
                  onClick={() =>
                    settle(
                      confirmGymSet(projectId, set.id, {
                        reps: wholeOrNull(draft.reps),
                        exercise: draft.exercise.trim() === "" ? null : draft.exercise.trim(),
                        weight: numberOrNull(draft.weight),
                        weight_unit: draft.unit,
                      }),
                    )
                  }
                >
                  Confirm
                </Button>
                {set.status !== "dismissed" && (
                  <Button
                    size="sm"
                    variant="ghost"
                    disabled={busy}
                    aria-label={`Leave out ${label}`}
                    onClick={() => settle(dismissGymSet(projectId, set.id))}
                  >
                    Leave out
                  </Button>
                )}
              </div>
              {set.caption !== null && (
                <p className="text-sm text-fg-primary">
                  In the export: <span className="font-mono">{set.caption}</span>
                </p>
              )}
            </li>
          );
        })}
      </ul>
      <p className="text-xs text-fg-muted">{data.boundary}</p>
    </section>
  );
}
