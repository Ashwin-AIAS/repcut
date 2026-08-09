import { cx } from "@/lib/cx";

export interface ProgressProps {
  /** 0 to 1. Clamped, because a job reporting 1.02 is a bar overflowing its track. */
  readonly value: number;
  /** What the bar is measuring — required, because a nameless meter says nothing. */
  readonly label: string;
  /** The step currently running, shown beside the percentage. */
  readonly step?: string;
  readonly tone?: "running" | "succeeded" | "failed";
}

const TONES = {
  running: "bg-fg-secondary",
  succeeded: "bg-positive",
  failed: "bg-danger",
} as const;

/**
 * A determinate progress bar.
 *
 * `role="progressbar"` with the three `aria-value*` attributes is what makes it
 * announce as progress rather than as a decorated div. The percentage is also
 * rendered as text: `.claude/rules/frontend-and-licensing.md` forbids a spinner
 * with no information, and a bar with no number is the same failure with a
 * nicer shape.
 */
export function Progress({
  value,
  label,
  step,
  tone = "running",
}: ProgressProps) {
  const clamped = Math.min(1, Math.max(0, Number.isFinite(value) ? value : 0));
  const percent = Math.round(clamped * 100);

  return (
    <div className="flex flex-col gap-2">
      <div className="flex items-baseline justify-between gap-3 text-xs">
        <span className="truncate text-fg-secondary">{step ?? label}</span>
        <span className="shrink-0 font-mono text-fg-muted">{percent}%</span>
      </div>
      <div
        role="progressbar"
        aria-label={label}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={percent}
        className="h-1 w-full overflow-hidden rounded-lg bg-raised"
      >
        <div
          className={cx(
            "h-full transition-[width] duration-layout ease-layout",
            TONES[tone],
          )}
          style={{ width: `${percent}%` }}
        />
      </div>
    </div>
  );
}
