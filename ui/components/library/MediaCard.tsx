"use client";

import { thumbnailStripUrl } from "@/lib/api/client";
import type { MediaFile } from "@/lib/api/schemas";
import {
  formatBytes,
  formatDuration,
  formatFps,
  formatResolution,
  frameRateMode,
} from "@/lib/format";
import { Badge } from "@/components/primitives/Badge";
import { Skeleton } from "@/components/primitives/Skeleton";
import { cx } from "@/lib/cx";

export interface MediaCardProps {
  readonly clip: MediaFile;
  readonly selected: boolean;
  readonly onSelect: (clip: MediaFile) => void;
}

/**
 * The frame-rate badge, which is the one place a `null` must not become a "no".
 *
 * Three answers, three renderings. "Unknown" is a real answer: the
 * `r_frame_rate != avg_frame_rate` heuristic is container-dependent, and
 * Matroska reports a genuinely variable clip as constant. Showing "constant"
 * for a clip the engine could not measure would be the UI asserting something
 * nothing checked — and beat sync would then drift with no warning anywhere.
 */
function FrameRateBadge({ clip }: { readonly clip: MediaFile }) {
  const mode = frameRateMode(clip.is_variable_frame_rate);

  if (mode === "variable") {
    return (
      <Badge tone="warning" label="variable frame rate">
        VFR
      </Badge>
    );
  }
  if (mode === "constant") {
    return (
      <Badge tone="neutral" label="constant frame rate">
        CFR
      </Badge>
    );
  }
  return (
    <Badge tone="neutral" label="frame rate could not be determined">
      VFR?
    </Badge>
  );
}

export function MediaCard({ clip, selected, onSelect }: MediaCardProps) {
  const label = `${clip.display_name}, ${formatDuration(clip.duration_seconds)}`;

  return (
    <button
      type="button"
      aria-pressed={selected}
      aria-label={label}
      onClick={() => onSelect(clip)}
      className={cx(
        "flex flex-col gap-2 rounded-xl border bg-panel p-3 text-left",
        "transition-colors duration-micro ease-micro",
        selected
          ? "border-line-strong"
          : "border-line hover:border-line-strong",
      )}
    >
      <div className="overflow-hidden rounded-lg bg-raised">
        {clip.has_thumbnail_strip ? (
          // The strip is one tiled JPEG of the whole clip; the card shows its
          // first cell by cropping, so the grid makes one request per clip
          // rather than one per thumbnail.
          //
          // `next/image` would send every thumbnail through Next's optimiser: a
          // second local process, a `remotePatterns` entry for localhost, and a
          // re-encode, to resize a 180px-tall JPEG the engine already rendered
          // at the size it is displayed. It also puts footage-derived bytes
          // through an extra hop and an on-disk cache for no benefit (P4).
          // eslint-disable-next-line @next/next/no-img-element -- see above
          <img
            src={thumbnailStripUrl(clip.id)}
            alt=""
            aria-hidden="true"
            className="h-20 w-full object-cover object-left"
          />
        ) : (
          <Skeleton className="h-20 w-full" label="Generating thumbnail" />
        )}
      </div>

      <div className="flex flex-col gap-1">
        <span className="truncate text-sm text-fg-primary" title={clip.display_name}>
          {clip.display_name}
        </span>
        <span className="font-mono text-xs text-fg-muted">
          {formatDuration(clip.duration_seconds)} ·{" "}
          {formatResolution(clip.display_width, clip.display_height)} ·{" "}
          {formatFps(clip.fps_normalized ?? clip.fps_source)} fps
        </span>
      </div>

      <div className="flex flex-wrap gap-1">
        <FrameRateBadge clip={clip} />
        {clip.video_codec !== null && (
          <Badge label="video codec">{clip.video_codec}</Badge>
        )}
        <Badge label="file size">{formatBytes(clip.size_bytes)}</Badge>
        {!clip.has_proxy && (
          <Badge tone="warning" label="preview status">
            no preview
          </Badge>
        )}
      </div>
    </button>
  );
}
