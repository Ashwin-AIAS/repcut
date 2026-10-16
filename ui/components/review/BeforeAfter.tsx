"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import type { PointerEvent as ReactPointerEvent } from "react";
import { Badge } from "@/components/primitives/Badge";
import { Button } from "@/components/primitives/Button";
import { Panel } from "@/components/primitives/Panel";
import { Slider } from "@/components/primitives/Slider";
import { getComparison, startGradedPreview } from "@/lib/api/client";
import { ENGINE_ORIGIN } from "@/lib/api/engine";
import type { Comparison, JobEvent } from "@/lib/api/schemas";
import { frameAt, frameOf, sideSeconds } from "@/lib/compare";

export interface BeforeAfterProps {
  readonly mediaFileId: string;
  /** A scene key; the clip's first scene when absent. */
  readonly sceneKey?: string;
  /** The project's jobs: the panel refetches when a graded preview finishes. */
  readonly jobs: readonly JobEvent[];
}

/**
 * Before/after on one scene: the original proxy on the left of the divider,
 * the fully processed scene (grade, correction) on the right.
 *
 * Both sides are files that already exist, so dragging never waits on a
 * render. Every seek goes through `sideSeconds`, the same mapping the engine's
 * gate checks frame by frame: the two sides always show the same instant.
 * Captions are burned in at export; this view says whether they will be.
 */
export function BeforeAfter({ mediaFileId, sceneKey, jobs }: BeforeAfterProps) {
  const [spec, setSpec] = useState<Comparison | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [split, setSplit] = useState(50);
  const [frame, setFrame] = useState(0);
  const [playing, setPlaying] = useState(false);
  const before = useRef<HTMLVideoElement>(null);
  const after = useRef<HTMLVideoElement>(null);
  const stage = useRef<HTMLDivElement>(null);

  const finished = jobs
    .filter((job) => job.job_type === "grade_preview" && job.status === "succeeded")
    .map((job) => job.job_id)
    .sort()
    .join(",");

  useEffect(() => {
    let stale = false;
    void getComparison(mediaFileId, sceneKey).then((result) => {
      if (stale) return;
      if (result.ok) {
        setSpec(result.data);
        setError(null);
      } else {
        setError(result.message);
      }
    });
    return () => {
      stale = true;
    };
  }, [mediaFileId, sceneKey, finished]);

  const seek = useCallback(
    (next: number) => {
      if (spec === null) return;
      setFrame(next);
      if (after.current !== null) {
        after.current.currentTime = sideSeconds("after", next, spec.offset_frames, spec.fps);
      }
      if (before.current !== null) {
        before.current.currentTime = sideSeconds("before", next, spec.offset_frames, spec.fps);
      }
    },
    [spec],
  );

  const onDrag = (event: ReactPointerEvent<HTMLDivElement>) => {
    if (event.buttons !== 1 || stage.current === null) return;
    const box = stage.current.getBoundingClientRect();
    if (box.width <= 0) return;
    setSplit(Math.round(Math.min(100, Math.max(0, ((event.clientX - box.left) / box.width) * 100))));
  };

  const toggle = () => {
    const video = after.current;
    if (video === null || spec === null) return;
    if (playing) {
      video.pause();
      before.current?.pause();
      seek(frameOf(video.currentTime, spec.fps, spec.frame_count));
      setPlaying(false);
    } else {
      void video.play().catch(() => undefined);
      void before.current?.play().catch(() => undefined);
      setPlaying(true);
    }
  };

  // While playing, the original follows the processed side frame by frame.
  const follow = () => {
    if (!playing || spec === null || after.current === null || before.current === null) return;
    const current = frameOf(after.current.currentTime, spec.fps, spec.frame_count);
    const wanted = sideSeconds("before", current, spec.offset_frames, spec.fps);
    if (Math.abs(before.current.currentTime - wanted) > 0.5 / spec.fps) {
      before.current.currentTime = wanted;
    }
    setFrame(current);
  };

  return (
    <Panel title="Before / after">
      <div className="flex flex-col gap-3 p-3">
        {error !== null && <p className="text-sm text-fg-muted">{error}</p>}
        {spec !== null && !spec.preview_ready && (
          <div className="flex items-center justify-between gap-3">
            <p className="text-sm text-fg-secondary">
              The processed side needs this scene&apos;s graded preview, rendered once on this
              machine.
            </p>
            <Button size="sm" onClick={() => void startGradedPreview(mediaFileId)}>
              Render preview
            </Button>
          </div>
        )}
        {spec !== null && spec.preview_ready && (
          <>
            <div
              ref={stage}
              data-testid="before-after-stage"
              className="relative aspect-video w-full cursor-ew-resize overflow-hidden rounded border border-line bg-panel"
              onPointerDown={onDrag}
              onPointerMove={onDrag}
            >
              <video
                ref={after}
                aria-label="After: graded and corrected"
                className="absolute inset-0 h-full w-full object-contain"
                src={`${ENGINE_ORIGIN}${spec.after_url}`}
                muted
                playsInline
                preload="auto"
                onTimeUpdate={follow}
                onLoadedMetadata={() => seek(frame)}
              />
              <video
                ref={before}
                aria-label="Before: the original footage"
                className="absolute inset-0 h-full w-full object-contain"
                style={{ clipPath: `inset(0 ${100 - split}% 0 0)` }}
                src={`${ENGINE_ORIGIN}${spec.before_url}`}
                muted
                playsInline
                preload="auto"
                onLoadedMetadata={() => seek(frame)}
              />
              <span
                aria-hidden="true"
                className="absolute inset-y-0 w-px bg-fg-primary"
                style={{ left: `${split}%` }}
              />
              <span className="absolute left-2 top-2 text-xs text-fg-primary">Before</span>
              <span className="absolute right-2 top-2 text-xs text-fg-primary">After</span>
            </div>
            <Slider label="Divider" value={split} min={0} max={100} onChange={setSplit} display={`${split}%`} />
            <Slider
              label="Position"
              value={frame}
              min={0}
              max={spec.frame_count - 1}
              onChange={(value) => seek(frameAt(value / Math.max(1, spec.frame_count - 1), spec.frame_count))}
              display={`frame ${frame + 1} of ${spec.frame_count}`}
            />
            <div className="flex items-center justify-between gap-3">
              <Button size="sm" onClick={toggle}>
                {playing ? "Pause" : "Play"}
              </Button>
              <span className="flex items-center gap-2 text-xs text-fg-secondary">
                {spec.captions_on ? (
                  <Badge tone="ai">Captions on</Badge>
                ) : (
                  <Badge>Captions off</Badge>
                )}
                {spec.captions_on
                  ? `burned in at export (${spec.captions_style}), not drawn here`
                  : "the export will have no captions"}
              </span>
            </div>
          </>
        )}
      </div>
    </Panel>
  );
}
