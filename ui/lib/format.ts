/**
 * Formatting for the values the media library renders.
 *
 * Pure functions, unit-tested — the library grid is where a wrong unit or a
 * silently-dropped `null` becomes a metadata claim the user believes.
 */

/** `null` renders as an em dash everywhere, never as `0` or an empty cell. */
export const UNKNOWN = "—";

/**
 * Seconds to `m:ss` or `h:mm:ss`.
 *
 * Not `Intl.NumberFormat`-based: durations here are timeline positions, and a
 * locale that groups digits or uses a different separator would make two
 * adjacent timecodes stop lining up.
 */
export function formatDuration(seconds: number | null): string {
  if (seconds === null || !Number.isFinite(seconds) || seconds < 0) return UNKNOWN;

  const whole = Math.floor(seconds);
  const hours = Math.floor(whole / 3600);
  const minutes = Math.floor((whole % 3600) / 60);
  const secs = whole % 60;
  const pad = (n: number): string => n.toString().padStart(2, "0");

  return hours > 0
    ? `${hours}:${pad(minutes)}:${pad(secs)}`
    : `${minutes}:${pad(secs)}`;
}

/**
 * Seconds to a frame-accurate timecode, `m:ss.ff`.
 *
 * The player shows this rather than a bare second count: at 30fps a whole
 * second is thirty frames of ambiguity, which is useless for judging a cut.
 */
export function formatTimecode(seconds: number, fps: number | null): string {
  if (!Number.isFinite(seconds) || seconds < 0) return UNKNOWN;
  const base = formatDuration(seconds);
  if (fps === null || !Number.isFinite(fps) || fps <= 0) return base;

  // Clamped: floating-point seconds can land exactly on the next second's
  // boundary and produce a frame number equal to the rate, e.g. "0:01.30".
  const frame = Math.min(Math.floor((seconds % 1) * fps), Math.ceil(fps) - 1);
  return `${base}.${frame.toString().padStart(2, "0")}`;
}

/** Binary units, because that is what a file manager will report for the same file. */
export function formatBytes(bytes: number | null): string {
  if (bytes === null || !Number.isFinite(bytes) || bytes < 0) return UNKNOWN;
  if (bytes < 1024) return `${bytes} B`;

  const units = ["KB", "MB", "GB", "TB"] as const;
  let value = bytes / 1024;
  let unit = 0;
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024;
    unit += 1;
  }
  return `${value.toFixed(value >= 100 ? 0 : 1)} ${units[unit]}`;
}

/**
 * Display resolution, which is not always the coded resolution.
 *
 * A portrait phone clip is stored as landscape pixels plus a rotation tag; the
 * engine already resolves that into `display_width`/`display_height`, and this
 * renders what it decided rather than re-deriving it.
 */
export function formatResolution(
  width: number | null,
  height: number | null,
): string {
  if (width === null || height === null) return UNKNOWN;
  return `${width}x${height}`;
}

export function formatFps(fps: number | null): string {
  if (fps === null || !Number.isFinite(fps) || fps <= 0) return UNKNOWN;
  // 29.97 must not round to 30: the difference is the whole drift problem.
  return Number.isInteger(fps) ? `${fps}` : fps.toFixed(2);
}

export type FrameRateMode = "variable" | "constant" | "unknown";

/**
 * The three-valued frame-rate answer, kept three-valued.
 *
 * `null` is **not** "constant". The `r_frame_rate != avg_frame_rate` test is
 * container-dependent — the same clip muxed to Matroska reports constant while
 * MP4 reports variable — so `null` means the container could not answer.
 * Collapsing it into "constant" reintroduces exactly the silent-desync bug the
 * column exists to catch, which is why this returns a third value instead of a
 * boolean.
 */
export function frameRateMode(isVariable: boolean | null): FrameRateMode {
  if (isVariable === null) return "unknown";
  return isVariable ? "variable" : "constant";
}
