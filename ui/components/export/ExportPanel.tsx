"use client";

import { useCallback, useEffect, useState } from "react";
import { AiSuggested } from "@/components/primitives/AiSuggested";
import { Button } from "@/components/primitives/Button";
import { Panel } from "@/components/primitives/Panel";
import { Progress } from "@/components/primitives/Progress";
import { Slider } from "@/components/primitives/Slider";
import { changeExport, exportFileUrl, getExportSettings, startExport } from "@/lib/api/client";
import type { ApiResult, ExportChange } from "@/lib/api/client";
import type { ExportScene, ExportSettings, JobEvent } from "@/lib/api/schemas";
import { cx } from "@/lib/cx";

export interface ExportPanelProps {
  readonly projectId: string;
  readonly jobs: readonly JobEvent[];
}

const STAGES: Record<string, string> = {
  plan: "Plan",
  segments: "Shots (grade, reframe, captions)",
  music: "Music stem",
  mux: "Mix and join",
  check: "Platform check",
};

function megabytes(bytes: number): string {
  return `${(bytes / 1_000_000).toFixed(1)} MB`;
}

function offsetLabel(offset: number): string {
  if (Math.abs(offset) < 0.01) return "centre";
  return `${offset < 0 ? "←" : "→"} ${Math.round(Math.abs(offset) * 100)}%`;
}

/**
 * Export: the preset, a hand nudge of the centre crop per scene, the render.
 *
 * The render is a job with progress over the jobs socket and runs on this
 * machine (P4). A re-run after a crash or a change re-renders only the shots
 * that changed; the panel says how many it reused. Every choice is the
 * engine's default until changed (P2) and logged when changed (P3). Whether
 * music is in the file is the Edit panel's "Export without music".
 */
export function ExportPanel({ projectId, jobs }: ExportPanelProps) {
  const [settings, setSettings] = useState<ExportSettings | null>(null);
  const [offsets, setOffsets] = useState<Record<string, number>>({});
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const exportJobs = jobs.filter((job) => job.job_type === "export");
  const running = exportJobs.find((job) => job.status === "queued" || job.status === "running");
  const lastFailed = exportJobs.at(-1)?.status === "failed" ? exportJobs.at(-1) : undefined;
  const finished = exportJobs
    .filter((job) => job !== running)
    .map((job) => `${job.job_id}:${job.status}`)
    .join(",");

  useEffect(() => {
    let stale = false;
    void getExportSettings(projectId).then((result) => {
      if (stale) return;
      if (result.ok) {
        setSettings(result.data);
        setOffsets(Object.fromEntries(result.data.scenes.map((s) => [s.key, s.offset])));
        setError(null);
      } else {
        setError(result.message);
      }
    });
    return () => {
      stale = true;
    };
  }, [projectId, finished]);

  const apply = useCallback(
    (change: ExportChange) => {
      setBusy(true);
      void changeExport(projectId, change).then((result: ApiResult<ExportSettings>) => {
        setBusy(false);
        if (result.ok) {
          setSettings(result.data);
          setError(null);
        } else {
          setError(result.message);
        }
      });
    },
    [projectId],
  );

  const render = () => {
    setBusy(true);
    void startExport(projectId).then((result) => {
      setBusy(false);
      setError(result.ok ? null : result.message);
    });
  };

  if (settings === null) {
    return error === null ? null : (
      <Panel title="Export">
        <p className="p-3 text-sm text-fg-muted">{error}</p>
      </Panel>
    );
  }

  const preset = settings.presets.find((p) => p.id === settings.preset_id);
  const suggested = settings.presets.find((p) => p.id === settings.suggested_preset_id);
  const scenes = settings.scenes.filter((scene: ExportScene) => scene.in_edit);
  const latest = settings.latest;

  return (
    <Panel
      title="Export"
      scroll
      action={
        <Button size="sm" disabled={busy || running !== undefined} onClick={render}>
          {latest ? "Export again" : "Export"}
        </Button>
      }
    >
      <div className="flex flex-col gap-4 p-3" aria-busy={busy}>
        {error !== null && (
          <p role="alert" className="text-sm text-danger">
            {error}
          </p>
        )}
        <p className="text-xs text-fg-muted">
          Rendered on this machine from your original files; nothing is uploaded.
        </p>

        <AiSuggested
          label="Format"
          isSuggestion={settings.preset_id === settings.suggested_preset_id}
          onReset={() => apply({ preset_id: settings.suggested_preset_id })}
          suggestion={suggested?.label ?? settings.suggested_preset_id}
        >
          <div role="radiogroup" aria-label="Export format" className="flex gap-2">
            {settings.presets.map((option) => {
              const active = option.id === settings.preset_id;
              return (
                <button
                  key={option.id}
                  type="button"
                  role="radio"
                  aria-checked={active}
                  disabled={busy}
                  onClick={() => apply({ preset_id: option.id })}
                  className={cx(
                    "flex flex-col rounded-lg border px-3 py-1 text-left text-sm",
                    "transition-colors duration-micro ease-micro",
                    active ? "border-line-strong bg-raised text-fg-primary" : "border-line text-fg-secondary hover:bg-raised",
                  )}
                >
                  {option.label}
                  <span className="font-mono text-xs text-fg-muted">
                    {option.width}×{option.height}
                  </span>
                </button>
              );
            })}
          </div>
        </AiSuggested>

        {scenes.length > 0 && (
          <section aria-label="Framing" className="flex flex-col gap-3">
            <h3 className="text-sm text-fg-secondary">
              Framing for {preset?.label ?? "this format"}: centre crop, nudged per scene
            </h3>
            {scenes.map((scene) => {
              const value = offsets[scene.key] ?? 0;
              return (
                <div
                  key={scene.key}
                  data-scene={scene.key}
                  onPointerUp={() => apply({ scene: scene.key, offset: value })}
                  onKeyUp={() => apply({ scene: scene.key, offset: value })}
                >
                  <Slider
                    label={`${scene.clip_name}, scene ${scene.index + 1}`}
                    value={value}
                    min={-1}
                    max={1}
                    step={0.05}
                    display={offsetLabel(value)}
                    suggested={Math.abs(scene.offset) < 0.01 && Math.abs(value) < 0.01}
                    disabled={busy}
                    onChange={(next) => setOffsets({ ...offsets, [scene.key]: next })}
                  />
                </div>
              );
            })}
          </section>
        )}

        {running !== undefined && (
          <Progress
            value={running.progress}
            label="Export"
            step={running.step ?? "queued"}
            tone="running"
          />
        )}
        {running === undefined && lastFailed !== undefined && (
          <p role="alert" className="text-sm text-danger">
            The export failed: {lastFailed.error ?? "no cause was given"}
          </p>
        )}

        {latest !== null && (
          <section aria-label="Last export" className="flex flex-col gap-2">
            <div className="flex items-center justify-between gap-3">
              <span className="text-sm text-fg-primary">
                {preset?.label} · {megabytes(latest.size_bytes)} · {latest.segments_total} shots
                {latest.segments_reused > 0 && ` (${latest.segments_reused} reused)`}
              </span>
              <a
                href={exportFileUrl(projectId, latest.preset_id)}
                download
                className="rounded-lg border border-line-strong px-3 py-1 text-sm text-fg-primary hover:bg-raised"
              >
                Download
              </a>
            </div>
            {latest.problems.length > 0 && (
              <ul aria-label="Why this file is not safe to post" className="flex flex-col gap-1">
                {latest.problems.map((problem) => (
                  <li key={problem} className="text-sm text-danger">
                    {problem}
                  </li>
                ))}
              </ul>
            )}
            {latest.warnings.length > 0 && (
              <ul aria-label="Notes about this export" className="flex flex-col gap-1">
                {latest.warnings.map((warning) => (
                  <li key={warning} className="text-sm text-warning">
                    {warning}
                  </li>
                ))}
              </ul>
            )}
            <dl className="grid grid-cols-2 gap-x-3 gap-y-1 text-xs">
              {Object.entries(latest.timings_ms).map(([stage, ms]) => (
                <div key={stage} className="contents">
                  <dt className="text-fg-muted">{STAGES[stage] ?? stage}</dt>
                  <dd className="font-mono text-fg-secondary">{(ms / 1000).toFixed(1)} s</dd>
                </div>
              ))}
            </dl>
          </section>
        )}
      </div>
    </Panel>
  );
}
