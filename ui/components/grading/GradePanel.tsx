"use client";

import { useCallback, useEffect, useState } from "react";
import { AiSuggested } from "@/components/primitives/AiSuggested";
import { Button } from "@/components/primitives/Button";
import { Panel } from "@/components/primitives/Panel";
import { Slider } from "@/components/primitives/Slider";
import {
  chooseForClip,
  chooseForScene,
  getGrade,
  getPack,
  gradedPreviewUrl,
  gradedStillUrl,
  resetGrade,
  startGradedPreview,
} from "@/lib/api/client";
import type { ApiResult, ChoiceChange } from "@/lib/api/client";
import { lightingTemperatures, lightingQualities } from "@/lib/api/schemas";
import type { ClipGrade, JobEvent, Pack, SceneGrade } from "@/lib/api/schemas";
import { cx } from "@/lib/cx";
import { formatDuration } from "@/lib/format";

export interface GradePanelProps {
  readonly mediaFileId: string;
  readonly sha256: string;
  /** The project's jobs; the panel refetches when this clip's analysis or grading finishes. */
  readonly jobs: readonly JobEvent[];
}

function themeName(pack: Pack, id: string): string {
  return pack.themes.find((theme) => theme.id === id)?.name ?? id;
}

function revisionOf(scene: SceneGrade): string {
  const c = scene.correction;
  return `${scene.theme_id}-${scene.intensity}-${c.exposure_stops}-${c.temperature_shift_k}`;
}

/**
 * Grading for the selected clip: the look for the whole clip, then each scene.
 *
 * Every value on screen is either the engine's suggestion or the person's
 * choice, and `AiSuggested` says which (P2). Every change goes to the engine,
 * which writes the taste event (P3) and answers with the whole grade, so the
 * panel never computes a dependent value itself: one source of truth.
 */
export function GradePanel({ mediaFileId, sha256, jobs }: GradePanelProps) {
  const [pack, setPack] = useState<Pack | null>(null);
  const [grade, setGrade] = useState<ClipGrade | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [sceneKey, setSceneKey] = useState<string | null>(null);
  // Intensity is sent when the slider is released, not on every step: each
  // send is a taste event, and dragging through 20 values is one decision.
  const [draftIntensity, setDraftIntensity] = useState<number | null>(null);

  const finished = jobs
    .filter(
      (job) =>
        job.sha256 === sha256 &&
        (job.job_type === "analysis" || job.job_type === "grade_preview") &&
        job.status !== "queued" &&
        job.status !== "running",
    )
    .map((job) => `${job.job_id}:${job.status}`)
    .sort()
    .join(",");

  useEffect(() => {
    void getPack("gym").then((result) => {
      if (result.ok) setPack(result.data);
    });
  }, []);

  useEffect(() => {
    let stale = false;
    void getGrade(mediaFileId).then((result) => {
      if (stale) return;
      if (result.ok) {
        setGrade(result.data);
        setError(null);
      } else {
        setGrade(null);
        setError(result.message);
      }
    });
    return () => {
      stale = true;
    };
  }, [mediaFileId, finished]);

  const apply = useCallback((pending: Promise<ApiResult<ClipGrade>>) => {
    void pending.then((result) => {
      if (result.ok) {
        setGrade(result.data);
        setError(null);
      } else {
        setError(result.message);
      }
      setDraftIntensity(null);
    });
  }, []);

  if (grade === null || pack === null || grade.media_file_id !== mediaFileId) {
    return error === null ? null : (
      <Panel title="Grade">
        <p className="p-3 text-sm text-fg-muted">{error}</p>
      </Panel>
    );
  }

  const scene =
    grade.scenes.find((candidate) => candidate.key === sceneKey) ?? grade.scenes[0];
  if (scene === undefined) return null;
  const clipChosen = grade.chosen.theme_id !== null || grade.chosen.intensity !== null;
  const intensity = draftIntensity ?? grade.intensity;
  const change = (body: ChoiceChange) => apply(chooseForClip(mediaFileId, body));
  const changeScene = (body: ChoiceChange) =>
    apply(chooseForScene(mediaFileId, scene.key, body));

  return (
    <Panel
      title="Grade"
      scroll
      action={
        <div className="flex gap-2">
          {!grade.suggested && (
            <Button
              size="sm"
              variant="ghost"
              onClick={() => apply(resetGrade(mediaFileId, { everything: true }))}
            >
              Reset all to AI
            </Button>
          )}
          <Button
            size="sm"
            onClick={() => void startGradedPreview(mediaFileId)}
          >
            Render graded preview
          </Button>
        </div>
      }
    >
      <div className="flex flex-col gap-4 p-3">
        {error !== null && (
          <p role="alert" className="text-sm text-danger">
            {error}
          </p>
        )}

        <AiSuggested
          label="Look for the whole clip"
          isSuggestion={!clipChosen}
          onReset={() => apply(resetGrade(mediaFileId, {}))}
          suggestion={themeName(pack, grade.recommended_theme_id)}
          rationale={
            grade.suggested
              ? "Suggested from the scenes' lighting. Each scene is still corrected for its own light."
              : undefined
          }
        >
          <div
            role="radiogroup"
            aria-label="Theme"
            className="grid grid-cols-5 gap-2"
          >
            {pack.themes.map((theme) => {
              const active = theme.id === grade.theme_id;
              return (
                <button
                  key={theme.id}
                  type="button"
                  role="radio"
                  aria-checked={active}
                  title={theme.description}
                  onClick={() => change({ theme_id: theme.id })}
                  className={cx(
                    "flex flex-col gap-1 rounded-lg border p-1 text-left",
                    "transition-colors duration-micro ease-micro",
                    active ? "border-line-strong bg-raised" : "border-line hover:bg-raised",
                  )}
                >
                  {/* eslint-disable-next-line @next/next/no-img-element -- local engine still, same reasoning as SceneStrip */}
                  <img
                    src={gradedStillUrl(
                      mediaFileId,
                      scene.key,
                      theme.id,
                      theme.id === scene.theme_id ? revisionOf(scene) : undefined,
                    )}
                    alt={`Scene ${scene.sequence_index + 1} graded ${theme.name}`}
                    data-theme={theme.id}
                    // The gate draws these into a canvas (verify-04 #16).
                    crossOrigin="anonymous"
                    className="aspect-video w-full rounded object-cover"
                  />
                  <span className="text-xs text-fg-secondary">
                    {theme.name}
                    {theme.id === grade.recommended_theme_id && (
                      <span className="text-accent"> · AI</span>
                    )}
                  </span>
                </button>
              );
            })}
          </div>
          <div
            // Released, not dragged: see draftIntensity above.
            onPointerUp={() => {
              if (draftIntensity !== null) change({ intensity: draftIntensity });
            }}
            onKeyUp={() => {
              if (draftIntensity !== null) change({ intensity: draftIntensity });
            }}
          >
            <Slider
              label="Intensity"
              value={intensity}
              min={0}
              max={100}
              display={`${intensity}%`}
              suggested={grade.chosen.intensity === null && draftIntensity === null}
              onChange={setDraftIntensity}
            />
          </div>
        </AiSuggested>

        <div className="flex gap-2 overflow-x-auto" role="tablist" aria-label="Scenes">
          {grade.scenes.map((candidate) => (
            <button
              key={candidate.key}
              type="button"
              role="tab"
              aria-selected={candidate.key === scene.key}
              onClick={() => setSceneKey(candidate.key)}
              className={cx(
                "shrink-0 rounded-lg border px-2 py-1 text-xs",
                candidate.key === scene.key
                  ? "border-line-strong bg-raised text-fg-primary"
                  : "border-line text-fg-muted",
              )}
            >
              Scene {candidate.sequence_index + 1}
              {!candidate.suggested && " ·"}
            </button>
          ))}
        </div>

        <AiSuggested
          label={`Scene ${scene.sequence_index + 1}`}
          isSuggestion={
            Object.values(scene.chosen).every((value) => value === null)
          }
          onReset={() => apply(resetGrade(mediaFileId, { sceneKey: scene.key }))}
          suggestion={`${themeName(pack, grade.theme_id)} with the analysed lighting`}
          rationale={`${formatDuration(scene.start_seconds)}–${formatDuration(scene.end_seconds)} · correction ${scene.correction.exposure_stops >= 0 ? "+" : ""}${scene.correction.exposure_stops.toFixed(2)} stops, ${scene.correction.temperature_shift_k >= 0 ? "+" : ""}${Math.round(scene.correction.temperature_shift_k)} K`}
        >
          <div className="grid grid-cols-3 gap-2 text-sm">
            <label className="flex flex-col gap-1 text-fg-secondary">
              Theme
              <select
                aria-label="Scene theme"
                value={scene.theme_id}
                onChange={(event) => changeScene({ theme_id: event.target.value })}
                className="rounded-lg border border-line bg-raised px-2 py-1 text-fg-primary"
              >
                {pack.themes.map((theme) => (
                  <option key={theme.id} value={theme.id}>
                    {theme.name}
                  </option>
                ))}
              </select>
            </label>
            <label className="flex flex-col gap-1 text-fg-secondary">
              Light colour
              <select
                aria-label="Scene light colour"
                value={scene.effective.lighting_temperature ?? ""}
                onChange={(event) =>
                  changeScene({ lighting_temperature: event.target.value })
                }
                className="rounded-lg border border-line bg-raised px-2 py-1 text-fg-primary"
              >
                <option value="" disabled>
                  Not analysed
                </option>
                {lightingTemperatures.map((value) => (
                  <option key={value} value={value}>
                    {value}
                  </option>
                ))}
              </select>
            </label>
            <label className="flex flex-col gap-1 text-fg-secondary">
              Light quality
              <select
                aria-label="Scene light quality"
                value={scene.effective.lighting_quality ?? ""}
                onChange={(event) => changeScene({ lighting_quality: event.target.value })}
                className="rounded-lg border border-line bg-raised px-2 py-1 text-fg-primary"
              >
                <option value="" disabled>
                  Not analysed
                </option>
                {lightingQualities.map((value) => (
                  <option key={value} value={value}>
                    {value}
                  </option>
                ))}
              </select>
            </label>
          </div>
          {scene.preview_ready ? (
            <video
              key={revisionOf(scene)}
              src={gradedPreviewUrl(mediaFileId, scene.key)}
              crossOrigin="anonymous"
              controls
              muted
              playsInline
              aria-label={`Scene ${scene.sequence_index + 1}, graded preview`}
              className="w-full rounded-lg bg-black"
            />
          ) : (
            // eslint-disable-next-line @next/next/no-img-element -- see above
            <img
              src={gradedStillUrl(mediaFileId, scene.key, undefined, revisionOf(scene))}
              alt={`Scene ${scene.sequence_index + 1}, graded still`}
              data-graded-current="true"
              crossOrigin="anonymous"
              className="w-full rounded-lg"
            />
          )}
        </AiSuggested>
      </div>
    </Panel>
  );
}
