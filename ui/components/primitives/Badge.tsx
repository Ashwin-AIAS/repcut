import type { ReactNode } from "react";
import { cx } from "@/lib/cx";

export type BadgeTone = "neutral" | "positive" | "warning" | "danger" | "ai";

export interface BadgeProps {
  readonly tone?: BadgeTone;
  readonly children: ReactNode;
  /**
   * Spoken prefix for assistive tech, when the visible text is an abbreviation.
   * `VFR` reads as three letters; "variable frame rate" is the actual meaning.
   */
  readonly label?: string;
}

/*
  Every tone pairs a colour with text the caller supplies — never colour alone.
  A red pill and a green pill are the same pill to a red-green colour-blind
  user, which is roughly one man in twelve and therefore not an edge case in a
  gym-footage tool.

  `ai` is the accent, and it appears here and in `AiSuggested` only.
*/
const TONES: Record<BadgeTone, string> = {
  neutral: "border-line bg-raised text-fg-secondary",
  positive: "border-line bg-raised text-positive",
  warning: "border-line bg-raised text-warning",
  danger: "border-line bg-raised text-danger",
  ai: "border-line bg-accent-surface text-accent",
};

export function Badge({ tone = "neutral", children, label }: BadgeProps) {
  return (
    <span
      className={cx(
        "inline-flex items-center gap-1 rounded-lg border px-2 py-1",
        "font-mono text-xs",
        TONES[tone],
      )}
    >
      {label !== undefined && <span className="sr-only">{label}: </span>}
      {children}
    </span>
  );
}
