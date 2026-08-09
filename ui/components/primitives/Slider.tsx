"use client";

import { useId } from "react";
import type { ChangeEvent } from "react";
import { cx } from "@/lib/cx";

export interface SliderProps {
  readonly label: string;
  readonly value: number;
  readonly min: number;
  readonly max: number;
  readonly step?: number;
  readonly onChange: (value: number) => void;
  /** Rendered beside the label — a formatted value, a unit, a timecode. */
  readonly display?: string;
  readonly disabled?: boolean;
  /** Draws the track in the accent hue. See `AiSuggested`; do not set by hand. */
  readonly suggested?: boolean;
}

/**
 * A range input, styled through tokens.
 *
 * Built on `<input type="range">` rather than on a div with drag handlers,
 * which is the usual shape of this component and the usual place accessibility
 * is lost. The platform control already gives us: arrow-key stepping, Home/End,
 * Page Up/Down, the correct ARIA role and value announcements, touch handling,
 * and a focus ring that follows the OS setting. Reimplementing that list
 * correctly is a week; reimplementing it incorrectly is an afternoon, and the
 * afternoon version is what usually ships.
 *
 * Only the visual furniture is ours: the track and the thumb, both via
 * `accent-*`-free token classes in `globals.css`.
 */
export function Slider({
  label,
  value,
  min,
  max,
  step = 1,
  onChange,
  display,
  disabled = false,
  suggested = false,
}: SliderProps) {
  const id = useId();

  return (
    <div className="flex flex-col gap-2">
      <div className="flex items-baseline justify-between gap-3">
        {/*
          `htmlFor` rather than wrapping the input in the label. A wrapping
          label folds *all* its text into the accessible name, so the control
          would announce as "Speed 1.5x" — the value twice, since the range role
          already reports it, and stale the moment it changes.
        */}
        <label htmlFor={id} className="text-sm text-fg-secondary">
          {label}
        </label>
        {display !== undefined && (
          <span
            // The slider announces its own value; this is the same number for
            // eyes only, and repeating it to a screen reader is noise.
            aria-hidden="true"
            className={cx(
              "font-mono text-xs",
              suggested ? "text-accent" : "text-fg-muted",
            )}
          >
            {display}
          </span>
        )}
      </div>
      <input
        id={id}
        type="range"
        value={value}
        min={min}
        max={max}
        step={step}
        disabled={disabled}
        onChange={(event: ChangeEvent<HTMLInputElement>) =>
          onChange(Number(event.target.value))
        }
        className={cx(
          "repcut-slider w-full",
          suggested && "repcut-slider--suggested",
          disabled && "cursor-not-allowed opacity-50",
        )}
      />
    </div>
  );
}
