import type { Scene } from "@/lib/api/schemas";
import { formatDuration } from "@/lib/format";

export interface EnergySparklineProps {
  readonly scenes: readonly Scene[];
}

const WIDTH = 240;
const HEIGHT = 40;
/** Keeps a flat-zero curve visible as a line rather than collapsing onto the axis. */
const MIN_RANGE = 0.0001;

/**
 * `energy_score` across a clip's scenes, in order.
 *
 * Inline SVG rather than a chart dependency — this is one small line, and
 * nothing charting-shaped exists elsewhere in the codebase to reuse
 * (`.claude/rules/frontend-and-licensing.md`: Tailwind core utilities only,
 * no component libraries). Colour comes from the same `fill-*`/`stroke-*`
 * utilities every other component uses, never a literal hex value.
 *
 * A scene with no score yet (analysis has not reached it) breaks the line
 * rather than plotting it at zero — zero energy and "not measured" are
 * different facts, and drawing the second as the first would be a claim
 * nothing checked.
 */
export function EnergySparkline({ scenes }: EnergySparklineProps) {
  const ordered = [...scenes].sort((a, b) => a.sequence_index - b.sequence_index);

  if (ordered.length === 0) {
    return <p className="text-xs text-fg-muted">No scenes to chart yet.</p>;
  }

  const known = ordered.filter((scene) => scene.energy_score !== null);
  if (known.length === 0) {
    return <p className="text-xs text-fg-muted">Energy has not been scored yet.</p>;
  }

  const max = Math.max(
    MIN_RANGE,
    ...known.map((scene) => scene.energy_score as number),
  );
  const step = ordered.length > 1 ? WIDTH / (ordered.length - 1) : 0;

  const points = ordered.map((scene, index) => ({
    id: scene.id,
    x: index * step,
    y: scene.energy_score === null ? null : HEIGHT - (scene.energy_score / max) * HEIGHT,
  }));

  // Split into runs of consecutive known points, so a gap for an
  // unanalyzed scene breaks the line instead of interpolating across it.
  const segments: string[] = [];
  let current: string[] = [];
  for (const point of points) {
    if (point.y === null) {
      if (current.length > 0) segments.push(current.join(" "));
      current = [];
      continue;
    }
    current.push(`${current.length === 0 ? "M" : "L"} ${point.x.toFixed(1)} ${point.y.toFixed(1)}`);
  }
  if (current.length > 0) segments.push(current.join(" "));

  const description = `Energy across ${ordered.length} scene${ordered.length === 1 ? "" : "s"}, ${formatDuration(ordered[0]?.start_seconds ?? 0)} to ${formatDuration(ordered[ordered.length - 1]?.end_seconds ?? 0)}`;

  return (
    <figure className="flex flex-col gap-1">
      <svg
        role="img"
        aria-label={description}
        viewBox={`0 0 ${WIDTH} ${HEIGHT}`}
        preserveAspectRatio="none"
        className="h-10 w-full"
      >
        {segments.map((d, index) => (
          // Index as key: a segment is a run of points with no identity of its
          // own beyond its position among the other runs, and the set is
          // rebuilt from `scenes` on every render rather than reordered.
          <path key={index} d={d} className="fill-none stroke-fg-secondary" strokeWidth={2} />
        ))}
        {points.map((point) =>
          point.y === null ? null : (
            <circle key={point.id} cx={point.x} cy={point.y} r={2.5} className="fill-fg-primary" />
          ),
        )}
      </svg>
      <figcaption className="text-xs text-fg-muted">{description}</figcaption>
    </figure>
  );
}
