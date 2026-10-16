/**
 * Before/after frame mapping, mirroring the engine's `review/compare.py`.
 *
 * The graded preview's frame `i` is the proxy's frame `offsetFrames + i`. Both
 * players seek to the middle of a frame, never its edge, so a browser's seek
 * rounding cannot land either side on a neighbour.
 */

export type Side = "before" | "after";

export const FRAME_CENTRE = 0.5;

export function sideSeconds(side: Side, frame: number, offsetFrames: number, fps: number): number {
  const base = side === "after" ? 0 : offsetFrames;
  return Math.round(((base + frame + FRAME_CENTRE) / fps) * 1e6) / 1e6;
}

/** The preview frame a scrub position (0-1 of the scene) lands on. */
export function frameAt(fraction: number, frameCount: number): number {
  const clamped = Math.min(1, Math.max(0, fraction));
  return Math.min(frameCount - 1, Math.floor(clamped * frameCount));
}

/** The frame a playing "after" video is on, from its current time. */
export function frameOf(afterSeconds: number, fps: number, frameCount: number): number {
  return Math.min(frameCount - 1, Math.max(0, Math.floor(afterSeconds * fps + 1e-6)));
}
