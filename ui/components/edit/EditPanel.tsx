"use client";

import { useCallback, useEffect, useState } from "react";
import { HookStrip } from "@/components/edit/HookStrip";
import { AiSuggested } from "@/components/primitives/AiSuggested";
import { Badge } from "@/components/primitives/Badge";
import { Button } from "@/components/primitives/Button";
import { Panel } from "@/components/primitives/Panel";
import { getEdit, listTracks, measureClips, overrideEdit, scanMusic } from "@/lib/api/client";
import type { ApiResult, EditOverride } from "@/lib/api/client";
import type { Edit, Hero, JobEvent, Library, Shot } from "@/lib/api/schemas";
import { cx } from "@/lib/cx";

export interface EditPanelProps {
  readonly projectId: string;
  /** The project's jobs; the panel refetches when footage or music finishes processing. */
  readonly jobs: readonly JobEvent[];
  /** Clip display names by digest, for the cut labels. */
  readonly clipNames: Readonly<Record<string, string>>;
  /** Media file ids by digest, for the opening candidates' previews. */
  readonly clipIds?: Readonly<Record<string, string>>;
}

const TARGETS = [15, 30, 60] as const;
const REFRESH_ON = new Set(["ingest", "analysis", "music_scan", "edit_signals"]);

function seconds(value: number): string {
  return `${value.toFixed(1)} s`;
}

function percent(value: number, total: number): string {
  return `${total > 0 ? (100 * value) / total : 0}%`;
}

function slow(shot: Shot): boolean {
  return shot.speed < 1;
}

/**
 * The auto-edit: music, length, and the cut list on a beat-marked timeline.
 *
 * Every value here is the engine's suggestion until the person changes it, and
 * `AiSuggested` says which (P2). A change goes to the engine, which re-plans
 * everything that is not locked and logs the taste event (P3), and answers with
 * the whole edit: the panel never computes a cut itself. Shots move to their
 * new places with a layout transition, so a re-sync is visible, not a jump.
 */
export function EditPanel({ projectId, jobs, clipNames, clipIds = {} }: EditPanelProps) {
  const [edit, setEdit] = useState<Edit | null>(null);
  const [library, setLibrary] = useState<Library | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const finished = jobs
    .filter(
      (job) =>
        REFRESH_ON.has(job.job_type) && job.status !== "queued" && job.status !== "running",
    )
    .map((job) => `${job.job_id}:${job.status}`)
    .sort()
    .join(",");

  useEffect(() => {
    let stale = false;
    void Promise.all([getEdit(projectId), listTracks()]).then(([editResult, tracks]) => {
      if (stale) return;
      if (editResult.ok) {
        setEdit(editResult.data);
        setError(null);
      } else {
        setError(editResult.message);
      }
      if (tracks.ok) setLibrary(tracks.data);
    });
    return () => {
      stale = true;
    };
  }, [projectId, finished]);

  const apply = useCallback(
    (change: EditOverride) => {
      setBusy(true);
      void overrideEdit(projectId, change).then((result: ApiResult<Edit>) => {
        setBusy(false);
        if (result.ok) {
          setEdit(result.data);
          setError(null);
        } else {
          setError(result.message);
        }
      });
    },
    [projectId],
  );

  if (edit === null) {
    return error === null ? null : (
      <Panel title="Edit">
        <p className="p-3 text-sm text-fg-muted">{error}</p>
      </Panel>
    );
  }

  const top = edit.recommendations[0];
  const analysed = (library?.tracks ?? []).filter((track) => track.status === "analysed");
  const unusable = (library?.tracks ?? []).filter(
    (track) => track.status === "rejected" || track.status === "failed",
  );
  const total = Math.max(edit.duration_seconds, 0.001);

  return (
    <Panel
      title="Edit"
      scroll
      action={
        <Button size="sm" disabled={busy} onClick={() => apply({ kind: "regenerate" })}>
          Regenerate
        </Button>
      }
    >
      <div className="flex flex-col gap-4 p-3" aria-busy={busy}>
        {error !== null && (
          <p role="alert" className="text-sm text-danger">
            {error}
          </p>
        )}
        {edit.warnings.length > 0 && (
          <ul aria-label="Notes about this edit" className="flex flex-col gap-1">
            {edit.warnings.map((warning) => (
              <li key={warning} className="text-sm text-warning">
                {warning}
              </li>
            ))}
          </ul>
        )}
        {edit.profile_notes.length > 0 && (
          <ul aria-label="What your style changed" className="flex flex-col gap-1">
            {edit.profile_notes.map((note) => (
              <li key={note} className="text-xs text-fg-secondary">
                Your style: {note}
              </li>
            ))}
          </ul>
        )}
        {edit.clips_pending.length > 0 && (
          <div className="flex items-center justify-between gap-3">
            <p className="text-sm text-fg-secondary">
              {edit.clips_pending.length} clip(s) not measured yet. Measuring reads motion,
              shake, loudness and speech, on this machine only.
            </p>
            <Button size="sm" onClick={() => void measureClips(projectId)}>
              Measure footage
            </Button>
          </div>
        )}

        <fieldset className="flex flex-col gap-2">
          <legend className="text-sm text-fg-secondary">Length</legend>
          <div role="radiogroup" aria-label="Edit length" className="flex gap-2">
            {TARGETS.map((target) => {
              const active = Math.round(edit.target_seconds) === target;
              return (
                <button
                  key={target}
                  type="button"
                  role="radio"
                  aria-checked={active}
                  disabled={busy}
                  onClick={() => apply({ kind: "target_duration", target_seconds: target })}
                  className={cx(
                    "rounded-lg border px-3 py-1 text-sm",
                    "transition-colors duration-micro ease-micro",
                    active ? "border-line-strong bg-raised text-fg-primary" : "border-line text-fg-secondary hover:bg-raised",
                  )}
                >
                  {target} s
                </button>
              );
            })}
          </div>
        </fieldset>

        <AiSuggested
          label="Music"
          isSuggestion={edit.music === "suggested"}
          onReset={() => apply({ kind: "music_suggested" })}
          suggestion={top?.display_name ?? "no music"}
          rationale={edit.music === "suggested" ? top?.reason : undefined}
        >
          <div className="flex flex-col gap-3">
            <label className="flex items-center gap-2 text-sm text-fg-primary">
              <input
                type="checkbox"
                checked={edit.music === "off"}
                disabled={busy}
                onChange={(event) =>
                  apply({ kind: event.target.checked ? "music_off" : "music_suggested" })
                }
              />
              Export without music
              <span className="text-fg-muted">
                (the edit stays cut to the beat; add the track inside the platform&apos;s app)
              </span>
            </label>

            {edit.music !== "off" && edit.recommendations.length > 0 && (
              <div role="radiogroup" aria-label="Recommended tracks" className="flex flex-col gap-2">
                {edit.recommendations.map((rec, index) => {
                  const active = rec.sha256 === edit.track_sha256;
                  return (
                    <button
                      key={rec.sha256}
                      type="button"
                      role="radio"
                      aria-checked={active}
                      disabled={busy}
                      data-track={rec.sha256}
                      onClick={() => apply({ kind: "music_track", track_sha256: rec.sha256 })}
                      className={cx(
                        "flex flex-col gap-1 rounded-lg border p-2 text-left",
                        "transition-colors duration-micro ease-micro",
                        active ? "border-line-strong bg-raised" : "border-line hover:bg-raised",
                      )}
                    >
                      <span className="flex items-center gap-2 text-sm text-fg-primary">
                        {rec.display_name}
                        <span className="font-mono text-xs text-fg-muted">{rec.bpm.toFixed(0)} BPM</span>
                        {index === 0 && <Badge tone="ai">AI pick</Badge>}
                      </span>
                      <span className="text-xs text-fg-secondary">{rec.reason}</span>
                    </button>
                  );
                })}
              </div>
            )}

            {edit.music !== "off" && analysed.length > edit.recommendations.length && (
              <label className="flex flex-col gap-1 text-sm text-fg-secondary">
                Whole library
                <select
                  value={edit.track_sha256 ?? ""}
                  disabled={busy}
                  onChange={(event) =>
                    apply({ kind: "music_track", track_sha256: event.target.value })
                  }
                  className="rounded-lg border border-line bg-panel px-2 py-1 text-fg-primary"
                >
                  {analysed.map((track) => (
                    <option key={track.sha256} value={track.sha256}>
                      {track.display_name} ({track.bpm?.toFixed(0)} BPM)
                    </option>
                  ))}
                </select>
              </label>
            )}

            {unusable.length > 0 && (
              <ul aria-label="Tracks that cannot be used" className="flex flex-col gap-1">
                {unusable.map((track) => (
                  <li key={track.sha256} className="text-xs text-fg-muted">
                    {track.display_name}: {track.reason}
                  </li>
                ))}
              </ul>
            )}

            <div className="flex items-center justify-between gap-3">
              <p className="text-xs text-fg-muted">{library?.licence_note}</p>
              <Button size="sm" variant="ghost" onClick={() => void scanMusic()}>
                Scan music folder
              </Button>
            </div>
          </div>
        </AiSuggested>

        <HookStrip edit={edit} busy={busy} apply={apply} clipNames={clipNames} clipIds={clipIds} />

        <section aria-label="Cuts" className="flex flex-col gap-2">
          <div className="flex items-baseline justify-between">
            <h3 className="text-sm text-fg-secondary">
              Cuts {edit.snapped ? "on the beat" : "at the theme's pace (no music)"}
            </h3>
            <span className="font-mono text-xs text-fg-muted">
              {seconds(edit.duration_seconds)} · re-synced in {edit.resync_ms.toFixed(0)} ms
            </span>
          </div>
          <div className="relative h-6 overflow-hidden rounded border border-line" aria-hidden="true">
            {edit.beats.map((beat) => (
              <span
                key={`b${beat}`}
                className="absolute top-3 h-3 w-px bg-line"
                style={{ left: percent(beat, total) }}
              />
            ))}
            {edit.downbeats.map((beat) => (
              <span
                key={`d${beat}`}
                className="absolute top-0 h-6 w-px bg-line-strong"
                style={{ left: percent(beat, total) }}
              />
            ))}
          </div>
          <ol className="relative h-16" aria-label="Cut list">
            {edit.shots.map((shot: Shot, index) => (
              <li
                key={shot.id}
                data-shot-id={shot.id}
                data-locked={shot.locked}
                data-slow={slow(shot)}
                data-hook={shot.hook}
                className={cx(
                  "absolute top-0 flex h-16 flex-col justify-between overflow-hidden rounded border p-1",
                  "transition-[left,width] duration-layout ease-layout",
                  shot.locked ? "border-line-strong bg-raised" : "border-line bg-panel",
                )}
                style={{ left: percent(shot.timeline_in, total), width: percent(shot.timeline_out - shot.timeline_in, total) }}
              >
                <span className="truncate text-xs text-fg-secondary" title={clipNames[shot.sha256] ?? ""}>
                  {index + 1}. {clipNames[shot.sha256] ?? "clip"}
                </span>
                {shot.hook && <Badge tone="ai">Opening</Badge>}
                {slow(shot) && (
                  <span title={shot.hero_reason ?? undefined} className="flex items-center gap-1 truncate">
                    <Badge tone="ai">Slow 2x</Badge>
                    {shot.hero_score !== null && shot.hero_score > 0 && (
                      <span className="font-mono text-xs text-fg-muted">
                        {Math.round(shot.hero_score * 100)}
                      </span>
                    )}
                  </span>
                )}
                <span className="flex gap-1">
                  <button
                    type="button"
                    aria-pressed={shot.locked}
                    aria-label={`${shot.locked ? "Unlock" : "Lock"} cut ${index + 1}`}
                    disabled={busy}
                    onClick={() =>
                      apply({ kind: shot.locked ? "cut_unlocked" : "cut_locked", shot_id: shot.id })
                    }
                    className="rounded border border-line px-1 text-xs text-fg-secondary hover:bg-raised"
                  >
                    {shot.locked ? "Locked" : "Lock"}
                  </button>
                  <button
                    type="button"
                    aria-label={`Remove cut ${index + 1}`}
                    disabled={busy}
                    onClick={() => apply({ kind: "cut_removed", shot_id: shot.id })}
                    className="rounded border border-line px-1 text-xs text-fg-secondary hover:bg-raised"
                  >
                    Remove
                  </button>
                  <button
                    type="button"
                    aria-pressed={slow(shot)}
                    aria-label={`${slow(shot) ? "Play at normal speed" : "Slow down"} cut ${index + 1}`}
                    disabled={busy || shot.locked}
                    title={shot.locked ? "Unlock this cut to change its speed" : undefined}
                    onClick={() =>
                      apply({
                        kind: slow(shot) ? "slowmo_off" : "slowmo_on",
                        segment_id: shot.segment_id,
                      })
                    }
                    className="rounded border border-line px-1 text-xs text-fg-secondary hover:bg-raised"
                  >
                    {slow(shot) ? "1x" : "Slow"}
                  </button>
                </span>
              </li>
            ))}
          </ol>
        </section>

        {edit.heroes.length > 0 && (
          <section aria-label="Hero moments" className="flex flex-col gap-2">
            <h3 className="text-sm text-fg-secondary">
              Hero moments: slowed to half speed, from your real frames, with a check that
              falls back to normal speed if the in-between frames do not hold up
            </h3>
            <ol className="flex flex-col gap-1">
              {edit.heroes.map((hero: Hero) => (
                <li
                  key={hero.segment_id}
                  data-hero={hero.segment_id}
                  className="flex items-center justify-between gap-3 rounded border border-line p-2"
                >
                  <span className="flex flex-col gap-1">
                    <span className="flex items-center gap-2 text-sm text-fg-primary">
                      #{hero.rank} {clipNames[hero.sha256] ?? "clip"} at {seconds(hero.peak)}
                      <span className="font-mono text-xs text-fg-muted">
                        score {Math.round(hero.score * 100)}
                      </span>
                      {hero.slow_in_edit && <Badge tone="ai">Slow 2x</Badge>}
                    </span>
                    <span className="text-xs text-fg-secondary">{hero.reasons.join("; ")}</span>
                  </span>
                  <label className="flex items-center gap-2 text-xs text-fg-secondary">
                    <input
                      type="checkbox"
                      checked={!hero.slowmo_off}
                      disabled={busy}
                      aria-label={`Slow motion for hero moment ${hero.rank}`}
                      onChange={(event) =>
                        apply({
                          kind: event.target.checked ? "slowmo_on" : "slowmo_off",
                          segment_id: hero.segment_id,
                        })
                      }
                    />
                    Slow motion
                  </label>
                </li>
              ))}
            </ol>
          </section>
        )}

        {edit.gain.length > 1 && (
          <figure className="flex flex-col gap-1">
            <svg
              viewBox={`0 0 ${total} 18`}
              preserveAspectRatio="none"
              className="h-10 w-full"
              role="img"
              aria-label="Music volume over the edit: lower under speech, higher on action"
            >
              <polyline
                fill="none"
                stroke="var(--accent)"
                strokeWidth={0.4}
                vectorEffect="non-scaling-stroke"
                points={edit.gain.map((p) => `${p.seconds},${-p.gain_db}`).join(" ")}
              />
            </svg>
            <figcaption className="text-xs text-fg-muted">
              {edit.speech_measured
                ? "Music ducks under speech and lifts on action."
                : "Speech was not measured (the speech model is not installed), so the music is not ducked."}
            </figcaption>
          </figure>
        )}
      </div>
    </Panel>
  );
}
