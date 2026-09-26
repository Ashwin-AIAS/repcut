"use client";

import { sceneFrameUrl } from "@/lib/api/client";
import type { Scene, SceneVlm } from "@/lib/api/schemas";
import { formatDuration } from "@/lib/format";
import { Badge } from "@/components/primitives/Badge";

export interface SceneStripProps {
  readonly sha256: string;
  readonly scenes: readonly Scene[];
}

/**
 * The fields shown behind `<details>` rather than on the card face.
 *
 * `content_type` / `exercise_guess` / `environment` are the three a person
 * scanning the strip wants first — what is it, what's the movement, where is
 * it. The rest is real but denser (lighting has three separate axes), so it
 * is one native disclosure widget away rather than crowding every card.
 */
const DETAIL_FIELDS: ReadonlyArray<readonly [keyof SceneVlm, string]> = [
  ["lighting_quality", "Lighting quality"],
  ["lighting_temperature", "Lighting temperature"],
  ["lighting_direction", "Lighting direction"],
  ["energy_level", "Energy level"],
  ["aesthetic_notes", "Aesthetic notes"],
];

/**
 * One scene's card: its sampled frame once the sampler has written one, its
 * Gemini tags once analysis has reached it, or a plain "not yet analyzed"
 * state.
 *
 * `vlm === null` is one wire state standing in for three real reasons (not
 * reached yet, degraded, or a response that never parsed) — the schema's own
 * doc comment says the UI cannot and need not tell them apart
 * (`SceneResponse.vlm` in `engine/repcut/api/schemas.py`), so this renders
 * exactly one state for all three rather than inventing a distinction the API
 * does not give it.
 */
function SceneCard({ sha256, scene }: { readonly sha256: string; readonly scene: Scene }) {
  const timing = `${formatDuration(scene.start_seconds)}–${formatDuration(scene.end_seconds)}`;
  const details = scene.vlm === null
    ? []
    : DETAIL_FIELDS.filter(([key]) => scene.vlm !== null && scene.vlm[key] !== null);

  return (
    <div
      role="group"
      aria-label={`Scene ${scene.sequence_index + 1}, ${timing}`}
      className="flex w-48 shrink-0 flex-col gap-2 rounded-xl border border-line bg-panel p-3"
    >
      <div className="flex h-28 items-center justify-center overflow-hidden rounded-lg bg-raised">
        {scene.has_sampled_frame ? (
          // A sampled frame is footage-derived, streamed straight from the
          // engine with Range support — same reasoning as `MediaCard`'s
          // thumbnail strip: no optimiser hop for a local, already-sized
          // JPEG, and no extra on-disk cache of the user's own footage (P4).
          // eslint-disable-next-line @next/next/no-img-element -- see above
          <img
            src={sceneFrameUrl(sha256, scene.id)}
            alt=""
            aria-hidden="true"
            className="h-full w-full object-cover"
          />
        ) : (
          <p className="px-2 text-center text-xs text-fg-muted">
            No sampled frame yet
          </p>
        )}
      </div>

      <span className="font-mono text-xs text-fg-muted">
        Scene {scene.sequence_index + 1} · {timing}
      </span>

      {scene.vlm === null ? (
        <Badge tone="neutral">Not yet analyzed</Badge>
      ) : (
        <div className="flex flex-col gap-2">
          <div className="flex flex-wrap gap-1">
            {scene.vlm.content_type !== null && (
              <Badge label="content type">{scene.vlm.content_type}</Badge>
            )}
            {scene.vlm.exercise_guess !== null && (
              <Badge label="exercise guess">{scene.vlm.exercise_guess}</Badge>
            )}
            {scene.vlm.environment !== null && (
              <Badge label="environment">{scene.vlm.environment}</Badge>
            )}
          </div>

          {details.length > 0 && (
            <details className="text-xs text-fg-muted">
              <summary className="cursor-pointer text-fg-secondary">
                More detail
              </summary>
              <dl className="mt-2 flex flex-col gap-1">
                {details.map(([key, label]) => (
                  <div key={key} className="flex flex-col">
                    <dt className="text-fg-muted">{label}</dt>
                    <dd className="text-fg-secondary">{scene.vlm?.[key]}</dd>
                  </div>
                ))}
              </dl>
            </details>
          )}
        </div>
      )}
    </div>
  );
}

/**
 * One card per detected scene, in `sequence_index` order.
 *
 * A horizontally scrolling list rather than a grid: scenes are inherently
 * ordered (they are cut points along the clip), and a row reads that order
 * the way a grid would not. Sorted defensively even though the engine already
 * orders by `sequence_index` — this renders whatever it is given, and a
 * future caller merging two sources should not have to remember to re-sort.
 */
export function SceneStrip({ sha256, scenes }: SceneStripProps) {
  const ordered = [...scenes].sort((a, b) => a.sequence_index - b.sequence_index);

  return (
    <ul aria-label="Detected scenes" className="flex gap-3 overflow-x-auto pb-1">
      {ordered.map((scene) => (
        <li key={scene.id} className="contents">
          <SceneCard sha256={sha256} scene={scene} />
        </li>
      ))}
    </ul>
  );
}
