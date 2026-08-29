"use client";

import { useEffect, useState } from "react";
import { EnergySparkline } from "@/components/analysis/EnergySparkline";
import { PrivacyDisclosure } from "@/components/analysis/PrivacyDisclosure";
import { SceneStrip } from "@/components/analysis/SceneStrip";
import { Panel } from "@/components/primitives/Panel";
import { listScenes } from "@/lib/api/client";
import type { JobEvent, Scene } from "@/lib/api/schemas";

export interface AnalysisPanelProps {
  readonly sha256: string;
  /** The project's jobs, unfiltered by type — this panel picks out its own. */
  readonly jobs: readonly JobEvent[];
}

/**
 * Per-clip scene analysis: sampled frames and Gemini tags, an energy curve,
 * and the P4 disclosure at the moment frames are actually sent.
 *
 * Fetches with the same shape `Workspace` uses for the media library —
 * `useEffect` plus a discriminated result, no data-fetching library for one
 * panel — rather than inventing a second pattern for one more list.
 *
 * Mounts only once scenes exist. Detection finishes before any Gemini call is
 * made, so the scene list is usually non-empty well before a single tag has
 * landed: the panel appears with "not yet analyzed" cards and fills in as the
 * job progresses, instead of waiting for the whole analysis job to finish. If
 * the fetch fails (engine unreachable, clip not yet in the library) this
 * renders nothing rather than an error banner — there is nothing on screen
 * yet for an error to explain, and the library's own error state already
 * covers an unreachable engine.
 */
export function AnalysisPanel({ sha256, jobs }: AnalysisPanelProps) {
  const [scenes, setScenes] = useState<readonly Scene[]>([]);

  const analysisJobs = jobs.filter(
    (job) => job.job_type === "analysis" && job.sha256 === sha256,
  );
  const currentStep = analysisJobs.find((job) => job.status === "running")?.step ?? null;
  // Refetch once this clip's analysis job leaves the queue/running states —
  // the same "terminal job -> refetch" signal `Workspace` uses for the
  // library, scoped to this clip so a different clip's job finishing does not
  // refetch scenes nobody is looking at.
  const finished = analysisJobs
    .filter((job) => job.status !== "queued" && job.status !== "running")
    .map((job) => `${job.job_id}:${job.status}`)
    .sort()
    .join(",");

  useEffect(() => {
    let stale = false;
    void listScenes(sha256).then((result) => {
      if (stale) return;
      setScenes(result.ok ? result.data : []);
    });
    return () => {
      stale = true;
    };
  }, [sha256, finished]);

  if (scenes.length === 0) return null;

  return (
    <Panel title="Scene analysis" scroll>
      <div className="flex flex-col gap-4 p-3">
        <PrivacyDisclosure step={currentStep} />
        <EnergySparkline scenes={scenes} />
        <SceneStrip sha256={sha256} scenes={scenes} />
      </div>
    </Panel>
  );
}
