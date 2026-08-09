"use client";

import type { ReactNode } from "react";
import { cx } from "@/lib/cx";

export interface AiSuggestedProps {
  /** What this value is. Used as the group's accessible name. */
  readonly label: string;
  /** True while the value still equals what the engine proposed. */
  readonly isSuggestion: boolean;
  /** Restores the suggested value. Rendered only once overridden. */
  readonly onReset: () => void;
  /**
   * The suggested value, in words, for the reset control's accessible name —
   * "Reset to AI suggestion" alone does not say what you are getting back.
   */
  readonly suggestion: string;
  /** Why the engine proposed this. Shown inline; P4 says don't bury it. */
  readonly rationale?: string;
  readonly children: ReactNode;
}

/**
 * The control wrapper that makes P2 visible.
 *
 * P2 is "AI recommends, user decides", and the part that is easy to get wrong
 * is not the override — it is making the *state* legible. A user has to be able
 * to tell, at a glance and without clicking anything, which values on screen
 * are the engine's opinion and which are their own. That is what the accent hue
 * is reserved for, and why it appears in this component and in `Badge`'s `ai`
 * tone and nowhere else in the UI.
 *
 * Three states, each visually distinct:
 *
 * - **suggested** — accent left border, "AI suggested" chip.
 * - **overridden** — neutral border, "Your choice" chip, reset control present.
 * - the reset itself, which is **always available once overridden**. P2 says
 *   every AI output is a default and never a lock; a one-way override is a lock
 *   with extra steps.
 *
 * Colour is never the only carrier: each state also has its own text, so the
 * distinction survives a colour-blind user and a greyscale screenshot.
 */
export function AiSuggested({
  label,
  isSuggestion,
  onReset,
  suggestion,
  rationale,
  children,
}: AiSuggestedProps) {
  return (
    <div
      role="group"
      aria-label={label}
      className={cx(
        "flex flex-col gap-2 rounded-lg border-l-2 pl-3",
        "transition-colors duration-micro ease-micro",
        isSuggestion ? "border-l-accent" : "border-l-line-strong",
      )}
    >
      <div className="flex items-center justify-between gap-2">
        <span
          className={cx(
            "text-xs font-medium",
            isSuggestion ? "text-accent" : "text-fg-muted",
          )}
        >
          {isSuggestion ? "AI suggested" : "Your choice"}
        </span>

        {!isSuggestion && (
          <button
            type="button"
            onClick={onReset}
            // The visible text is "Reset"; the accessible name says what to.
            aria-label={`Reset ${label} to the AI suggestion, ${suggestion}`}
            className={cx(
              "rounded-lg px-2 py-1 text-xs text-fg-muted",
              "transition-colors duration-micro ease-micro",
              "hover:bg-raised hover:text-fg-primary",
            )}
          >
            Reset
          </button>
        )}
      </div>

      {children}

      {rationale !== undefined && (
        <p className="text-xs text-fg-muted">{rationale}</p>
      )}
    </div>
  );
}
