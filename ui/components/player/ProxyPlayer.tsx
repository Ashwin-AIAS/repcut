"use client";

import { useCallback, useRef, useState } from "react";
import { proxyUrl } from "@/lib/api/client";
import type { MediaFile } from "@/lib/api/schemas";
import { formatTimecode } from "@/lib/format";

export interface ProxyPlayerProps {
  readonly clip: MediaFile | null;
}

/** Fallback step when the engine could not determine a rate. */
const ASSUMED_FPS = 30;

/**
 * The preview player.
 *
 * Plays the **proxy**, never the original: proxies exist so scrubbing is smooth
 * on a laptop, and every real render reads the source bytes so quality loss
 * cannot compound.
 *
 * Seeking depends on the engine answering Range requests — see
 * `engine/repcut/api/media.py`. Without `206 Partial Content` the browser
 * refetches from byte 0 on every frame step, which on a real clip is the
 * difference between instant and unusable.
 *
 * Keyboard, per the design system's accessibility baseline:
 *
 * | Key | Action |
 * |---|---|
 * | Space / K | play or pause |
 * | ← / → | one frame back or forward |
 * | Shift + ← / → | one second |
 * | Home / End | start or end |
 *
 * The handler lives on a focusable wrapper rather than on `window`: a global
 * listener would swallow Space and the arrow keys while the user was typing a
 * project name three components away.
 */
export function ProxyPlayer({ clip }: ProxyPlayerProps) {
  if (clip === null) {
    return (
      <div className="flex h-full items-center justify-center rounded-xl border border-line bg-panel p-6">
        <p className="text-sm text-fg-muted">Select a clip to preview it.</p>
      </div>
    );
  }

  if (!clip.has_proxy) {
    return (
      <div className="flex h-full flex-col items-center justify-center gap-2 rounded-xl border border-line bg-panel p-6">
        <p className="text-sm text-fg-secondary">No preview yet.</p>
        <p className="text-xs text-fg-muted">
          The preview is generated after upload. If this clip failed to ingest,
          use Re-ingest in the library.
        </p>
      </div>
    );
  }

  // Keyed, so selecting another clip mounts a fresh player rather than reusing
  // one holding the previous clip's position and play state. Resetting that in
  // an effect instead would render one frame of the old timecode against the
  // new clip, and cost a second render to correct it.
  return <LoadedPlayer key={clip.id} clip={clip} />;
}

function LoadedPlayer({ clip }: { readonly clip: MediaFile }) {
  const videoRef = useRef<HTMLVideoElement>(null);
  const [currentTime, setCurrentTime] = useState(0);
  const [playing, setPlaying] = useState(false);

  const fps = clip.fps_normalized ?? clip.fps_source ?? ASSUMED_FPS;
  const frame = 1 / (fps > 0 ? fps : ASSUMED_FPS);

  const seekBy = useCallback((delta: number) => {
    const video = videoRef.current;
    if (video === null) return;
    const duration = Number.isFinite(video.duration) ? video.duration : 0;
    const next = Math.min(Math.max(0, video.currentTime + delta), duration);
    video.currentTime = next;
    setCurrentTime(next);
  }, []);

  const togglePlay = useCallback(() => {
    const video = videoRef.current;
    if (video === null) return;
    if (video.paused) {
      // `play()` rejects if the element is detached or the source is bad; the
      // catch keeps that from surfacing as an unhandled rejection.
      void video.play().catch(() => setPlaying(false));
    } else {
      video.pause();
    }
  }, []);

  const onKeyDown = useCallback(
    (event: React.KeyboardEvent<HTMLDivElement>) => {
      if (videoRef.current === null) return;

      switch (event.key) {
        case " ":
        case "k":
          event.preventDefault(); // Space would otherwise scroll the page.
          togglePlay();
          break;
        case "ArrowLeft":
          event.preventDefault();
          seekBy(event.shiftKey ? -1 : -frame);
          break;
        case "ArrowRight":
          event.preventDefault();
          seekBy(event.shiftKey ? 1 : frame);
          break;
        case "Home":
          event.preventDefault();
          seekBy(-Number.MAX_SAFE_INTEGER);
          break;
        case "End":
          event.preventDefault();
          seekBy(Number.MAX_SAFE_INTEGER);
          break;
        default:
          break;
      }
    },
    [frame, seekBy, togglePlay],
  );

  return (
    <div
      role="group"
      aria-label={`Preview: ${clip.display_name}`}
      tabIndex={0}
      onKeyDown={onKeyDown}
      className="flex h-full flex-col gap-2 rounded-xl border border-line bg-panel p-3"
    >
      <div className="flex min-h-0 flex-1 items-center justify-center overflow-hidden rounded-lg bg-surface">
        {/* No caption track is attached, deliberately: these are the user's own
            unprocessed gym clips, and Prompt 06 is what generates captions. A
            placeholder <track> would claim an accessibility feature that does
            not exist. */}
        <video
          ref={videoRef}
          src={proxyUrl(clip.id)}
          preload="metadata"
          className="max-h-full max-w-full"
          onTimeUpdate={(event) => setCurrentTime(event.currentTarget.currentTime)}
          onPlay={() => setPlaying(true)}
          onPause={() => setPlaying(false)}
        />
      </div>

      <div className="flex shrink-0 items-center justify-between gap-3">
        <span className="font-mono text-xs text-fg-secondary">
          {formatTimecode(currentTime, fps)}
        </span>
        <span className="text-xs text-fg-muted">
          {playing ? "Playing" : "Paused"} · Space to play, ← → to step a frame
        </span>
      </div>
    </div>
  );
}
